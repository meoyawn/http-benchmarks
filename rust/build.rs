fn main() {
    let root = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    println!("cargo:rustc-link-arg=-Wl,-rpath,{root}/.tools/sqlite/lib");
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=../db/sqlite-config.json");
}
