//! The only application FFI: process-global SQLite configuration before any connection.
//! Queries, bindings, stepping, rows and transaction handling use safe rusqlite APIs.
use rusqlite::ffi;
use serde::Deserialize;
use std::{collections::BTreeMap, sync::OnceLock};

#[derive(Deserialize)]
pub struct Runtime {
    pub memstatus: bool,
    pub pagecache_slots: i32,
    pub page_bytes: i32,
    pub connection_mutex: bool,
}

#[derive(Deserialize)]
pub struct Config {
    pub version: String,
    pub runtime: Runtime,
    pub pragmas: BTreeMap<String, serde_json::Value>,
}

pub fn config() -> &'static Config {
    static CONFIG: OnceLock<Config> = OnceLock::new();
    CONFIG.get_or_init(|| {
        serde_json::from_str(include_str!("../../db/sqlite-config.json"))
            .expect("invalid shared SQLite config")
    })
}

pub fn initialize() -> Result<(), String> {
    static INITIALIZED: OnceLock<Result<(), String>> = OnceLock::new();
    INITIALIZED
        .get_or_init(|| {
            let runtime = &config().runtime;
            let check = |rc| {
                if rc == ffi::SQLITE_OK {
                    Ok(())
                } else {
                    Err(format!("SQLite initialization failed: {rc}"))
                }
            };
            // SAFETY: OnceLock serializes initialization; every application connection calls
            // this first, and the engine is never shut down/reconfigured. Varargs match
            // SQLite's documented C signatures. The page pool is aligned to 8 bytes and
            // intentionally lives for the process lifetime, as required by SQLite.
            unsafe {
                check(ffi::sqlite3_config(
                    ffi::SQLITE_CONFIG_MEMSTATUS,
                    i32::from(runtime.memstatus),
                ))?;
                let mut header: i32 = 0;
                check(ffi::sqlite3_config(
                    ffi::SQLITE_CONFIG_PCACHE_HDRSZ,
                    &mut header as *mut i32,
                ))?;
                let slot = (runtime.page_bytes + header + 7) & !7;
                if slot <= 0 || runtime.pagecache_slots <= 0 {
                    return Err("invalid page pool size".into());
                }
                let words = slot as usize / 8 * runtime.pagecache_slots as usize;
                let pool = Box::leak(vec![0u64; words].into_boxed_slice());
                check(ffi::sqlite3_config(
                    ffi::SQLITE_CONFIG_PAGECACHE,
                    pool.as_mut_ptr().cast::<std::ffi::c_void>(),
                    slot,
                    runtime.pagecache_slots,
                ))?;
                check(ffi::sqlite3_initialize())?;
            }
            if rusqlite::version() != config().version {
                return Err(format!("wrong SQLite engine: {}", rusqlite::version()));
            }
            Ok(())
        })
        .clone()
}

pub fn snapshot() -> Result<String, String> {
    let conn = crate::open(std::path::Path::new(":memory:"))?;
    let options: Vec<String> = conn
        .prepare("PRAGMA compile_options")
        .map_err(|e| e.to_string())?
        .query_map([], |r| r.get(0))
        .map_err(|e| e.to_string())?
        .collect::<rusqlite::Result<_>>()
        .map_err(|e| e.to_string())?;
    Ok(serde_json::json!({"version": rusqlite::version(), "compile_options": options}).to_string())
}
