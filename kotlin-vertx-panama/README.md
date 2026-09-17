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
| Jackson, retained for Vert.x configuration and the optional PostgreSQL path | 2.22.2 |
| [SQLite](https://sqlite.org/download.html), compiled from pinned source | 3.53.4 |
| [jextract](https://jdk.java.net/jextract/) | 25-jextract+2-4 |
| [Gradle](https://gradle.org/releases/) / [Shadow](https://plugins.gradle.org/plugin/com.gradleup.shadow) | 9.7.1 / 9.6.1 |
| Dependency updates plugin | 0.64.0 |
| JUnit / AssertJ | 6.1.3 / 3.27.7 |
| oha, through pkgx | 1.16.0 |

jextract's bundled Java 25 runs the generator only. Compilation, tests and the
server use Java 26 and its stable Foreign Function & Memory API; no preview flags.
The production build contains the selected stack only; experimental comparison
sources, dependencies and Gradle tasks are not included.

## Build and run

Requires JDK 26, jextract 25, Python 3 and a C compiler. On macOS Apple Silicon,
with Homebrew OpenJDK 26 and Xcode Command Line Tools already installed, using fish:

```fish
cd kotlin-vertx-panama
set -gx JAVA_HOME (brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home
set -gx JEXTRACT_HOME "$PWD/.tools/jextract-25"

mkdir -p .tools
curl -fL 'https://download.java.net/java/early_access/jextract/25/2/openjdk-25-jextract+2-4_macos-aarch64_bin.tar.gz' -o .tools/jextract.tar.gz
echo '3dd1dd1bde059d271739e2cc2290c64f93f85488c86c01e566c0e374eece798f  .tools/jextract.tar.gz' | shasum -a 256 -c -
tar -xzf .tools/jextract.tar.gz -C .tools

./gradlew test shadowJar

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

Without Task, run from this directory:

```fish
"$JAVA_HOME/bin/java" -server -XX:+PerfDisableSharedMem --enable-native-access=ALL-UNNAMED \
  -Dhttp.socket=/tmp/benchmark.sock -jar build/libs/kotlin-vertx-panama-1.0-all.jar
```

The default database is `../db/db.sqlite`. Override it with
`-Ddb.path=/absolute/path.sqlite` before `-jar`. `-Dhttp.workers=4` is the default;
no heap-size or garbage-collector override was used in the published results.
An existing socket is rejected, and the database must already be migrated.
Stop with Ctrl-C or SIGTERM to close the HTTP server and drain the writer queue.
The optional PostgreSQL path remains available with `-Ddb.backend=postgres`;
it was not benchmarked in this update.

## Bundled SQLite

[Tailscale compiles its bundled SQLite C source into each Go target binary](https://github.com/tailscale/sqlite/blob/acbe2dadf94c/cgosqlite/cgosqlite.go).
It does not use Apple's system SQLite or choose from a set of prebuilt SQLite
binaries at runtime. This JVM build follows the same source-bundling approach,
producing a `.dylib` on macOS or `.so` on Linux and loading it through FFM.

The JAR contains the library for the build machine's OS and CPU. Rebuild on each
target platform; it is not a universal multi-platform JAR. Runtime extraction is
automatic and cleaned up on normal JVM exit. macOS ARM64 was tested here; the
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

The [root tables](../README.md) report three rotating fresh-process runs with Go
and OCaml: 10 seconds per endpoint, 50 connections, `/posts` then `/echo`, no HTTP
warm-up. Kotlin's medians are **44.5K writes/sec** and
**373.6K echo RPS**, above the previous 42.7K / 364.6K.
Peak sampled RSS is **225.3 / 392.1 MiB** respectively.
RSS includes native memory and excludes oha, sampled approximately every 100 ms.
Echo retains allocations from writes. The local generated `ocaml/measurements.json`
report at the repository root contains all samples and integrity/content/foreign-key
checks (gitignored).

```fish
# Both application artifacts must be built before the comparison.
./gradlew shadowJar
# In ../go: env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -o bench .
python3 measure-comparison.py ../results/jvm-comparison

# One Kotlin process, with a fresh database:
python3 measure-http.py ../results/kotlin-http
```

Each script rejects an existing output database. `measure-http.py` accepts
`--oha oha` when installed directly, `--socket /tmp/another.sock`, and repeatable
`--jvm-arg=-Dname=value` overrides. Its default is `pkgx oha`. All completed requests
must have the expected status. oha cancels in-flight requests at the deadline, so
up to 50 extra posts per write run may commit without a received HTTP 201.
The script checks row counts, stored content, user ID sequence, integrity and
foreign keys after shutdown. Every final run passed.

## Binary startup

The built JAR reached its first `Listening on…` log in a median **1.209s**
across five fresh JVM processes, alternating with Go. The marker is
emitted after Vert.x successfully binds the socket. Timing includes JVM and native
library loading, database initialization and listening, with builds and database
migration excluded. There is no HTTP warm-up or filesystem cache flushing; an
echo request verifies readiness after the timer stops.

With both application artifacts built and `JAVA_HOME` pointing to JDK 26:

```fish
python3 ../measure-startup.py ../results/startup
```

The [root tables](../README.md) show the same startup time for both endpoints.
Logs, commands, individual samples and artifact hashes are saved with the summary.

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
```

This records three clean `clean shadowJar` builds and three incremental `shadowJar`
builds. Each incremental sample changes a startup log string in `App.kt` in a
disposable source copy, preserving the working tree and runnable artifact.
Warm Gradle/Kotlin daemons, offline dependencies and `--no-build-cache` are used.
Clean timing includes native SQLite C compilation, jextract, Kotlin/Java compilation
and fat-JAR packaging; tests and downloads are excluded. The root tables report
medians. Adding native compilation makes clean builds more expensive than the
previous version that linked a preinstalled library.

The measured medians are **18.21s clean** and
**2.22s incremental**. Every sample is retained in the local generated
`ocaml/measurements.json` report at the repository root (gitignored).

Tests cover the HTTP endpoints and validation, JSON escaping and invalid schemas,
large strings and embedded NULs, case-insensitive user reuse and AUTOINCREMENT gaps,
rollback/recovery, queued writes and orderly shutdown, and missing databases.
Raw HTTP/RSS logs, JMH JSON and build timings from this update are saved locally
under `results/jvm-optimization/`; the final shared-configuration measurements
are under `results/ocaml-2026-09-17/` (both gitignored).
