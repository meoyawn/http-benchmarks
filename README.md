# HTTP server benchmarks

- HTTP over Unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro **10 cores (8 performance + 2 efficiency)**, 16 GiB RAM, macOS 26.4.
OCaml was remeasured on **2026-09-17**. Go, Kotlin JVM and GraalVM were
fully remeasured on **2026-09-18**, including both workloads, startup, clean builds,
debug rebuilds and artifact sizes. Rust and both C# modes (JIT and Native AOT)
were also measured on **2026-09-18**.
Each has three runs with fresh processes and databases;
multi-implementation comparisons rotate their order. Each process handles `/posts` for
10 seconds, then `/echo` for 10 seconds: `pkgx oha` **1.16.0**, 50 connections,
no HTTP warm-up. RPS and p50 are medians; RAM is the largest sampled RSS.
Other desktop applications remained running; benchmark loads ran sequentially.
All other framework rows retain historical results and were not rerun.

Go, Kotlin, OCaml, Rust and C# use the same precompiled whole-string ASCII email rule:
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
count within a run. `-domains 1` / `-domains 4` select HTTP domains; the writer
adds one. Every worker explicitly receives an 8 MiB minor heap.
The full [1/2/4/8-domain results](ocaml/README.md#multicore-results)
document the tradeoff.
The previous single-event-loop OCaml results are superseded.

Kotlin's JVM row uses **OpenJDK 26.0.2.1**, **Kotlin 2.4.20**, **Vert.x Web 5.1.8**,
**fastjson2 2.0.65** and Panama FFM, with four HTTP event loops and one writer.
The GraalVM row uses the same application and SQLite with **Oracle GraalVM
25.3.4.1 / JDK 25.0.4.1**, `-O3`, `-march=native`, the default Serial GC and
ML-inferred profiles (no workload-trained PGO). fastjson2 initializes at runtime
in the native executable. Both optimized native and fat-JAR builds remain available.
Go uses **1.27.1**, **FastHTTP 1.74.0**, **encoding/json/v2**,
and **Tailscale SQLite acbe2dadf94c**. `GOMAXPROCS=2` is the write-optimized
default; `GOMAXPROCS=4` is the echo configuration. Both rows use the same binary,
with the same setting for both endpoints. This knob controls Go scheduler
processors, not an HTTP worker count or OS thread limit. The SQLite writer is
unchanged. The library comparison covered
Hertz's two transports, poller/buffer tuning, FastHTTP, `net/http`, and automatic
struct-mapping JSON codecs including `encoding/json/v2`. Setup: [Go](go/README.md),
[Kotlin](kotlin/README.md), [OCaml](ocaml/README.md).

Rust uses **1.98.0**, **Actix Web 4.15.0**, **serde_json 1.0.151** and
**rusqlite 0.40.2**. One HTTP worker and one SQLite writer maximize writes;
a second configuration uses three HTTP workers for echo. Each configuration
uses the same worker count for both endpoints. `-workers 1` / `-workers 3` select
HTTP workers; the SQLite writer adds one thread. Actix's Tokio/mio runtime uses
macOS **kqueue**. The handler awaits an ordinary reply sent immediately after
its transaction commits. Framework selection used echo throughput and startup,
then verified the full write path. Setup and comparisons: [Rust](rust/README.md).

C# uses **.NET SDK 10.0.401 / runtime 10.0.12**, **Kestrel minimal APIs**,
source-generated **System.Text.Json**, and generated .NET interop with the shared
SQLite engine. Its **JIT** and **Native AOT** rows use the same
application, two pool workers, three socket I/O threads and one dedicated SQLite
writer. Kestrel and the runtime inline HTTP continuations; generic JSON responses
carry their actual serialized content length. Both endpoints retain this setup.
Each commit immediately completes that request's ordinary task reply, without
batching HTTP wake-ups. JIT uses the installed runtime's tiering
and dynamic PGO defaults. AOT optimizes for speed and targets the host CPU.
Setup, library comparisons and tradeoffs: [C#](csharp/README.md).

```sh
oha http://localhost/posts --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

Use `pkgx oha` if `oha` is not installed.

## SQLite write throughput

| Framework | RPS | p50 latency | Peak RAM (RSS) | CPU utilization | Start + UDS bind | Clean release build | Warm debug rebuild | Release binary size |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [Go FastHTTP (`GOMAXPROCS=2`)](go/) | 50.7K | 0.883ms | 23.8 MiB | 150% | 8.59ms | 20.20s | 1.03s | 7.78 MiB |
| [Rust Actix (`-workers 1`)](rust/) | 48.5K | 0.954ms | 11.2 MiB | 143% | 8.10ms | 44.47s | 1.25s | 1.70 MiB |
| [C# .NET JIT](csharp/) | 48.4K | 0.908ms | 168.7 MiB | 320% | 166.47ms | 15.83s | 0.707s | 1.81 MiB (bundle) |
| [C# .NET AOT](csharp/) | 48.2K | 0.906ms | 105.3 MiB | 307% | 89.28ms | 24.85s | 0.707s (JIT) | 9.19 MiB |
| [Rust Actix (`-workers 3`)](rust/) | 46.1K | 0.995ms | 12.0 MiB | 165% | 8.76ms | 44.47s | 1.25s | 1.70 MiB |
| Kotlin Vert.x SQLite Panama (JVM) | 45.3K | 0.992ms | 170.7 MiB | 191% | 183.04ms | 21.15s | 1.09s | 15.07 MiB (JAR) |
| [OCaml Cohttp/Eio (`-domains 1`)](ocaml/) | 45.1K | 0.973ms | 35.6 MiB | 157% | 8.92ms | 1.12s | 0.521s | 4.60 MiB |
| [Go FastHTTP (`GOMAXPROCS=4`)](go/) | 44.1K | 1.025ms | 25.4 MiB | 216% | 8.65ms | 20.20s | 1.03s | 7.78 MiB |
| Zig http.zig | 43K | 1ms | — | — | — | — | — | — |
| [OCaml Cohttp/Eio (`-domains 4`)](ocaml/) | 35.1K | 1.298ms | 89.6 MiB | 261% | 10.00ms | 1.12s | 0.521s | 4.60 MiB |
| JS Bun Hono | 21K | 1.9ms | — | — | — | — | — | — |
| Python Blacksheep | 19K | 2.5ms | — | — | — | — | — | — |
| Elixir Bandit | 10K | 4.9ms | — | — | — | — | — | — |
| [Kotlin Vert.x SQLite Panama (GraalVM)](kotlin/#optimized-graalvm-executable) | 8.9K | 5.109ms | 104.6 MiB | 116% | 29.62ms | 149.49s | 1.09s (JVM) | 88.12 MiB |

Go measures **50.7K writes/sec** with `GOMAXPROCS=2` and **333.6K echo RPS**
with `GOMAXPROCS=4`. Their other endpoints measure **270.7K echo** and **44.1K
writes/sec**, respectively: each row keeps its processor setting for both tests.
All three final write samples at two processors exceed 50K (50.4K–50.9K), and all
four-processor echo samples exceed 332K (332.5K–334.0K). Both targets are met using
the two explicitly labeled configurations. Peak RSS is **23.8 / 24.3 MiB** for
two-processor writes/echo and **25.4 / 25.8 MiB** for four processors.

The fresh Kotlin JVM measurements are **45.3K writes/sec** and **373.0K echo RPS**.
These are three-run medians, not the best individual samples; JVM writes ranged from 40.3K–45.4K and
echo from 362.2K–378.2K. JVM peak sampled RSS is **170.7 / 490.3 MiB** for
writes/echo; the echo maximum comes from the first run's higher RSS.

Kotlin GraalVM measures **8.9K writes/sec** and **306.2K echo RPS**, with a
**29.62ms** startup median. Native writes ranged from **8.77K–8.95K**, and echo
from **306.0K–307.8K**. It has lower throughput and sampled RSS than the JVM.
The native write bottleneck has not been profiled. Every JVM and native run
passed response and database checks.

Rust measures **48.5K writes/sec** with one HTTP worker and
**400.4K echo RPS** with three. The default's write runs ranged
from 48.2K–48.5K; the 50K write target was not reached in these runs.
The historical Rust 51K / 266K entries are replaced by these measurements.

C# measures **48.4K writes/sec (JIT)** and **48.2K (Native AOT)**,
with **368.8K / 349.2K echo RPS** using the same configuration for both endpoints.
The HTTP/JSON setup correction replaces the earlier 146.2K / 136.3K echo results:
inline I/O scheduling and ordinary buffered JSON responses avoid extra queue
transfers and chunked response framing. The JSON helper is generic; it has no
model-size assumptions or fixed response data. SQLite transactions and immediate
per-commit replies are unchanged.

Write runs ranged **44.1K–48.8K (JIT)** and **47.6K–48.7K (AOT)**;
echo ranged **362.2K–382.0K / 340.6K–350.8K**. All three samples are retained,
including the slower JIT write run. Small differences between write medians do
not establish a stable ordering. The historical 45K / 190K C# row is not a
controlled baseline. The earlier 47.4K versus 44.3K AOT concern and the separate
HTTP setup experiments are documented in [C#](csharp/README.md#regression-check).

C# performance and startup figures above retain the preceding measurements.
Only release size was updated for the JIT bundle. New timing and throughput
measurements are excluded because they overlapped background video playback.
JIT's retained startup figure predates bundling and excludes bundle extraction.

Both Kotlin rows were fully remeasured after removing Jackson, staging native
libraries at build time and adding a HotSpot class/linkage/profile cache for the
JVM. JVM startup is **183.04ms** median across seven fresh processes
(179.30–187.10ms), with full tiered JIT enabled. First echo completes at
**223.44ms**, followed by the first committed post at **239.88ms**, from launch.
GraalVM startup is **29.62ms** (28.64–30.63ms); its first echo/post complete at
**31.11 / 32.42ms**. The **21.15s** JVM clean build includes cache training and
assembly; the native clean build is **149.49s**. Both rows use the newly measured
**1.09s** JVM development rebuild, labeled `(JVM)` for GraalVM.
[Profiling and reproduction](kotlin/README.md#binary-startup).

Start + UDS bind is median wall time across five fresh launches (seven for the
updated Kotlin startup) of the built
native executable or `java -jar`, ending at its first post-bind listening log.
It includes runtime/native-library loading, SQLite initialization and listening.
Databases are migrated beforehand, and an untimed echo checks readiness afterward.
Builds and migration are excluded. Filesystem caches are not flushed, so these
are process-start measurements, not guaranteed cold-cache startup.
[Go script](go/measure-startup.py), [Kotlin startup script](kotlin/measure-startup.py), [OCaml script](ocaml/measure-startup.py),
[Rust script](rust/measure-startup.py),
[Kotlin GraalVM startup script](kotlin/measure-startup.py),
[C# script](csharp/measure.py). Rust also includes HTTP worker setup. C# launches
the built apphost directly after resolving the runtime through `pkgx dotnet`;
pkgx resolution is excluded from startup timing.
The earlier ~39ms ntex result included an unconditional 25ms framework startup
sleep; switching to Actix brings measured startup below 9ms.
[Startup diagnosis](rust/README.md#measured-results).

**Warm debug rebuild** means an incremental development build after a public API
change, measured on **2026-09-18**. The [shared runner](measure-debug-build.py)
renames `NewPost` (OCaml: `Model.new_post`) and updates its consumers across files
in a disposable source copy. Each of three samples uses a fresh name; the table
reports their median wall time. C# uses the equivalent
[C# runner](csharp/measure-build.py) for both deployment modes. A full warm-up build and a no-change control are
excluded. Dependencies, compiler caches and Kotlin's daemons remain warm.
Compiled output hashes verify that each edit caused a rebuild.

- Go: `go build -gcflags='all=-N -l' -o bench-debug .`; optimizations and inlining
  disabled, debug information retained, application recompiled and linked.
- Kotlin: `./gradlew --offline --no-build-cache -Pkotlin.incremental=true classes`
  on **OpenJDK 26.0.2.1**. This is the compilation/resources prerequisite of
  `application run`, with debug metadata and incremental Kotlin compilation;
  it neither starts the server nor packages a JAR.
  The GraalVM row shares this JVM development workflow; its `(JVM)`
  label distinguishes that measurement from a native-image rebuild.
- OCaml: `dune build --profile dev bin/bench.exe`, using the **pkgx** toolchain
  wrapper and the pinned Flambda compiler. The rename also regenerates ATD codecs;
  Dune compiles and links with development/debug flags, without release `-O3`.
- Rust: `cargo build --offline --locked`, Cargo's unoptimized dev profile with
  full debug information and incremental compilation, without release LTO.
- C# (both rows): `pkgx dotnet build -c Debug --no-restore`, renaming the
  public `NewPost` type and consumers across files. The SDK/runtime and native
  SQLite stay cached; the unoptimized application is recompiled. AOT uses this same JIT-based development workflow.

Timing includes build-command startup/configuration, application compilation and
linking (or JVM classes/resources). Setup, downloads, source copying/edits, output
checks, tests and application startup are excluded. Native SQLite stays cached
with the same optimized [shared configuration](db/sqlite-config.json).
Raw samples, edit patches and build logs are in
`results/debug-rebuild-2026-09-18/` (gitignored); the updated Go samples are in
`results/go-stacks-2026-09-18/debug/`.
After the per-language toolchain/dependency setup, reproduce from the repository
root with a fresh output directory:

```sh
python3 measure-debug-build.py results/debug-build
```

**Clean release build** reports three-run medians. Go's selected stack and both
Kotlin modes were remeasured; other languages retain their preceding measurements.
Definitions differ by language:

- [Go](go/measure-build.py): a fresh `GOCACHE` per clean build compiles the standard
  library, dependencies, bundled SQLite C and application, including release
  stripping. `CGO_CFLAGS=-O3 -DNDEBUG`.
- [Kotlin](kotlin/measure-build.py): warm Gradle/Kotlin daemons and
  offline dependencies, build cache disabled. `clean buildJit` includes SQLite C
  compilation, jextract, Kotlin/Java compilation, JAR packaging, native staging
  and verified HotSpot cache training/assembly.
  GraalVM uses three `clean nativeCompile` builds on JDK 25, including the same
  prerequisites plus `-O3` native compilation, linking, stripping and macOS
  ad-hoc signing and native-library staging. Dependency and reachability-metadata
  downloads are excluded.
- [OCaml](ocaml/measure-build.py): remove Dune's `_build` and disable shared caching;
  generate codecs, compile application OCaml/C modules and link. Compiler/opam
  dependencies and the common native SQLite engine remain prebuilt. SQLite engine
  compilation and pkgx/opam environment resolution are outside this measurement.
- [Rust](rust/measure-build.py): fresh Cargo target directories; compile the shared
  SQLite engine, mimalloc C, Rust dependencies and application, then fat LTO,
  linking and stripping. The Rust standard library is prebuilt. Release builds
  target the host CPU and use `panic=abort`.
- [C#](csharp/measure-build.py): remove `bin`, `obj` and native SQLite from a
  disposable source copy, compile the shared SQLite C plus its configuration ABI
  shim, and run `pkgx dotnet publish`. The retained JIT timing emits IL without
  single-file bundling;
  AOT compiles, links and strips native code. The installed SDK/runtime and NuGet
  caches remain warm; downloads are excluded. Each mode has three rotating runs.

Release binary size is the artifact's on-disk size in MiB (2²⁰ bytes), measured on
2026-09-18. Native executables are stripped: Go uses the normal optimized build
with `-trimpath -ldflags='-s -w'`; OCaml uses Dune's release `-O3` profile followed
by macOS `strip` and ad-hoc signing; Rust uses its release `strip=true` profile.
Kotlin uses `shadowJar` on OpenJDK 26.0.2.1, plus `buildJit` for the cached launch.
The GraalVM executable is stripped and ad-hoc signed after compilation.

- Go's **7.78 MiB executable includes statically linked SQLite**. Both
  `GOMAXPROCS` configurations use this same artifact, down from 10.12 MiB.
- Kotlin's **15.07 MiB runnable fat JAR includes dependencies and native SQLite**.
  The **183.04ms** startup uses **40.97 MiB of HotSpot cache** and **1.75 MiB of
  staged native libraries**, **57.80 MiB combined**. The installed JDK 26 is excluded.
- Kotlin GraalVM's **88.12 MiB executable includes SQLite and Netty native
  resources**, plus a **0.07 MiB `libmanagement_ext.dylib`** runtime library and
  **1.75 MiB staged native libraries** for the **29.62ms** startup:
  **89.95 MiB combined**, with no separately installed JDK required.
- OCaml's **4.60 MiB executable requires the 1.62 MiB shared SQLite library**,
  for **6.22 MiB combined**.
- Rust's **1.70 MiB executable requires the same 1.62 MiB shared SQLite library**,
  for **3.32 MiB combined**. Both worker configurations use the same executable.

- C# JIT's **1.81 MiB single-file bundle includes the application and all
  application dependencies, including native SQLite**. The separately installed
  .NET/ASP.NET runtime is excluded, as the JDK is for Kotlin's fat JAR. SQLite
  extracts to the .NET bundle cache on first launch; application code remains IL.
- C# AOT's **9.19 MiB stripped executable** includes its runtime and requires
  the **1.62 MiB SQLite library** beside it, for **10.81 MiB combined**.
  Debug symbols are excluded from both C# sizes; JIT's manifests are bundled.

System libraries are excluded. The measured Go, Kotlin and OCaml artifacts passed
echo, committed-write and database-integrity checks. Exact byte counts, hashes,
build commands and validation logs are in `results/release-size-2026-09-18/`
(gitignored). Go's updated audit and measurements are in
`results/go-stacks-2026-09-18/` (gitignored), including exact aggregates and
artifact hashes. Other entries' binary sizes have not been measured.

The dependency audit found one HTTP server, JSON implementation and SQLite engine
in Rust (Actix / serde_json / rusqlite), OCaml (Cohttp / generated ATD codecs using
Yojson / sqlite3), and C# (Kestrel / System.Text.Json / native SQLite). Their
experiment and build-tool dependencies are excluded from release binaries.
Go's former Hertz build retained four JSON implementations; the replacement links
only FastHTTP, the standard JSON engine and Tailscale SQLite. Go 1.27's v1 JSON
compatibility API delegates to that same v2 engine.

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
| [Rust Actix (`-workers 3`)](rust/) | 400.4K | 0.093ms | 12.3 MiB | 223% | 8.76ms | 44.47s | 1.25s |
| Kotlin Vert.x (JVM) | 373.0K | 0.096ms | 490.3 MiB | 277% | 183.04ms | 21.15s | 1.09s |
| [C# .NET JIT](csharp/) | 368.8K | 0.116ms | 170.0 MiB | 258% | 166.47ms | 15.83s | 0.707s |
| [C# .NET AOT](csharp/) | 349.2K | 0.126ms | 105.9 MiB | 267% | 89.28ms | 24.85s | 0.707s (JIT) |
| [Go FastHTTP (`GOMAXPROCS=4`)](go/) | 333.6K | 0.129ms | 25.8 MiB | 308% | 8.65ms | 20.20s | 1.03s |
| [Kotlin Vert.x (GraalVM)](kotlin/#optimized-graalvm-executable) | 306.2K | 0.131ms | 90.1 MiB | 341% | 29.62ms | 149.49s | 1.09s (JVM) |
| [Go FastHTTP (`GOMAXPROCS=2`)](go/) | 270.7K | 0.165ms | 24.3 MiB | 185% | 8.59ms | 20.20s | 1.03s |
| Zig http.zig | 264K | 0.2ms | — | — | — | — | — |
| [Rust Actix (`-workers 1`)](rust/) | 225.1K | 0.176ms | 11.5 MiB | 94% | 8.10ms | 44.47s | 1.25s |
| Python Blacksheep | 192K | 0.2ms | — | — | — | — | — |
| [OCaml Cohttp/Eio (`-domains 4`)](ocaml/) | 160.4K | 0.295ms | 93.2 MiB | 297% | 10.00ms | 1.12s | 0.521s |
| JS Bun Hono | 156K | 0.3ms | — | — | — | — | — |
| Elixir Bandit | 139K | 0.3ms | — | — | — | — | — |
| [OCaml Cohttp/Eio (`-domains 1`)](ocaml/) | 89.5K | 0.515ms | 37.9 MiB | 88% | 8.92ms | 1.12s | 0.521s |

OCaml's default used **157% CPU during writes** (100% is one core); the 4-HTTP-domain configuration used **297% during echo**. Every HTTP domain served requests, verified by counters. More HTTP domains improve echo but add coordination to SQLite's serialized writer.

All completed requests returned the expected status. Every database passed
integrity, foreign-key, stored-content, single-user and AUTOINCREMENT checks.
oha cancels in-flight requests at its deadline; commits exceeded received 201
responses by at most 50 per run. Both endpoints share one artifact per
configuration and therefore build timings. Each C# deployment mode has its own artifact and clean-build timing.

The local generated `ocaml/measurements.json` report (gitignored) contains individual
samples, validation, CPU usage, versions and source/artifact hashes. Raw oha JSON,
RSS samples, logs and databases are in `results/ocaml-2026-09-17/` (gitignored).
The equivalent Rust report is `rust/measurements.json`, with raw measurements in
`results/rust-2026-09-18/` (both gitignored). Rust reproduction commands are in
[its README](rust/README.md#reproduce-measurements).
Both Kotlin modes' complete measurements are in
`results/kotlin-complete-2026-09-18/` (gitignored): `runtimes/summary.json` contains
all table values, artifact/source hashes, startup checks and database verification;
`jvm-build/`, `native-build/` and `debug-build/` contain build logs and raw timings.
[Reproduction commands](kotlin/README.md#optimized-graalvm-executable).

```sh
python3 ocaml/measure-comparison.py results/comparison --ocaml-domains 1 2 4 8
python3 measure-startup.py results/startup-go-kotlin
python3 ocaml/measure-startup.py results/startup-ocaml --domains 1
```

Go's two configurations, after `cd go && task build`, can be reproduced from
the repository root with:

```sh
python3 go/measure-configs.py results/go-http --binary go/bench --gomaxprocs 2 4
python3 go/measure-startup.py results/go-startup --gomaxprocs 2 4
python3 go/measure-build.py results/go-build
python3 measure-debug-build.py results/go-debug --language go
```

C# raw runs, artifact/source hashes and verification are in
`results/csharp-http-2026-09-18/`; startup samples are in
`results/csharp-http-startup-2026-09-18/`, and clean/debug build samples in
`results/csharp-http-build-2026-09-18/` (all gitignored). Earlier runs and the
controlled implementation comparison remain recorded in the [C# notes](csharp/README.md).

C# reproduction after publishing both modes:

```sh
python3 csharp/measure.py results/csharp-http --mode jit aot --rounds 3
python3 csharp/measure.py results/csharp-startup --mode jit aot --startup --fresh-bundle-extraction --rounds 5
python3 csharp/measure-build.py results/csharp-build --mode jit aot
```

Use fresh output directories. Both tables are sorted by descending RPS.
