#![deny(unsafe_code)]

use crossbeam_channel::{Sender, bounded};
use regex::Regex;
use rusqlite::{Connection, OpenFlags, Statement};
use serde::{Deserialize, Serialize};
use std::{io, path::Path, sync::OnceLock, thread};
#[cfg(feature = "actix")]
use tokio::sync::oneshot;

#[allow(unsafe_code)]
pub mod sqlite_runtime;

#[global_allocator]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

pub const USER_SQL: &str = "INSERT OR IGNORE INTO users (email) VALUES (?1)";
pub const POST_SQL: &str = "INSERT INTO posts (content, user_id) SELECT ?1, id FROM users WHERE email IS ?2 RETURNING id, user_id, content, created_at, updated_at";
pub const BODY_LIMIT: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct NewPost {
    pub content: String,
    pub email: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Post {
    pub id: i64,
    pub user_id: i64,
    pub content: String,
    pub created_at: i64,
    pub updated_at: i64,
}

fn email_rule() -> &'static Regex {
    static RULE: OnceLock<Regex> = OnceLock::new();
    RULE.get_or_init(|| Regex::new(r"\A[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\z").unwrap())
}

impl NewPost {
    pub fn valid(&self) -> bool {
        !self.content.is_empty() && email_rule().is_match(&self.email)
    }
}

pub struct OwnedConnection(pub Connection);

impl Drop for OwnedConnection {
    fn drop(&mut self) {
        if let Err(error) = self.0.execute_batch("PRAGMA optimize") {
            eprintln!("SQLite shutdown optimize: {error}");
        }
    }
}

pub fn open(path: &Path) -> Result<Connection, String> {
    sqlite_runtime::initialize()?;
    let config = sqlite_runtime::config();
    let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
        | OpenFlags::SQLITE_OPEN_CREATE
        | if config.runtime.connection_mutex {
            OpenFlags::SQLITE_OPEN_FULL_MUTEX
        } else {
            OpenFlags::SQLITE_OPEN_NO_MUTEX
        };
    let conn = Connection::open_with_flags(path, flags).map_err(|e| e.to_string())?;
    conn.pragma_update(None, "page_size", config.runtime.page_bytes)
        .map_err(|e| e.to_string())?;
    for (key, value) in &config.pragmas {
        match value {
            serde_json::Value::String(s) => conn.pragma_update(None, key, s),
            serde_json::Value::Number(n) => {
                conn.pragma_update(None, key, n.as_i64().ok_or("noninteger pragma")?)
            }
            _ => return Err(format!("unsupported pragma value: {key}")),
        }
        .map_err(|e| e.to_string())?;
    }
    conn.execute_batch("PRAGMA optimize=0x10002")
        .map_err(|e| e.to_string())?;
    Ok(conn)
}

pub struct Statements<'a> {
    begin: Statement<'a>,
    user: Statement<'a>,
    post: Statement<'a>,
    commit: Statement<'a>,
}

impl<'a> Statements<'a> {
    fn new(conn: &'a OwnedConnection) -> rusqlite::Result<Self> {
        Ok(Self {
            begin: conn.0.prepare("BEGIN IMMEDIATE")?,
            user: conn.0.prepare(USER_SQL)?,
            post: conn.0.prepare(POST_SQL)?,
            commit: conn.0.prepare("COMMIT")?,
        })
    }

    fn transact(&mut self, input: &NewPost) -> rusqlite::Result<Post> {
        self.begin.execute([])?;
        self.user.execute([&input.email])?;
        let post = {
            let mut rows = self.post.query([&input.content, &input.email])?;
            let row = rows.next()?.ok_or(rusqlite::Error::QueryReturnedNoRows)?;
            let post = Post {
                id: row.get(0)?,
                user_id: row.get(1)?,
                content: row.get(2)?,
                created_at: row.get(3)?,
                updated_at: row.get(4)?,
            };
            // Complete RETURNING before COMMIT, checking the final step for errors.
            if rows.next()?.is_some() {
                return Err(rusqlite::Error::ExecuteReturnedResults);
            }
            post
        };
        self.commit.execute([])?;
        Ok(post)
    }
}

self_cell::self_cell!(
    pub struct Database {
        owner: OwnedConnection,
        #[covariant]
        dependent: Statements,
    }
);

impl Database {
    pub fn open(path: &Path) -> Result<Self, String> {
        Self::from_connection(OwnedConnection(open(path)?))
    }

    pub fn from_connection(conn: OwnedConnection) -> Result<Self, String> {
        Self::try_new(conn, |conn| Statements::new(conn)).map_err(|e| e.to_string())
    }

    pub fn transact(&mut self, input: &NewPost) -> Result<Post, String> {
        self.with_dependent_mut(|conn, statements| {
            let result = statements.transact(input);
            if result.is_err() && !conn.0.is_autocommit() {
                conn.0
                    .execute_batch("ROLLBACK")
                    .map_err(|e| format!("rollback failed: {e}"))?;
            }
            result.map_err(|e| e.to_string())
        })
    }
}

pub struct Job {
    input: NewPost,
    reply: Reply,
}

enum Reply {
    #[cfg(feature = "actix")]
    Async(oneshot::Sender<Result<Post, String>>),
    #[cfg(feature = "may")]
    Coroutine(may::sync::spsc::Sender<Result<Post, String>>),
}

impl Reply {
    fn send(self, result: Result<Post, String>) {
        match self {
            #[cfg(feature = "actix")]
            Self::Async(reply) => {
                let _ = reply.send(result);
            }
            #[cfg(feature = "may")]
            Self::Coroutine(reply) => {
                let _ = reply.send(result);
            }
        }
    }
}

#[derive(Clone)]
pub struct Client {
    sender: Sender<Option<Job>>,
}

impl Client {
    #[cfg(feature = "actix")]
    pub async fn write(&self, input: NewPost) -> Result<Post, String> {
        let (reply, response) = oneshot::channel();
        self.sender
            .try_send(Some(Job {
                input,
                reply: Reply::Async(reply),
            }))
            .map_err(|e| e.to_string())?;
        response.await.map_err(|e| e.to_string())?
    }

    #[cfg(feature = "may")]
    pub fn write_coroutine(&self, input: NewPost) -> Result<Post, String> {
        let (reply, response) = may::sync::spsc::channel();
        self.sender
            .try_send(Some(Job {
                input,
                reply: Reply::Coroutine(reply),
            }))
            .map_err(|e| e.to_string())?;
        response.recv().map_err(|e| e.to_string())?
    }
}

pub struct Writer {
    sender: Sender<Option<Job>>,
    thread: thread::JoinHandle<()>,
}

impl Writer {
    pub fn stop(self) -> io::Result<()> {
        let _ = self.sender.send(None);
        self.thread
            .join()
            .map_err(|_| io::Error::other("SQLite writer panicked"))
    }
}

#[derive(Clone)]
pub struct Options {
    pub database: std::path::PathBuf,
    pub socket: std::path::PathBuf,
    pub workers: usize,
}

impl Options {
    pub fn parse() -> io::Result<Self> {
        let mut options = Self {
            database: "../db/db.sqlite".into(),
            socket: "/tmp/benchmark.sock".into(),
            workers: 1,
        };
        let mut args = std::env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "-db" | "--db" => {
                    options.database = args
                        .next()
                        .ok_or_else(|| io::Error::other("-db needs a path"))?
                        .into()
                }
                "-socket" | "--socket" => {
                    options.socket = args
                        .next()
                        .ok_or_else(|| io::Error::other("-socket needs a path"))?
                        .into()
                }
                "-workers" | "--workers" => {
                    options.workers = args
                        .next()
                        .ok_or_else(|| io::Error::other("-workers needs a count"))?
                        .parse()
                        .map_err(io::Error::other)?
                }
                "-check-config" => {
                    println!("{}", sqlite_runtime::snapshot().map_err(io::Error::other)?);
                    std::process::exit(0);
                }
                _ => return Err(io::Error::other(format!("unknown option: {arg}"))),
            }
        }
        if options.workers == 0 {
            return Err(io::Error::other("-workers must be positive"));
        }
        Ok(options)
    }

    pub fn start(&self) -> io::Result<(Client, Writer)> {
        email_rule();
        let conn = OwnedConnection(open(&self.database).map_err(io::Error::other)?);
        let (sender, receiver) = bounded::<Option<Job>>(1024);
        let (ready_tx, ready_rx) = bounded(1);
        let thread = thread::Builder::new()
            .name("sqlite-writer".into())
            .spawn(move || {
                let mut db = match Database::from_connection(conn) {
                    Ok(db) => {
                        let _ = ready_tx.send(Ok(()));
                        db
                    }
                    Err(error) => {
                        let _ = ready_tx.send(Err(error));
                        return;
                    }
                };
                while let Ok(Some(job)) = receiver.recv() {
                    let result = db.transact(&job.input);
                    // A disconnected HTTP client does not cancel an accepted transaction.
                    job.reply.send(result);
                }
            })?;
        ready_rx
            .recv()
            .map_err(io::Error::other)?
            .map_err(io::Error::other)?;
        Ok((
            Client {
                sender: sender.clone(),
            },
            Writer { sender, thread },
        ))
    }

    pub fn listening(&self) {
        println!(
            "Listening on {} (SQLite {}, {} HTTP workers + 1 writer)",
            self.socket.display(),
            rusqlite::version(),
            self.workers
        );
    }
}
