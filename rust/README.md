# Rust

Actix Web **4.15.0**, serde_json **1.0.151**, and rusqlite **0.40.2**, built
with Rust **1.98.0**. `/posts` parses JSON, validates nonempty content and the shared
ASCII email rule, commits its SQLite transaction, then returns all five database
columns. `/echo` parses and reserializes the two request fields.

## Measured results

| HTTP workers + one writer | Endpoint | RPS | p50 | Peak RSS | CPU | Startup |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `/posts` | 48.5K | 0.954ms | 11.2 MiB | 143% | 8.10ms |
| 1 | `/echo` | 225.1K | 0.176ms | 11.5 MiB | 94% | 8.10ms |
| 3 | `/posts` | 46.1K | 0.995ms | 12.0 MiB | 165% | 8.76ms |
| 3 | `/echo` | 400.4K | 0.093ms | 12.3 MiB | 223% | 8.76ms |

Measured on the root README's M1 Pro on 2026-09-18. Each configuration has three
fresh processes and databases, 50 connections, 10 seconds of writes followed by
10 seconds of echo, and no HTTP warm-up. RPS, latency and CPU are medians; RAM is
the largest sampled whole-process RSS. Both endpoints use the same worker count
within each run. One worker is the default because it produced the most writes;
three workers produced the most echo throughput in the worker sweep.

The default's write runs ranged from **48.2K–48.5K writes/sec**. The 50K write target was not reached in these runs. The three-worker echo runs ranged from 370.2K to 408.9K RPS.

The follow-up [write-path investigation](write-profile.md) compares Rust against
a fresh Go control, profiles both writers, and tests Axum, direct Hyper, Smol,
blocking HTTP, shared scheduling and wake-up changes. It includes reproducible
`sample`, Instruments and fgprof commands. No replacement was selected.

The subsequent [MAY coroutine evaluation](may-evaluation.md) implements issue #12
with a pinned personal `may_minihttp` fork, Unix sockets, bounded chunked bodies
and graceful shutdown. Its three-run write median was 55.12K versus 54.94K for a
fresh Go control and 55.14K for Actix, with overlapping ranges. The fork remains
available through the opt-in `may` feature; Actix remains the default.
Those figures belong to the newer controlled sweep, not the historical table above.

The stripped release executable is **1.70 MiB (1,784,224 bytes)**; the shared SQLite library adds **1.62 MiB**, for 3.32 MiB combined. Median build times are **44.47s clean release** and **1.25s warm incremental debug rebuild**. Both worker configurations share these build timings.

The previous ntex startup measurement around 39 ms had a concrete cause:
[ntex-server's accept loop sleeps for 25 ms before signalling startup](https://github.com/ntex-rs/ntex/commit/ecf32afab7e977813d788822f7c063c85df35546).
An instrumented launch spent about 1.9 ms inside Rust on runtime setup, SQLite,
the writer thread and binding, then another 26.6 ms in `HttpServer::run`.
Process launch and library loading add to these internal timings. The selected
Actix build has no such delay. Its Tokio/mio runtime uses macOS **kqueue**.

## Build and run

The pinned toolchain is installed by rustup. Python 3 and a C compiler are needed
to prepare SQLite. Run Cargo from this directory so it reads the local toolchain
and SQLite linker configuration:

```sh
python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite
cargo build --release --locked
pkgx sqlite3 -bail ../db/db.sqlite < ../db/migrations/001_init.up.sql
target/release/rust-benchmark
```

Migrate a fresh database. Defaults are `../db/db.sqlite`, `/tmp/benchmark.sock`,
and one HTTP worker. Override with `-db PATH -socket PATH -workers N`; use
`-workers 3` for the measured echo configuration. The server refuses to replace
an existing socket. `task build`, `task start` and `task test` are available.

## SQLite parity and transaction semantics

[The common builder](../db/prepare-sqlite.py) verifies the source archive checksum
and compiles exactly the SQLite engine and flags in
[sqlite-config.json](../db/sqlite-config.json). Cargo links this local shared
library, with its directory recorded as an rpath. Keep `.tools/sqlite/lib`
available when running the executable.

Rust reads all connection settings from the shared JSON file. SQLite is
initialized once with memory accounting disabled and an aligned page pool. The
connection has one owner and uses `NOMUTEX`. The only application FFI configures
SQLite's process-wide runtime; application queries, parameters and row access
use safe rusqlite APIs with ordinary Rust strings. Startup checks the engine
version. The configuration, migration and other languages' queries are unchanged.

Every request executes these same statements as Go, Kotlin and OCaml:

```sql
BEGIN IMMEDIATE;
INSERT OR IGNORE INTO users (email) VALUES (?1);
INSERT INTO posts (content, user_id)
SELECT ?1, id FROM users WHERE email IS ?2
RETURNING id, user_id, content, created_at, updated_at;
COMMIT;
```

Statements are prepared once. Rust reads every returned column and checks the
final `RETURNING` step before committing. Statement or commit errors roll back
the whole transaction. A dedicated SQLite thread receives requests through a
bounded channel, commits each request independently, and immediately sends its
result through Tokio's ordinary oneshot channel. The HTTP handler awaits that
result. There is no batching, reply polling, response grouping, deferred commit
or user cache. AUTOINCREMENT is preserved, including ignored user inserts.
Shutdown drains accepted writes, runs `PRAGMA optimize`, and closes SQLite.

## Framework selection

The final comparison uses echo throughput and startup to distinguish HTTP
frameworks, with the same JSON/SQLite implementation, release profile and worker
counts. The write benchmark then checks the complete stack. Small differences
in SQLite-limited write scores alone did not justify the earlier ntex choice.

| Framework | HTTP workers | Write RPS | Echo RPS | Echo peak RSS | Startup | Release executable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Actix Web 4.15.0 | 1 | 48.2K | 222.1K | 11.4 MiB | 8.12ms | 1.73 MiB |
| Actix Web 4.15.0 | 2 | 47.0K | 380.2K | 11.9 MiB | 9.60ms | 1.73 MiB |
| Actix Web 4.15.0 | 3 | 45.9K | 400.4K | 12.3 MiB | 8.05ms | 1.73 MiB |
| Actix Web 4.15.0 | 4 | 46.2K | 395.9K | 12.7 MiB | 8.69ms | 1.73 MiB |
| xitca-web 0.8.3 | 1 | 40.1K | 218.2K | 10.3 MiB | 7.03ms | 1.58 MiB |
| xitca-web 0.8.3 | 2 | 47.6K | 383.3K | 10.7 MiB | 7.43ms | 1.58 MiB |
| xitca-web 0.8.3 | 3 | 46.6K | 387.1K | 11.0 MiB | 9.97ms | 1.58 MiB |
| xitca-web 0.8.3 | 4 | 46.4K | 376.0K | 11.4 MiB | 7.00ms | 1.58 MiB |
| ntex 3.12.3 | 1 | 48.0K | 181.4K | 19.3 MiB | 36.31ms | 2.22 MiB |
| ntex 3.12.3 | 2 | 46.7K | 281.1K | 21.1 MiB | 39.10ms | 2.22 MiB |
| ntex 3.12.3 | 4 | 45.8K | 254.3K | 21.9 MiB | 40.25ms | 2.22 MiB |

These are medians of two rotating 5-second runs per endpoint; startup uses five
fresh launches. RSS is the largest sample during echo, following writes. Every
framework passed the HTTP correctness checks. All use macOS kqueue, through
Tokio/mio for Actix and xitca and ntex's native polling runtime for ntex.

Actix's three-worker echo results were 396.2K–404.5K, ahead of xitca's
383.2K–390.9K. Four Actix workers were close to three on echo while using more RAM
and CPU, so the echo configuration uses three. A single Actix HTTP worker had
its best write throughput. These results select among the tested implementations
for this workload; they are not a universal framework ranking.

The reply-channel comparison kept normal commit-then-response behavior. Tokio's
oneshot measured 49.1K writes/sec with one HTTP worker versus 48.7K for the
standalone `oneshot` crate; both measured 46.8K with three HTTP workers. The release
executables had the same on-disk size. Tokio reuses the framework's existing
dependency and is selected. Full-length final measurements are reported above.

## JSON and SQLite API selection

Five rotating rounds of 2,000,000 JSON operations per sample compared typed
deserialization plus serialization. The write case serializes the five-field
response; echo serializes the two parsed fields. simd-json includes its required
mutable input copy and reuses parser scratch space.

| JSON library | Version | Echo work (ns/op) | Write JSON work (ns/op) |
| --- | --- | ---: | ---: |
| serde_json | 1.0.151 | 176.47 | 215.07 |
| Sonic | 0.5.10 | 176.07 | 215.12 |
| simd-json | 0.18.1 | 214.99 | 264.95 |

A complete Actix server comparison used the same bytes extractor and response
adapter for serde_json and Sonic, with two rotating 5-second trials:

| JSON library | HTTP workers | Write RPS | Echo RPS | Release executable |
| --- | ---: | ---: | ---: | ---: |
| serde_json | 1 | 48.3K | 224.9K | 1.75 MiB |
| serde_json | 3 | 45.6K | 395.8K | 1.75 MiB |
| Sonic | 1 | 47.8K | 230.5K | 1.83 MiB |
| Sonic | 3 | 46.0K | 407.3K | 1.83 MiB |

Sonic improved echo by about 3%, but write throughput was within the observed run
variation. serde_json's comparison executable was **82,656 bytes smaller**;
it also supports Actix's standard typed `Json` extractor and response API.
The final implementation uses that typed API. Custom body adapters and codec
experiments are absent from the shipped implementation.

Five alternating rounds of 100,000 complete transactions per SQLite API used
fresh on-disk databases, persistent prepared statements and the shared engine.

| SQLite API | Version | Median µs/transaction |
| --- | --- | ---: |
| sqlite | 0.37.0 | 16.503 |
| rusqlite | 0.40.2 | 16.753 |

The 1.5% gap is small; rusqlite provides checked typed access and length-aware
text handling. The `sqlite` crate's `String` reader uses C-string semantics and
truncates embedded NULs, which fails the required response round trip. rusqlite
preserves NULs, Unicode, quotes and newlines without application C-string handling.

Library documentation: [Actix Web](https://docs.rs/actix-web/4.15.0/actix_web/),
[ntex](https://docs.rs/ntex/3.12.3/ntex/),
[xitca-web](https://docs.rs/xitca-web/0.8.3/xitca_web/),
[serde_json](https://docs.rs/serde_json/1.0.151/serde_json/),
[rusqlite](https://docs.rs/rusqlite/0.40.2/rusqlite/).

## Validation

Seven Rust tests check the shared email fixtures, every connection pragma and
compile define, all five committed response columns, text round trips,
case-insensitive user lookup, AUTOINCREMENT, and rollback/recovery after statement
and commit failures. The real HTTP test passes with both one and three workers:
211 verified commits, 50 concurrent clients, malformed JSON/types/UTF-8, large
and chunked bodies, and graceful shutdown. A separate connection observes each
returned row before the test counts its response. Echo remains responsive while
an external SQLite write lock delays a post.

```sh
cargo fmt --check
cargo clippy --release --all-targets -- -D warnings
cargo test --release --locked
python3 test-http.py
python3 test-http.py --workers 3
```

## Reproduce measurements

Build first, then run from the repository root with fresh output directories:

```sh
python3 rust/measure-http.py results/rust-http --workers 1 3
python3 rust/measure-startup.py results/rust-startup-1 --workers 1
python3 rust/measure-startup.py results/rust-startup-3 --workers 3
python3 rust/measure-build.py results/rust-build
python3 measure-debug-build.py results/rust-debug-build --language rust
```

The HTTP runner uses oha 1.16.0 and the protocol of PR #4. Whole-process RSS is
sampled approximately every 100 ms; CPU sums every thread, with 100% equal to one
core. Echo retains allocations from writes. Each database is checked for
integrity, foreign keys, stored content, AUTOINCREMENT and committed row counts.
oha cancels requests at its deadline, so up to 50 commits can lack a received
response. All completed requests must have the expected HTTP status.

Startup is the median of five launches through the post-bind listening log,
including SQLite initialization, library loading and HTTP worker construction.
Migration/builds are outside the interval. An untimed echo verifies readiness
afterward. Filesystem caches are not flushed; this measures new processes rather
than guaranteed cold-cache launches.

Clean release builds use three fresh Cargo target directories and recompile SQLite C,
mimalloc C, all Rust dependencies and the application, including native CPU
optimization, fat LTO, `panic=abort`, linking and stripping. Rust's standard
library is prebuilt. The release runner's additional log-string rebuild samples
are not used in the root tables.

Warm debug rebuilds use `cargo build --offline --locked`: Cargo's unoptimized dev
profile, full debug information and incremental compilation, with no release LTO.
After an excluded warm-up and no-change control, three samples rename the public
`NewPost` type in `src/lib.rs` and its consumers in `src/main.rs`. Dependencies and
the optimized shared SQLite engine stay cached; Cargo recompiles the application
and links. Each edit uses a fresh name in a disposable copy, and changed binary
hashes verify each rebuild. Downloads, setup, tests, source edits/copying and output
checks are excluded. Samples and patches from 2026-09-18 are in
`../results/debug-rebuild-2026-09-18/` (gitignored).

The local `measurements.json` report and raw data in
`../results/rust-2026-09-18/actix/` are gitignored. Framework, codec and channel
comparisons, source snapshots and startup diagnostics are in
`../results/rust-actix-recheck/`; the earlier microbenchmarks are in
`../results/rust-selection/`.
