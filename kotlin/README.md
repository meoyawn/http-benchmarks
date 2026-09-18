# Kotlin Vert.x / SQLite FFM benchmark

`POST /posts` parses and validates JSON, inserts or reuses a user, inserts a post,
and returns the persisted row after committing. `POST /echo` parses and serializes
the two request fields. Both use HTTP/1.1 over a Unix domain socket.

The selected stack is **Vert.x Web + fastjson2 + Panama FFM**, with a bundled,
optimized SQLite build. Routing and body handling use Vert.x's application APIs.
Four HTTP event loops share one SQLite writer thread and a bounded 1,024-request
queue. Every transaction schedules its response immediately after commit.

The shared schema, SQL and returned fields are unchanged. Each
request executes `BEGIN IMMEDIATE`, `INSERT OR IGNORE` for the user, the post insert
with `RETURNING`, and `COMMIT`. This preserves SQLite's AUTOINCREMENT gaps on ignored
user inserts. There is no transaction batching, response grouping or user/post
cache. WAL, `synchronous=NORMAL`, foreign keys, the 10-second busy timeout and the
explicit 1,000-page WAL autocheckpoint match Go and OCaml. The
[shared SQLite configuration](../db/README.md) also aligns compiler options, page
pool, connection cache, temporary storage and mmap behavior.

Email validation uses the same precompiled, whole-string ASCII regex as Go and
OCaml: `^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`. Content must be
nonempty. The shared [validation examples](../testdata/email-validation.json)
are checked by all three implementations.

## Versions

Measured on 2026-09-17, Apple M1 Pro, 16 GiB RAM, macOS 26.4.

| Component | Version |
| --- | --- |
| Homebrew OpenJDK | 26.0.2.1 |
| [Kotlin](https://kotlinlang.org/docs/releases.html) | 2.4.20 |
| [Vert.x Web](https://vertx.io/docs/vertx-web/java/) | 5.1.8 |
| Netty, aligned with the Vert.x BOM | 4.2.18.Final |
| [fastjson2](https://github.com/alibaba/fastjson2) | 2.0.65 |
| Kotlin coroutines | 1.11.0 |
| [SQLite](https://sqlite.org/download.html), compiled from pinned source | 3.53.4 |
| [jextract](https://jdk.java.net/jextract/) | 25-jextract+2-4 |
| [Gradle](https://gradle.org/releases/) / [Shadow](https://plugins.gradle.org/plugin/com.gradleup.shadow) | 9.7.1 / 9.6.1 |
| Dependency updates plugin | 0.64.0 |
| JUnit / AssertJ | 6.1.3 / 3.27.7 |
| Current randomized load driver | [Vegeta 12.13.0](../loadgen/README.md) |

jextract's bundled Java 25 runs the generator only. Compilation, tests and the
server use Java 26 and its stable Foreign Function & Memory API; no preview flags.
The production build contains the selected stack only; experimental comparison
sources, dependencies and Gradle tasks are not included.
fastjson2 is the only bundled JSON implementation. `FastJsonFactory` supplies
Vert.x's JSON SPI for configuration, JSON wrappers and the optional PostgreSQL
path. Jackson is excluded from all Gradle configurations; the unused JSON-schema
dependency is removed and WebClient is test-only.

## Build and run

Requires JDK 26, jextract 25, Python 3 and a C compiler. On macOS Apple Silicon,
with Homebrew OpenJDK 26 and Xcode Command Line Tools already installed, using fish:

```fish
cd kotlin
set -gx JAVA_HOME (brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home
set -gx JEXTRACT_HOME "$PWD/.tools/jextract-25"

mkdir -p .tools
curl -fL 'https://download.java.net/java/early_access/jextract/25/2/openjdk-25-jextract+2-4_macos-aarch64_bin.tar.gz' -o .tools/jextract.tar.gz
echo '3dd1dd1bde059d271739e2cc2290c64f93f85488c86c01e566c0e374eece798f  .tools/jextract.tar.gz' | shasum -a 256 -c -
tar -xzf .tools/jextract.tar.gz -C .tools

./gradlew test buildJit

# Once, for a fresh database; reuse an already migrated database as-is.
sqlite3 -bail ../db/db.sqlite < ../db/migrations/001_init.up.sql
task start
```

The SQLite CLI is only used for the initial migration. Building downloads and
verifies the SQLite amalgamation, compiles it with `cc -O3`, generates Java FFM
bindings from its header, and packages the host's native library inside the JAR.
Downloads are cached in `.tools`; subsequent builds support Gradle's `--offline`.
Set `CC` to select another C compiler. The Gradle distribution and SQLite archive
are pinned with SHA-256 checksums.

`buildJit` also stages the JAR's three native libraries in `build/jit/native`
and trains a JDK 26 HotSpot cache at `build/jit/kotlin-bench.aot` using the packaged
HTTP verification suite and a disposable database. It regenerates the cache when
the JAR (including its timestamp), native libraries, training inputs or JDK change.
The cache stores classes, linkage and method profiles; full tiered JIT compilation
and the default GC remain enabled. This is separate from GraalVM Native Image.

Without Task, run from this directory:

```fish
"$JAVA_HOME/bin/java" -server -XX:+PerfDisableSharedMem --enable-native-access=ALL-UNNAMED \
  "-Djava.library.path=$PWD/build/jit/native" \
  "-Dsqlite.library=$PWD/build/jit/native/libsqlite3.dylib" \
  -XX:AOTMode=on -XX:AOTCache=build/jit/kotlin-bench.aot \
  -Dhttp.socket=/tmp/benchmark.sock -jar build/libs/kotlin-1.0-all.jar
```

On Linux use `libsqlite3.so`. The cache requires the matching JAR and JDK;
`AOTMode=on` fails explicitly on an incompatible cache. Rebuild with `buildJit`
after changing either. To use just the self-contained JAR, build with `shadowJar`
and omit the two native-library properties and the two AOT options; this retains
the slower per-process extraction path. Distributing the fast launch requires
the JAR, `build/jit/native`, the cache and a matching installed JDK.

The default database is `../db/db.sqlite`. Override it with
`-Ddb.path=/absolute/path.sqlite` before `-jar`. `-Dhttp.workers=4` is the default;
no heap-size or garbage-collector override was used in the published results.
An existing socket is rejected, and the database must already be migrated.
Stop with Ctrl-C or SIGTERM to close the HTTP server and drain the writer queue.
The optional PostgreSQL path remains available with `-Ddb.backend=postgres`;
it was not benchmarked in this update.

### Optimized GraalVM executable

`shadowJar` retains the standalone JDK 26 fat-JAR build; `buildJit` / `task build`
add native staging and the HotSpot cache. The separate
`nativeCompile` / `task build-native` target uses **Oracle GraalVM 25.3.4.1**
(JDK **25.0.4.1**) and Native Build Tools **1.1.13**. GraalVM's current release
line uses JDK 25, selected with `-PjavaVersion=25`; the default remains 26.
Install [GraalVM](https://www.graalvm.org/downloads/) for your platform, then:

```fish
set -gx JAVA_HOME /absolute/path/to/graalvm/Contents/Home # macOS
set -gx GRAALVM_HOME $JAVA_HOME
set -gx JEXTRACT_HOME "$PWD/.tools/jextract-25"
./gradlew -PjavaVersion=25 nativeCompile
python3 verify-executable.py --native build/native/nativeCompile/kotlin-bench \
  "--runtime-arg=-Djava.library.path=$PWD/build/native/nativeCompile/native" \
  "--runtime-arg=-Dsqlite.library=$PWD/build/native/nativeCompile/native/libsqlite3.dylib"

# Uses the same already migrated database and system-property overrides as the JAR.
build/native/nativeCompile/kotlin-bench \
  "-Djava.library.path=$PWD/build/native/nativeCompile/native" \
  "-Dsqlite.library=$PWD/build/native/nativeCompile/native/libsqlite3.dylib" \
  -Dhttp.socket=/tmp/benchmark.sock
```

On Linux, `JAVA_HOME` is the extracted GraalVM directory itself. The build enables
`-O3`, `-march=native` and FFM native access, with no JVM fallback. Host-CPU targeting
means the executable is intended for the build CPU or a compatible CPU. It embeds
the same SQLite library and Netty native transport resources as the JAR; no JDK
is required to run it. The build also stages the same native libraries in the
adjacent `native/` directory for reuse by `task start-native`. Distribute that
directory and the executable together with any adjacent
GraalVM runtime libraries (`libmanagement_ext.dylib` on the measured macOS ARM64
build). The executable is stripped and ad-hoc signed on macOS.
The native Linux and PostgreSQL paths have not been validated here.

The checked-in reachability metadata registers the SQLite FFM call signatures,
including the three variadic `sqlite3_config` forms, JSON model reflection and
native resources. Dependency metadata comes from the GraalVM metadata repository.
Netty/Vert.x reflection and JNI registrations were collected with the GraalVM
tracing agent while running `verify-executable.py` on the JDK 25 fat JAR; they
supplement dependency metadata, including kqueue's native entry points.
fastjson2 2.0.65 remains the codec for both artifacts. Its upstream build-time
initialization captures private JDK String lambdas that GraalVM cannot compile;
the native build instead initializes fastjson2 at runtime so its fallback paths
can run. This setting does not change the JVM build.

To return to the JDK 26 fat JAR, reset `JAVA_HOME` to JDK 26 and run
`./gradlew test buildJit`. `task start` runs the cached JVM; `task start-native` runs the
native executable. `task test-native` builds and checks the packaged native server.

The measured macOS ARM64 toolchain archive is
[`graalvm-jdk-25i3-25.0.4.1_macos-aarch64_bin.tar.gz`](https://gds.oracle.com/download/graal/25i3/archive/graalvm-jdk-25i3-25.0.4.1_macos-aarch64_bin.tar.gz),
SHA-256 `8411c28344f47726c433a2fbf0fa399c199531802d458a917d4a05b106043141`.

Reproduce the complete comparison in this order, running one measurement at a time:

```fish
# GraalVM/JDK 25 selected as above; downloads resolved before timing.
python3 measure-build.py ../results/kotlin-complete/native-build --native

# Switch the default JVM back to JDK 26; keep JEXTRACT_HOME unchanged.
set -gx JAVA_HOME (brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home
python3 measure-build.py ../results/kotlin-complete/jvm-build
python3 ../measure-debug-build.py ../results/kotlin-complete/debug-build --language kotlin
./gradlew test buildJit
python3 measure-runtimes.py ../results/kotlin-complete/runtimes \
  --build-results ../results/kotlin-complete
```

The native build runner leaves the last of three clean optimized executables in
`build/`. JVM clean-build timing uses a disposable source copy and includes the
HotSpot cache training/assembly, alongside SQLite C, jextract, Kotlin/Java, the
fat JAR and native-library staging. The shared debug column measures three real
public-type-renaming rebuilds of JVM development classes; the GraalVM row labels
this development workflow `(JVM)`. Native-image incremental rebuilds are not used
as debug-build measurements.

The runtime runner alternates three fresh JVM/GraalVM processes and databases.
Each serves `/posts` for 10 seconds, then `/echo` for 10 seconds, at 50 connections
and no HTTP warm-up. RPS, p50 and CPU are medians; RSS is the maximum 100 ms sample.
It then measures seven startups per runtime, including the first echo and first
committed post, and records artifact/source hashes, sizes, commands and database
verification. Use fresh result directories. Current results live under
`results/kotlin-complete-2026-09-18/` at the repository root (gitignored).

The complete **2026-09-18** rerun measures **8.9K writes/sec** and **306.2K echo
RPS**, median p50 **5.109 / 0.131ms**, maximum sampled RSS **104.6 / 90.1 MiB**,
and median CPU **116% / 341%** for writes/echo. Native write runs ranged from
8.77K–8.95K, and echo from 306.0K–307.8K. The native write bottleneck remains
unprofiled; no JVM measurements are substituted for native results.

The seven-start median is **29.62ms** (**28.64–30.63ms**), with first echo and
first committed post at **31.11 / 32.42ms** from launch. An earlier paired trial
of the same source measured 630.98ms with per-process library extraction versus
31.34ms with staged libraries. No database initialization is deferred past
listening. The final clean builds took **161.69, 146.05 and 149.49 seconds**,
a **149.49s** median including native staging, stripping and signing.

The final stripped executable is **92,405,360 bytes (88.12 MiB)**. Together with
the **73,984-byte** runtime library and **1.75 MiB** of staged native libraries,
the fast-launch distribution is **89.95 MiB**. It needs no HotSpot cache or
installed JDK. The **1.09s (JVM)** debug cell is the freshly measured development
rebuild shared by both release modes.

The packaged checks pass echo, JSON/schema/email validation, Unicode/NUL/large
strings, committed responses, case-insensitive user reuse, forced rollback and
recovery, concurrent writes, shutdown, integrity and AUTOINCREMENT accounting.
All final HTTP runs also pass their response and database checks. Full samples,
commands, source/artifact hashes and build logs are under
`results/kotlin-complete-2026-09-18/` (gitignored).

## Bundled SQLite

[Tailscale compiles its bundled SQLite C source into each Go target binary](https://github.com/tailscale/sqlite/blob/acbe2dadf94c/cgosqlite/cgosqlite.go).
It does not use Apple's system SQLite or choose from a set of prebuilt SQLite
binaries at runtime. This JVM build follows the same source-bundling approach,
producing a `.dylib` on macOS or `.so` on Linux and loading it through FFM.

The JAR contains the library for the build machine's OS and CPU. Rebuild on each
target platform; it is not a universal multi-platform JAR. Standalone runtime
extraction is automatic and cleaned up on normal JVM exit. `buildJit` stages the
same libraries once at build time so `task start` can reuse them. macOS ARM64 was tested here; the
Linux and x86-64 build paths have not been exercised on this machine. A compatible
external library can be selected for experiments with
`-Dsqlite.library=/absolute/path/to/libsqlite3.dylib`.

Packaging alone does not speed up SQLite. The benefits come from a controlled
version, compiler options and connection setup. The previous JVM build already
used Homebrew SQLite 3.53.4, not Apple's system SQLite. The new build uses SQLite's
[recommended compile options](https://sqlite.org/compile.html), including
`THREADSAFE=2`, disabled memory-status accounting and explicit initialization.
It retains the database features used by the shared schema. Exact flags and the
source checksum are in [the common configuration](../db/sqlite-config.json),
built by [the shared helper](../db/prepare-sqlite.py). All three engines report
identical SQLite compile options.

A roughly 4 MiB process-wide [SQLite page-cache pool](https://sqlite.org/malloc.html)
reduces allocator calls, with SQLite's normal allocation fallback when it fills.
Prepared statements and UTF-8 binding storage are reused on the owning thread.
`SQLITE_STATIC` bindings are cleared before that storage is reused or released.
Results are read from SQLite, including all five returned columns; the request
body is not substituted for the persisted row. Embedded NULs and Unicode survive
the round trip. Failed writes reset statements and roll back before the next job.

## HTTP throughput and RAM

The JVM row uses four rotating rounds with C# JIT/AOT and Zig on **2026-09-18**,
with [randomized valid JSON](../loadgen/README.md): 10 seconds per endpoint,
50 connections, `/posts` then `/echo`, no HTTP warm-up, fresh processes and databases.
The JVM retains staged libraries, the HotSpot cache, four HTTP event loops and one writer.
GraalVM retains its earlier three-run static-payload measurements and is not directly comparable.

| Runtime | Writes RPS | Echo RPS | Write / echo p50 | Write / echo peak RSS | Write / echo CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| JVM | 28.8K | 365.0K | 1.259 / 0.093ms | 176.3 / 434.7 MiB | 183% / 380% |
| GraalVM | 8.9K | 306.2K | 5.109 / 0.131ms | 104.6 / 90.1 MiB | 116% / 341% |

RPS, p50 and CPU are medians; RSS is the maximum sample, including native memory
and excluding the load generator. Echo retains allocations from writes.
CPU 100% means one core. Every randomized response had the expected status;
completed writes matched committed rows exactly and passed integrity, foreign-key
and corpus-content checks. Raw samples are in
`results/random-json-remaining-2026-09-18/` (gitignored).

`measure-http.py` supports `--loadgen COMMAND`, `--socket /tmp/another.sock`,
`--native`, and repeatable `--jvm-arg=-Dname=value` overrides.
The default load generator is the shared Vegeta driver.

## Binary startup

On 2026-09-18 the final cached JVM starts in **183.04ms** median across seven fresh
processes (**179.30–187.10ms**), down from the reproduced **1,204.91ms** baseline.
Every launch opens SQLite and prepares its statements before the post-bind
`Listening on…` log. Median launch-to-first-echo is **223.44ms**; the following
first committed post completes at **239.88ms**. The respective ranges are
218.71–226.50ms and 233.92–241.95ms. Readiness includes the real database path.

JFR native-method samples and `-Xlog:class+init=info` exposed three gaps in
`NativeLibraries.load`: about **254ms for Netty kqueue**, **212ms for Netty DNS**,
and **225ms for SQLite** in the instrumented baseline. Reusing the same native
files, instead of extracting fresh temporary copies each process, reduced the
unmodified baseline's five-start median to **485.38ms**. The remaining class
loading/linking work is reduced by the
[JDK HotSpot AOT cache](https://docs.oracle.com/en/java/javase/26/docs/specs/man/java.html).
No JIT tier limit, alternate GC, disabled verification or deferred database
initialization is used. Jackson was also removed; fastjson2 now supplies the
Vert.x JSON SPI. Removing Jackson alone was not the main startup improvement.

`buildJit` creates the cache through `verify-executable.py`, exercising echo,
committed writes, validation and rollback against a disposable database. Cache
creation and native staging happen during the build and are excluded from startup.
The **15.07 MiB** JAR, **40.97 MiB** cache and **1.75 MiB** native directory total
**57.80 MiB**, excluding the installed JDK. The standalone JAR remains available
without the cache, with slower extraction and class loading.

```fish
./gradlew test buildJit
python3 measure-startup.py ../results/kotlin-startup --modes bundled libraries cached
# Profile a separate run; these instrumented timings are not benchmark results.
python3 measure-startup.py ../results/kotlin-startup-profile --modes bundled --rounds 1 --profile
```

All times start immediately before `Popen`; the first echo and post follow the
post-bind log, and the post is checked for visibility in a separate connection.
Each launch uses a fresh migrated database, with no HTTP warm-up or filesystem
cache flushing. This measures fresh processes with warm filesystem caches, not
cold-disk startup. The root tables use the final complete rerun's seven-start measurements.
Earlier seven-run checks measured 190.86–224.05ms; a separate run overlapping
background compilation measured 343.41ms and is retained as contention data.

The final commands, logs, samples and artifact hashes are in
`results/kotlin-complete-2026-09-18/runtimes/jvm-startup/summary.json` and
`graalvm-startup/summary.json`. Exploratory comparisons and JFR recordings are
retained in `results/kotlin-startup-2026-09-18/` (all gitignored).

## Library comparisons

These measurements select among the implementations tested here; they do not
establish a universally fastest JVM framework, JSON library or SQLite binding.
All load tests and microbenchmarks were run sequentially on the same machine.
The figures below record the library-selection experiments; their temporary
comparison harness is not part of the project.

### HTTP application frameworks

Vert.x Web, Armeria and Javalin use their own routing and body APIs with the same
fastjson2 adapter and echo payload. Four event-loop/selector threads, 50 Unix-socket
connections, a 5-second warm-up and two 10-second measurements per framework.
These echo-only comparisons differ from the cold-process root table.

| Application framework | Version | Median echo RPS | Median p50 | Peak measured RSS |
| --- | --- | ---: | ---: | ---: |
| Vert.x Web | 5.1.8 | **355.0K** | 0.098ms | 388.1 MiB |
| Armeria | 1.41.1 | 242.5K | 0.141ms | 407.0 MiB |
| Javalin / Jetty | 7.2.3 / 12.1.13 | 124.5K | 0.193ms | 766.4 MiB |

RAM is the maximum across the two measured windows; warm-up samples are excluded.
Armeria varied from 221.0K to 264.0K, still below Vert.x's 346.9–363.1K. These
are small local trials, not an exhaustive tuning study of every JVM framework.

### JSON

JMH 1.37, two forks, three 1-second warm-up iterations and five 1-second measurement
iterations per fork. Each operation parses and serializes the same UTF-8 payload
using the same public-field model, returning the resulting byte array. DSL-JSON
uses generated adapters; instances and reusable writers are initialized outside
the timed operation. Mean time per operation, lower is better:

| JSON library | Version | ns/parse + serialize |
| --- | --- | ---: |
| fastjson2 | 2.0.65 | **84.1** |
| Apache Fory JSON | 1.7.3 | 106.3 |
| DSL-JSON | 2.0.2 | 115.5 |
| Jackson 2 | 2.22.2 | 287.7 |
| Jackson 3 | 3.2.2 | 371.3 |

The application adds a small typed adapter for Kotlin's immutable request class;
fastjson2 still handles tokenization, UTF-8, escaping and response serialization.
The adapter requires both string fields and skips unknown fields. The microbenchmark
compares library codecs, rather than reproducing all HTTP validation work.

### SQLite

The same on-disk schema, two writes and individual WAL/NORMAL transaction, reading
all five returned columns. Prepared statements, one owning thread, no HTTP, same
JMH iteration/fork settings as above. Engines are all SQLite 3.53.4; Xerial uses
its own bundled build, so this compares complete library/engine configurations,
not just the intrinsic cost of JNI versus FFM.

| SQLite path | Mean µs/transaction | JMH error (99.9% CI half-width) |
| --- | ---: | ---: |
| Direct Panama, bundled optimized SQLite | 16.96 | ±0.61 |
| Previous general Panama wrapper, same bundled engine | 16.65 | ±0.34 |
| Xerial JDBC 3.53.4.0 / JNI | 29.94 | ±7.87 |

The two FFM paths are close. The HTTP improvement also comes from replacing the
coroutine request/reply rendezvous with a bounded executor and spreading HTTP
work across four event loops. The direct store reuses binding memory and explicitly
finishes `RETURNING` before commit; the old general wrapper resets after the first
row. The JNI result is noisier, but provides no reason to replace FFM here.

## Build timings and validation

```fish
python3 measure-build.py ../results/kotlin-build
python3 ../measure-debug-build.py ../results/kotlin-debug-build --language kotlin
```

The first script supplies the **21.15s clean JVM release** median from three
`clean buildJit` runs (**21.204, 21.102 and 21.145 seconds**). This includes native
SQLite C compilation, jextract, Kotlin/Java compilation, the fat JAR, native
staging and verified HotSpot cache training/assembly. Additional release rebuild
samples (**4.791, 4.783 and 4.771 seconds**) are retained separately and are not
the debug column. GraalVM's three clean builds have a **149.49s** median.

The debug runner supplies the **1.09s warm development rebuild** median, from
**1.101, 1.086 and 1.060 seconds**, on **OpenJDK 26.0.2.1**. It runs
`./gradlew --offline --no-build-cache -Pkotlin.incremental=true classes`, the
compilation/resources prerequisite of `application run`. The task graph is checked
with `run --dry-run`; no server starts and no JAR is packaged. JVM classes retain
line-number and local-variable debug metadata, verified with `javap -c -l`.
Both release-mode rows use this development workflow; GraalVM labels it `(JVM)`.

After an excluded warm-up and no-change control, each debug sample renames the
public `NewPost` type and its consumers across five Kotlin files. Gradle and
Kotlin daemons, incremental state, dependencies, generated bindings and native
SQLite remain warm. Changed class hashes verify recompilation of the type and
its callers. JVM clean-build and debug measurements use disposable source copies;
unit tests, downloads, copying and edits are excluded from timing. Cache training
and its HTTP verification are included in `buildJit`. Native clean builds run in
this project without source edits and leave the final executable available.
All build samples, patches and task logs are under
`results/kotlin-complete-2026-09-18/{jvm-build,native-build,debug-build}/`.

Tests cover the HTTP endpoints and validation, JSON escaping and invalid schemas,
large strings and embedded NULs, case-insensitive user reuse and AUTOINCREMENT gaps,
rollback/recovery, queued writes and orderly shutdown, and missing databases.
Current HTTP/RSS and build measurements are under `results/kotlin-complete-2026-09-18/`.
Earlier library-comparison JMH output remains under `results/jvm-optimization/`
(all gitignored).
