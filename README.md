# HTTP server benchmarks

- HTTP over Unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro **10 cores (8 performance + 2 efficiency)**, 16 GiB RAM, macOS 26.4.
The **eleven Rust/Go/OCaml/C#/Kotlin JVM/Zig rows marked †** were remeasured on
**2026-09-18** using [randomized valid JSON](loadgen/README.md) and **Vegeta 12.13.0**.
Rust, Go and OCaml use six rotating rounds. C# JIT/AOT and Kotlin JVM retain
the subsequent four-round sweep; the modernized Zig stack uses its own four-round
sweep with one and four HTTP executors. Configurations rotate through order
positions within each sweep, with fresh processes and databases.
Haskell's **two additional † rows** use the same workload in a separate
four-round sweep on **2026-09-22**; they were not measured alongside the older stacks.
Each runs `/posts` for 10 seconds, then `/echo` for 10 seconds, at **50 concurrent requests**, without HTTP warm-up.

The seeded corpus has **65,536 valid email/content pairs** and **32–256 Unicode
characters** of content, randomly selected per request. It exercises many new
and existing users. Every response must be **201 / 200**, respectively; clients
drain outstanding requests and committed rows must match 201 responses exactly.
RPS, p50 and CPU are medians; RAM is the largest sampled server RSS.

Five independent single-processor client processes reduce client contention.
This Mac has **no SMT**; an actual affinity probe returned `KERN_NOT_SUPPORTED`.
These are **same-host, unpinned Unix-socket measurements**, with no enforced
CPU/cache-domain or NUMA separation. Loads run sequentially, after builds and
tests; normal desktop applications remain active. Separate client CPU, calibration,
reproduction commands and isolation limits are in the [load methodology](loadgen/README.md).

**Unmarked rows retain historical static-payload results and are not directly
comparable to the randomized rows.** Zig and Haskell startup, clean/debug build
times and artifact size are also measured with their current implementations.
The other stacks retain their prior non-HTTP measurements.

Go, Kotlin, OCaml, Rust and C# use the same precompiled whole-string ASCII email rule:
`^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`, checked against
[shared examples](testdata/email-validation.json). Content must be nonempty. Zig and Haskell implement the same rule with ASCII
scanners and pass those same fixtures. These seven language implementations use **identical SQLite 3.53.4 source and reported compile options**,
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
The historical [1/2/4/8-domain results](ocaml/README.md#multicore-results)
document the original domain-count selection.
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

Haskell uses **pkgx GHC 9.14.1 / Cabal 3.14.2.0**, **Warp 3.4.16**,
**Aeson 2.3.2.0 decoding / jsonifier 0.2.1.3 encoding**, and
**direct-sqlite 2.3.29**. Two GHC capabilities (`-N2`) are the default;
four (`-N4`) provide the echo configuration. Each keeps the same capability count
across endpoints, with an 8 MiB allocation area per capability and one bound
SQLite writer. HTTP connection threads are distributed across capabilities.
Profiling exposed shared timer contention: pinning **time-manager 0.4.0** fixed
the collapse seen with 0.3.2 and restored multicore scaling. SQLite uses the
library's callback-free stepping API; long foreign calls can delay GHC collection.
[Selection, profiling, tradeoffs and reproduction](haskell/README.md).

```sh
task post
```

Build and reproduction: [randomized load runner](loadgen/README.md). `task benchmark` reruns Rust/Go/OCaml; the load-runner documentation includes C#/Kotlin and the separate Zig and Haskell sweeps.

## SQLite write throughput

| Framework | RPS | p50 latency | Peak RAM (RSS) | CPU utilization | Start + UDS bind | Clean release build | Warm debug rebuild | Release binary size |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [Zig std.http/zio (`-workers 1`)](zig/) † | 31.8K | 1.201ms | 15.8 MiB | 123% | 6.09ms | 34.25s | 3.05s | 0.80 MiB |
| [Haskell Warp (`-N2`)](haskell/) † | 31.3K | 0.609ms | 150.8 MiB | 149% | 13.75ms | 6.79s | 2.78s | 39.25 MiB |
| [Go FastHTTP (`GOMAXPROCS=2`)](go/) † | 31.2K | 1.222ms | 24.8 MiB | 139% | 9.26ms | 19.87s | 1.03s | 7.78 MiB |
| [C# .NET JIT](csharp/) † | 31.0K | 1.210ms | 169.0 MiB | 278% | 166.47ms | 15.83s | 0.707s | 1.81 MiB (bundle) |
| [Rust Actix (`-workers 1`)](rust/) † | 30.8K | 1.238ms | 11.3 MiB | 130% | 12.68ms | 43.20s | 1.23s | 1.70 MiB |
| [C# .NET AOT](csharp/) † | 30.0K | 1.232ms | 105.6 MiB | 257% | 89.28ms | 24.85s | 0.707s (JIT) | 9.19 MiB |
| [OCaml Cohttp/Eio (`-domains 1`)](ocaml/) † | 29.4K | 1.306ms | 37.0 MiB | 148% | 8.92ms | 1.12s | 0.521s | 4.60 MiB |
| [Haskell Warp (`-N4`)](haskell/) † | 29.4K | 0.993ms | 101.2 MiB | 176% | 14.75ms | 6.79s | 2.78s | 39.25 MiB |
| [Rust Actix (`-workers 3`)](rust/) † | 29.2K | 1.298ms | 12.3 MiB | 141% | 10.67ms | 43.20s | 1.23s | 1.70 MiB |
| Kotlin Vert.x SQLite Panama (JVM) † | 28.8K | 1.259ms | 176.3 MiB | 183% | 183.04ms | 21.15s | 1.09s | 14.36 MiB (JAR) |
| [Go FastHTTP (`GOMAXPROCS=4`)](go/) † | 28.6K | 1.372ms | 26.1 MiB | 183% | 8.82ms | 19.87s | 1.03s | 7.78 MiB |
| [Zig std.http/zio (`-workers 4`)](zig/) † | 26.8K | 1.493ms | 14.9 MiB | 187% | 5.84ms | 34.25s | 3.05s | 0.80 MiB |
| [OCaml Cohttp/Eio (`-domains 4`)](ocaml/) † | 23.0K | 1.685ms | 95.4 MiB | 226% | 10.00ms | 1.12s | 0.521s | 4.60 MiB |
| JS Bun Hono | 21K | 1.9ms | — | — | — | — | — | — |
| Python Blacksheep | 19K | 2.5ms | — | — | — | — | — | — |
| Elixir Bandit | 10K | 4.9ms | — | — | — | — | — | — |
| [Kotlin Vert.x SQLite Panama (GraalVM)](kotlin/#optimized-graalvm-executable) | 8.9K | 5.109ms | 104.6 MiB | 116% | 29.62ms | 149.49s | 1.09s (JVM) | 88.12 MiB |

With randomized JSON, Go measures **31.2K writes/sec** at
`GOMAXPROCS=2` and **300.3K echo RPS** at `GOMAXPROCS=4`.
The other endpoints measure **248.9K echo** and **28.6K writes/sec**, respectively.
The same freshly built executable serves both settings. Server code and SQL are
unchanged. Differences from the previous single-user results reflect a new
workload and client, not a controlled implementation regression comparison.

Kotlin JVM measures **28.8K writes/sec** and **365.0K echo RPS**
with randomized JSON. Its four-run write/echo peak RSS is **176.3 / 434.7 MiB**.

Kotlin GraalVM measures **8.9K writes/sec** and **306.2K echo RPS**, with a
**29.62ms** startup median. Native writes ranged from **8.77K–8.95K**, and echo
from **306.0K–307.8K**. These native results retain the earlier static-payload workload.
The native write bottleneck has not been profiled. Every JVM and native run
passed response and database checks.

Rust measures **30.8K writes/sec** with one HTTP worker and
**397.9K echo RPS** with three. One-worker writes ranged
**30.5K–31.7K**; three-worker echo ranged **388.7K–400.2K**.
OCaml measures **29.4K writes/sec / 67.6K echo** with one HTTP domain,
and **23.0K / 139.2K** with four. Both keep a dedicated writer.
These six configurations share the randomized workload and measurement sweep.
C# and Kotlin JVM use the same workload in the subsequent four-round sweep.
Zig uses the same workload in a separate four-round sweep after modernization.

C# measures **31.0K writes/sec (JIT)** and **30.0K (Native AOT)**,
with **324.8K / 290.8K echo RPS**, respectively. Both keep the same server
configuration across endpoints. Its startup/build/size figures retain the prior
measurements; JIT startup predates bundling and excludes bundle extraction.

Zig now uses the installed **0.16.0** compiler, **std.http.Server + zio 0.17.0**,
**yyjson 0.12.0**, and **zqlite** with the shared SQLite **3.53.4** engine.
Its architecture follows Rust: coroutine HTTP executors submit to a bounded queue,
one dedicated thread owns SQLite, and each request resumes immediately after its
own commit. zio supplies working async `std.Io` networking through macOS kqueue.
One executor maximizes writes; four maximize echo among the measured settings.
Both endpoints keep the same executor count within a run. Local comparisons
covered four HTTP stacks, three JSON codecs, two SQLite wrappers, and executor
counts. SQLite wrapper throughput was effectively tied; zqlite had the lower
median. [Selection evidence, full results and reproduction](zig/README.md).

Zig measures **31.8K writes/sec / 230.6K echo RPS** with one executor,
and **26.8K / 377.8K** with four. One-executor writes ranged **31.6K–31.9K**;
four-executor echo ranged **366.5K–389.5K**. Startup medians are **6.09 / 5.84ms**,
respectively. The same **0.80 MiB** release executable serves both configurations;
clean release builds take **34.25s**, and real debug edits rebuild in **3.05s**.

Haskell measures **31.3K writes/sec / 243.9K echo RPS** at `-N2`, and
**29.4K / 304.1K** at `-N4`. Two-capability writes ranged **30.1K–31.4K**;
four-capability echo ranged **302.8K–305.1K**. Startup takes **13.75 / 14.75ms**,
clean application release builds **6.79s**, and debug edits rebuild in **2.78s**.
The callback-free SQLite path improves throughput but has longer write tails:
p99 is **24.247 / 22.495ms**. The [Haskell notes](haskell/README.md) retain
ordinary and mixed FFI comparisons, the timer diagnosis and complete aggregates.
These are separate September 22 measurements; historical JS results remain unchanged.

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
[C# script](csharp/measure.py), [Zig script](zig/measure-startup.py),
[Haskell script](haskell/measure-startup.py). Rust also includes HTTP worker setup. C# launches
the built apphost directly after resolving the runtime through `pkgx dotnet`;
pkgx resolution is excluded from startup timing.
The earlier ~39ms ntex result included an unconditional 25ms framework startup
sleep. Actix removes that framework delay; the current process-start measurements
include ordinary launch variation.
[Startup diagnosis](rust/README.md#measured-results).

**Warm debug rebuild** means an incremental development build after a public API
change, measured on **2026-09-18** (**2026-09-22** for Haskell). The [shared runner](measure-debug-build.py)
renames `NewPost` (OCaml: `Model.new_post`) and updates its consumers across files
in a disposable source copy. Each of three samples uses a fresh name; the table
reports their median wall time. C# and Zig use equivalent
[C#](csharp/measure-build.py) and [Zig](zig/measure-build.py) runners; Haskell uses
[its equivalent runner](haskell/measure-build.py); C# shares its development build between deployment modes. A full warm-up build and a no-change control are
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
- Zig: `python3 zig.py build -Doptimize=Debug`, with LLVM, full debug checks
  and metadata. Three public `NewPost` renames and consumer updates rebuild and
  relink the executable, with Zig compiler caches and the shared SQLite engine warm.
- Haskell: Cabal `--disable-optimization` with `-g`, using the pkgx compiler.
  Three public `NewPost` type/constructor renames and consumer updates rebuild
  application modules and relink; optimized external dependencies remain cached.

Timing includes build-command startup/configuration, application compilation and
linking (or JVM classes/resources). Setup, downloads, source copying/edits, output
checks, tests and application startup are excluded. Native SQLite stays cached
with the same optimized [shared configuration](db/sqlite-config.json).
Raw samples, edit patches and build logs are in
`results/debug-rebuild-2026-09-18/` (gitignored); the updated Go and Rust samples
are in `results/actix-go-final-2026-09-18/debug-builds/`. Zig samples are in
`results/zig-016-builds/`; Haskell samples are in
`results/haskell-multicore-build-2026-09-22/`.
After the per-language toolchain/dependency setup, reproduce from the repository
root with a fresh output directory:

```sh
python3 measure-debug-build.py results/debug-build
```

**Clean release build** reports three-run medians. Go and Rust retain the
preceding static-payload sweep's build measurements. Both Kotlin modes retain their preceding remeasurement; the remaining languages retain
their earlier measurements, except the newly measured Zig and Haskell stacks.
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
- [Zig](zig/measure-build.py): fresh local and global compiler caches in a
  disposable source copy; compile shared SQLite C, yyjson C, zio, the Zig standard
  library and application, link and strip. Three release builds use the installed
  compiler and warm dependency sources; downloads and toolchain installation are excluded.
- [Haskell](haskell/measure-build.py): fresh Cabal application build directories;
  compile Haskell modules and the C configuration shim, link, strip and sign.
  GHC, optimized external dependencies and native SQLite stay prebuilt, matching
  OCaml's application-only scope. pkgx resolution and setup are excluded.

Release binary size is the artifact's on-disk size in MiB (2²⁰ bytes), measured on
2026-09-18 (2026-09-22 for Haskell). Native executables are stripped: Go uses the normal optimized build
with `-trimpath -ldflags='-s -w'`; OCaml uses Dune's release `-O3` profile followed
by macOS `strip` and ad-hoc signing; Rust uses its release `strip=true` profile.
Kotlin uses `shadowJar` on OpenJDK 26.0.2.1, plus `buildJit` for the cached launch.
The GraalVM executable is stripped and ad-hoc signed after compilation.

- Go's **7.78 MiB executable includes statically linked SQLite**. Both
  `GOMAXPROCS` configurations use this same artifact, down from 10.12 MiB.
- Kotlin's **14.36 MiB runnable fat JAR includes dependencies and native SQLite**,
  down from 15.07 MiB after removing unused PostgreSQL and Netty DNS dependencies.
  The current **40.23 MiB HotSpot cache** and **1.70 MiB staged native libraries**
  bring the distribution to **56.29 MiB**, excluding the installed JDK 26.
  Startup and throughput figures retain their earlier measurements.
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
- Zig's **0.80 MiB executable includes std.http, zio and yyjson**, and requires
  the same **1.62 MiB shared SQLite library**, for **2.42 MiB combined**.
  Both executor configurations use the same stripped `ReleaseFast` artifact.

- Haskell's **39.25 MiB stripped executable** contains the GHC runtime and
  Haskell dependencies and requires the **1.62 MiB shared SQLite library**:
  **40.87 MiB combined**, excluding macOS system libraries. Both capability
  configurations use the same executable. No installed GHC is needed to launch it.

System libraries are excluded. The measured Go, Kotlin and OCaml artifacts passed
echo, committed-write and database-integrity checks. Exact byte counts, hashes,
build commands and validation logs are in `results/release-size-2026-09-18/`
(gitignored). Go's earlier dependency audit is in
`results/go-stacks-2026-09-18/` (gitignored). Final Go/Rust samples, verification
results, exact aggregates and artifact hashes are retained locally under
`results/actix-go-final-2026-09-18/` (gitignored). Other entries'
binary sizes have not been measured.

The dependency audit found one HTTP server, JSON implementation and SQLite engine
in Rust (Actix / serde_json / rusqlite), OCaml (Cohttp / generated ATD codecs using
Yojson / sqlite3), C# (Kestrel / System.Text.Json / native SQLite), and Zig (std.http / yyjson /
zqlite with shared SQLite; zio supplies async I/O). Their
experiment and build-tool dependencies are excluded from release binaries.
Haskell links Warp, Aeson decoding, jsonifier encoding and direct-sqlite; the
Snap comparison adapter and Warp's unused TLS certificate support are excluded.
Go's former Hertz build retained four JSON implementations; the replacement links
only FastHTTP, the standard JSON engine and Tailscale SQLite. Go 1.27's v1 JSON
compatibility API delegates to that same v2 engine.

Peak RAM is whole-process RSS sampled with `ps` approximately every 100 ms, in MiB
(2²⁰ bytes), including native SQLite and all domains/threads but excluding the load generator.
It is a sampled peak, not a kernel high-water mark or managed heap size. Echo
retains memory from the preceding write workload. CPU utilization is median
whole-process CPU-time delta divided by elapsed wall time around the load, summed
across all threads/domains; **100% equals one core**. `—` means unmeasured.

## Won't do

- Kotlin Native Ktor: can't listen on Unix domain sockets

## Bonus

HTTP POST JSON echo: parse the request and serialize its two fields.

```sh
task echo
```

| Framework | RPS | p50 latency | Peak RAM (RSS) | CPU utilization | Start + UDS bind | Clean release build | Warm debug rebuild |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [Rust Actix (`-workers 3`)](rust/) † | 397.9K | 0.093ms | 13.5 MiB | 269% | 10.67ms | 43.20s | 1.23s |
| [Zig std.http/zio (`-workers 4`)](zig/) † | 377.8K | 0.104ms | 15.2 MiB | 329% | 5.84ms | 34.25s | 3.05s |
| Kotlin Vert.x (JVM) † | 365.0K | 0.093ms | 434.7 MiB | 380% | 183.04ms | 21.15s | 1.09s |
| [C# .NET JIT](csharp/) † | 324.8K | 0.132ms | 172.1 MiB | 300% | 166.47ms | 15.83s | 0.707s |
| [Kotlin Vert.x (GraalVM)](kotlin/#optimized-graalvm-executable) | 306.2K | 0.131ms | 90.1 MiB | 341% | 29.62ms | 149.49s | 1.09s (JVM) |
| [Haskell Warp (`-N4`)](haskell/) † | 304.1K | 0.113ms | 103.0 MiB | 374% | 14.75ms | 6.79s | 2.78s |
| [Go FastHTTP (`GOMAXPROCS=4`)](go/) † | 300.3K | 0.117ms | 26.4 MiB | 345% | 8.82ms | 19.87s | 1.03s |
| [C# .NET AOT](csharp/) † | 290.8K | 0.154ms | 106.2 MiB | 299% | 89.28ms | 24.85s | 0.707s (JIT) |
| [Go FastHTTP (`GOMAXPROCS=2`)](go/) † | 248.9K | 0.171ms | 24.9 MiB | 197% | 9.26ms | 19.87s | 1.03s |
| [Haskell Warp (`-N2`)](haskell/) † | 243.9K | 0.179ms | 153.7 MiB | 194% | 13.75ms | 6.79s | 2.78s |
| [Zig std.http/zio (`-workers 1`)](zig/) † | 230.6K | 0.203ms | 15.9 MiB | 99% | 6.09ms | 34.25s | 3.05s |
| [Rust Actix (`-workers 1`)](rust/) † | 201.4K | 0.201ms | 12.5 MiB | 99% | 12.68ms | 43.20s | 1.23s |
| Python Blacksheep | 192K | 0.2ms | — | — | — | — | — |
| JS Bun Hono | 156K | 0.3ms | — | — | — | — | — |
| [OCaml Cohttp/Eio (`-domains 4`)](ocaml/) † | 139.2K | 0.330ms | 98.2 MiB | 316% | 10.00ms | 1.12s | 0.521s |
| Elixir Bandit | 139K | 0.3ms | — | — | — | — | — |
| [OCaml Cohttp/Eio (`-domains 1`)](ocaml/) † | 67.6K | 0.692ms | 39.2 MiB | 94% | 8.92ms | 1.12s | 0.521s |

All randomized requests returned the expected status. Every database passed
integrity, foreign-key, timestamp, exact email/content-pair and AUTOINCREMENT
checks. Drained 201 responses equal committed rows exactly. Both endpoints keep
the same artifact and server configuration. Each OCaml HTTP domain served requests.

The `results/random-json-2026-09-18/summary.json` report (gitignored) contains
the Rust/Go/OCaml rounds, verification, client CPU, topology and artifact hashes.
The earlier additional four-stack sweep is in `results/random-json-remaining-2026-09-18/summary.json`
(gitignored), with its raw logs, histograms and databases alongside it.
Modernized Zig's final four-round sweep is in `results/zig-016-final/summary.json`;
startup samples are in `results/zig-016-start-{1,4}/`, build samples in
`results/zig-016-builds/`, and exact byte counts/linkage/hashes in
`results/zig-016-artifact.json` (all gitignored).
Haskell's final four-round report is
`results/haskell-multicore-final-2026-09-22/summary.json`; startup/build/profile
reports use the corresponding `haskell-multicore-{startup,build,profile}-2026-09-22/`
directories. Exact bytes, linkage and hashes are in
`results/haskell-multicore-artifact-2026-09-22.json` (all gitignored).
A compact [measurement record](haskell/measurements.json) retains the published
aggregates, individual samples, correctness results and source/artifact hashes.
Full raw JSON, histograms, RSS samples, logs and databases remain locally under
`results/random-json-2026-09-18/` (gitignored). Historical reports remain under
`results/actix-go-final-2026-09-18/` and `results/ocaml-2026-09-17/`.

Both Kotlin modes' complete measurements are in
`results/kotlin-complete-2026-09-18/` (gitignored): `runtimes/summary.json` contains
the historical static-payload values, artifact/source hashes, startup checks and database verification;
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

Use fresh output directories. Both tables are sorted by descending RPS; † rows use randomized JSON; unmarked rows retain the older static workload.
