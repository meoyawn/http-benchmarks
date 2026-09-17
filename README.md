# HTTP server benchmarks

- HTTP over Unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro **10 cores (8 performance + 2 efficiency)**, 16 GiB RAM, macOS 26.4.
Go, Kotlin Panama and OCaml were remeasured on **2026-09-17**; Rust was remeasured
on **2026-09-18**. Each has three runs with fresh processes and databases;
multi-implementation comparisons rotate their order. Each process handles `/posts` for
10 seconds, then `/echo` for 10 seconds: `pkgx oha` **1.16.0**, 50 connections,
no HTTP warm-up. RPS and p50 are medians; RAM is the largest sampled RSS.
Other desktop applications remained running; benchmark loads ran sequentially.
All other framework rows retain historical results and were not rerun.

Go, Kotlin, OCaml and Rust use the same precompiled whole-string ASCII email rule:
`^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`, checked against
[shared examples](testdata/email-validation.json). Content must be nonempty.
They also use **identical SQLite 3.53.4 source and reported compile options**,
`-O3 -DNDEBUG`, disabled memory accounting, a preallocated page pool, and the
same [connection settings](db/README.md): WAL, `synchronous=NORMAL`, foreign keys,
10-second busy timeout, 2,000 KiB cache, 1,000-page automatic checkpoint,
`temp_store=MEMORY`, mmap disabled and a single-owner `NOMUTEX` connection.

Every write performs `BEGIN IMMEDIATE`, the user insert, the post insert with
five `RETURNING` columns, and its own commit. Responses follow their commits;
there is no batching, response grouping or user cache. This retains the existing
WAL/NORMAL durability policy.

OCaml uses **5.5.1 with Flambda and `-O3`**, **Cohttp/Eio 6.3.0 / Eio 1.5**,
**ATDgen 4.2.0 / Yojson 3.0.0**, and **sqlite3-ocaml 5.4.2**. Its default is
**one HTTP domain + one dedicated SQLite writer domain**, selected for maximum
write throughput. These are two parallel OCaml domains in one process. A second
configuration shows the best echo domain count; both endpoints retain that same
count within a run. Every worker explicitly receives an 8 MiB minor heap.
The full [1/2/4/8-domain results](ocaml/README.md#multicore-results)
document the tradeoff.
The previous single-event-loop OCaml results are superseded.

Kotlin uses **OpenJDK 26.0.2.1**, **Kotlin 2.4.20**, **Vert.x Web 5.1.8**,
**fastjson2 2.0.65** and Panama FFM, with four HTTP event loops and one writer.
Go uses **1.27.1**, **Hertz 0.10.6 / netpoll 0.7.5**, **goccy/go-json 0.10.6**,
and **Tailscale SQLite acbe2dadf94c**. Setup: [Go](go/README.md),
[Kotlin](kotlin/README.md), [OCaml](ocaml/README.md).

Rust uses **1.98.0**, **Actix Web 4.15.0**, **serde_json 1.0.151** and
**rusqlite 0.40.2**. One HTTP worker and one SQLite writer maximize writes;
a second configuration uses three HTTP workers for echo. Each configuration
uses the same worker count for both endpoints. Actix's Tokio/mio runtime uses
macOS **kqueue**. The handler awaits an ordinary reply sent immediately after
its transaction commits. Framework selection used echo throughput and startup,
then verified the full write path. Setup and comparisons: [Rust](rust/README.md).

```sh
oha http://localhost/posts --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

Use `pkgx oha` if `oha` is not installed.

## SQLite write throughput

| Framework | RPS | p50 latency | Peak RAM (RSS) | CPU utilization | Start + UDS bind | Clean release build | Warm debug rebuild | Release binary size |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [Rust Actix (1 HTTP + 1 writer)](rust/) | 48.5K | 0.954ms | 11.2 MiB | 143% | 8.10ms | 44.47s | 1.25s | 1.70 MiB |
| Go Hertz / Tailscale SQLite | 45.2K | 0.988ms | 73.3 MiB | 233% | 9.52ms | 21.11s | 1.23s | 10.12 MiB |
| OCaml Cohttp/Eio (1 HTTP + 1 writer) | 45.1K | 0.973ms | 35.6 MiB | 157% | 8.92ms | 1.12s | 0.521s | 4.60 MiB |
| C# ASP.NET Core | 45K | 1ms | — | — | — | — | — | — |
| Kotlin Vert.x SQLite Panama | 44.5K | 0.998ms | 225.3 MiB | 192% | 1.209s | 18.21s | 1.30s | 17.57 MiB (JAR) |
| Zig http.zig | 43K | 1ms | — | — | — | — | — | — |
| JS Bun Hono | 21K | 1.9ms | — | — | — | — | — | — |
| Python Blacksheep | 19K | 2.5ms | — | — | — | — | — | — |
| Elixir Bandit | 10K | 4.9ms | — | — | — | — | — | — |

Go now measures **45.2K writes/sec** and **297.9K echo RPS**, above the previous 43.7K / 288.0K entries. Kotlin measures **44.5K / 373.6K**, above 42.7K / 364.6K. These are three-run medians, not the best individual samples.

Rust measures **48.5K writes/sec** with one HTTP worker and
**400.4K echo RPS** with three. The default's write runs ranged
from 48.2K–48.5K; the 50K write target was not reached in these runs.
The historical Rust 51K / 266K entries are replaced by these measurements.

Start + UDS bind is median wall time across five fresh launches of the built
native executable or `java -jar`, ending at its first post-bind listening log.
It includes runtime/native-library loading, SQLite initialization and listening.
Databases are migrated beforehand, and an untimed echo checks readiness afterward.
Builds and migration are excluded. Filesystem caches are not flushed, so these
are process-start measurements, not guaranteed cold-cache startup.
[Go/Kotlin script](measure-startup.py), [OCaml script](ocaml/measure-startup.py),
[Rust script](rust/measure-startup.py). Rust also includes HTTP worker setup.
The earlier ~39ms ntex result included an unconditional 25ms framework startup
sleep; switching to Actix brings measured startup below 9ms.
[Startup diagnosis](rust/README.md#measured-results).

**Warm debug rebuild** means an incremental development build after a public API
change, measured on **2026-09-18**. The [shared runner](measure-debug-build.py)
renames `NewPost` (OCaml: `Model.new_post`) and updates its consumers across files
in a disposable source copy. Each of three samples uses a fresh name; the table
reports their median wall time. A full warm-up build and a no-change control are
excluded. Dependencies, compiler caches and Kotlin's daemons remain warm.
Compiled output hashes verify that each edit caused a rebuild.

- Go: `go build -gcflags='all=-N -l' -o bench-debug .`; optimizations and inlining
  disabled, debug information retained, application recompiled and linked.
- Kotlin: `./gradlew --offline --no-build-cache -Pkotlin.incremental=true classes`
  on **OpenJDK 26.0.2.1**. This is the compilation/resources prerequisite of
  `application run`, with debug metadata and incremental Kotlin compilation;
  it neither starts the server nor packages a JAR.
- OCaml: `dune build --profile dev bin/bench.exe`, using the **pkgx** toolchain
  wrapper and the pinned Flambda compiler. The rename also regenerates ATD codecs;
  Dune compiles and links with development/debug flags, without release `-O3`.
- Rust: `cargo build --offline --locked`, Cargo's unoptimized dev profile with
  full debug information and incremental compilation, without release LTO.

Timing includes build-command startup/configuration, application compilation and
linking (or JVM classes/resources). Setup, downloads, source copying/edits, output
checks, tests and application startup are excluded. Native SQLite stays cached
with the same optimized [shared configuration](db/sqlite-config.json).
Raw samples, edit patches and build logs are in
`results/debug-rebuild-2026-09-18/` (gitignored).
After the per-language toolchain/dependency setup, reproduce from the repository
root with a fresh output directory:

```sh
python3 measure-debug-build.py results/debug-build
```

**Clean release build** retains the earlier three-run medians. Its definitions
differ by language:

- [Go](go/measure-build.py): a fresh `GOCACHE` per clean build compiles the standard
  library, dependencies, bundled SQLite C and application. `CGO_CFLAGS=-O3 -DNDEBUG`.
- [Kotlin](kotlin/measure-build.py): warm Gradle/Kotlin daemons and
  offline dependencies, build cache disabled. `clean shadowJar` includes SQLite C
  compilation, jextract, Kotlin/Java compilation and JAR packaging.
- [OCaml](ocaml/measure-build.py): remove Dune's `_build` and disable shared caching;
  generate codecs, compile application OCaml/C modules and link. Compiler/opam
  dependencies and the common native SQLite engine remain prebuilt. SQLite engine
  compilation and pkgx/opam environment resolution are outside this measurement.
- [Rust](rust/measure-build.py): fresh Cargo target directories; compile the shared
  SQLite engine, mimalloc C, Rust dependencies and application, then fat LTO,
  linking and stripping. The Rust standard library is prebuilt. Release builds
  target the host CPU and use `panic=abort`.

Release binary size is the artifact's on-disk size in MiB (2²⁰ bytes), measured on
2026-09-18. Native executables are stripped: Go uses the normal optimized build
with `-trimpath -ldflags='-s -w'`; OCaml uses Dune's release `-O3` profile followed
by macOS `strip` and ad-hoc signing; Rust uses its release `strip=true` profile.
Kotlin uses `shadowJar` on OpenJDK 26.0.2.1.

- Go's **10.12 MiB executable includes statically linked SQLite**.
- Kotlin's **17.57 MiB runnable fat JAR includes dependencies and native SQLite**;
  the separately installed JDK 26 is excluded.
- OCaml's **4.60 MiB executable requires the 1.62 MiB shared SQLite library**,
  for **6.22 MiB combined**.
- Rust's **1.70 MiB executable requires the same 1.62 MiB shared SQLite library**,
  for **3.32 MiB combined**. Both worker configurations use the same executable.

System libraries are excluded. The measured Go, Kotlin and OCaml artifacts passed
echo, committed-write and database-integrity checks. Exact byte counts, hashes,
build commands and validation logs are in `results/release-size-2026-09-18/`
(gitignored). Other entries' binary sizes have not been measured.

Peak RAM is whole-process RSS sampled with `ps` approximately every 100 ms, in MiB
(2²⁰ bytes), including native SQLite and all domains/threads but excluding oha.
It is a sampled peak, not a kernel high-water mark or managed heap size. Echo
retains memory from the preceding write workload. CPU utilization is median
whole-process CPU-time delta divided by elapsed wall time around the load, summed
across all threads/domains; **100% equals one core**. `—` means unmeasured.

## Won't do

- Kotlin Native Ktor: can't listen on Unix domain sockets

## Bonus

HTTP POST JSON echo: parse the request and serialize its two fields.

```sh
oha http://localhost/echo --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "foo@gmail.com" }'
```

| Framework | RPS | p50 latency | Peak RAM (RSS) | CPU utilization | Start + UDS bind | Clean release build | Warm debug rebuild |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [Rust Actix (3 HTTP + 1 writer)](rust/) | 400.4K | 0.093ms | 12.3 MiB | 223% | 8.76ms | 44.47s | 1.25s |
| Kotlin Vert.x | 373.6K | 0.097ms | 392.1 MiB | 282% | 1.209s | 18.21s | 1.30s |
| Go Hertz | 297.9K | 0.151ms | 74.6 MiB | 348% | 9.52ms | 21.11s | 1.23s |
| Zig http.zig | 264K | 0.2ms | — | — | — | — | — |
| [Rust Actix (1 HTTP + 1 writer)](rust/) | 225.1K | 0.176ms | 11.5 MiB | 94% | 8.10ms | 44.47s | 1.25s |
| Python Blacksheep | 192K | 0.2ms | — | — | — | — | — |
| C# ASP.NET Core | 190K | 0.3ms | — | — | — | — | — |
| OCaml Cohttp/Eio (4 HTTP + 1 writer) | 160.4K | 0.295ms | 93.2 MiB | 297% | 10.00ms | 1.12s | 0.521s |
| JS Bun Hono | 156K | 0.3ms | — | — | — | — | — |
| Elixir Bandit | 139K | 0.3ms | — | — | — | — | — |
| OCaml Cohttp/Eio (1 HTTP + 1 writer) | 89.5K | 0.515ms | 37.9 MiB | 88% | 8.92ms | 1.12s | 0.521s |

OCaml's default used **157% CPU during writes** (100% is one core); the 4-HTTP-domain configuration used **297% during echo**. Every HTTP domain served requests, verified by counters. More HTTP domains improve echo but add coordination to SQLite's serialized writer.

All completed requests returned the expected status. Every database passed
integrity, foreign-key, stored-content, single-user and AUTOINCREMENT checks.
oha cancels in-flight requests at its deadline; commits exceeded received 201
responses by at most 50 per run. Both endpoints share one artifact and therefore
build timings.

The local generated `ocaml/measurements.json` report (gitignored) contains individual
samples, validation, CPU usage, versions and source/artifact hashes. Raw oha JSON,
RSS samples, logs and databases are in `results/ocaml-2026-09-17/` (gitignored).
The equivalent Rust report is `rust/measurements.json`, with raw measurements in
`results/rust-2026-09-18/` (both gitignored). Rust reproduction commands are in
[its README](rust/README.md#reproduce-measurements).
After building all three applications and setting `JAVA_HOME` to JDK 26:

```sh
python3 ocaml/measure-comparison.py results/comparison --ocaml-domains 1 2 4 8
python3 measure-startup.py results/startup-go-kotlin
python3 ocaml/measure-startup.py results/startup-ocaml --domains 1
```

Use fresh output directories. Both tables are sorted by descending RPS.
