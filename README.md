# HTTP server benchmarks

- HTTP over Unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro, 16 GiB RAM, macOS 26.4; server and load generator on the same machine.
The Kotlin Panama and Go rows were remeasured on **2026-09-17** using `pkgx oha`
**1.16.0**, with three fresh server processes and databases per language, alternating
execution order. Tables show median RPS and median p50; peak RAM is the maximum
sampled RSS across the three runs. Other desktop applications remained running.
All remaining frameworks retain historical results and were not rerun.

Kotlin uses **OpenJDK 26.0.2.1**, **Kotlin 2.4.20**, **Vert.x Web 5.1.8**,
**fastjson2 2.0.65** and bundled **SQLite 3.53.4** through Panama FFM. Four HTTP event
loops share one SQLite writer. Go uses **Go 1.27.1**, **Hertz 0.10.6 / netpoll 0.7.5**,
**goccy/go-json 0.10.6** and **Tailscale SQLite acbe2dadf94c / SQLite 3.53.4**.
The Go implementation was unchanged for this comparison.

Both execute the same user insert and post insert with `RETURNING`, one
`BEGIN IMMEDIATE` transaction and commit per request, WAL and `synchronous=NORMAL`.
Kotlin preserves user ID allocation and sends each response immediately after
its commit; there is no transaction batching, response grouping or user cache.

Kotlin's write median is **2% below Go**, with overlapping observed ranges
(41.5–44.6K versus 43.3–44.0K). Its echo median is **27% faster**, at the cost of
higher RAM usage. The previous recorded Kotlin rows were 36.2K writes/sec and
173.2K echo requests/sec; a fresh pre-optimization control in this session measured
39.7K and 164.1K. The older historical **53K writes/sec** result was not reproduced.
These comparisons do not isolate the effects of individual upgrades or settings.

[The Kotlin setup, library comparisons and measurement scripts](kotlin-vertx-panama/README.md)
explain the framework, JSON and native-library choices.
[The Go setup and comparisons](go/README.md) document its existing stack.
Raw measurements are saved locally under `results/jvm-optimization/final-comparison/`
and `results/jvm-optimization/build-final/` (gitignored).

Use `pkgx oha` when `oha` is not installed. The published workload remains
10 seconds, oha's default 50 connections, and the following payload:

```sh
oha http://localhost/posts --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

| Framework                   | RPS   | p50 latency | Peak RAM (RSS) | Binary start | Clean build | Incremental build |
| --------------------------- | ----- | ----------- | -------------- | ------------ | ----------- | ----------------- |
| Rust Actix-Web              | 51K   | 0.9ms       | —              | —            | —           | —                 |
| C# ASP.NET Core             | 45K   | 1ms         | —              | —            | —           | —                 |
| Go Hertz / Tailscale SQLite | 43.7K | 1.031ms     | 71.8 MiB       | 8.73ms       | 22.31s      | 1.33s             |
| Zig http.zig                | 43K   | 1ms         | —              | —            | —           | —                 |
| Kotlin Vert.x SQLite Panama | 42.7K | 1.005ms     | 232.0 MiB      | 1.228s       | 13.86s      | 2.01s             |
| Kotlin Vert.x SQLite JNI    | 40K   | 1.1ms       | —              | —            | —           | —                 |
| JS Bun Hono                 | 21K   | 1.9ms       | —              | —            | —           | —                 |
| Python Blacksheep           | 19K   | 2.5ms       | —              | —            | —           | —                 |
| Elixir Bandit               | 10K   | 4.9ms       | —              | —            | —           | —                 |

Kotlin build times are median wall times of three runs with warm Gradle/Kotlin
daemons, downloaded dependencies and the build cache disabled. Clean build runs
`./gradlew --offline --no-build-cache clean shadowJar`, including **bundled SQLite C
compilation**, jextract binding generation, Kotlin/Java compilation and fat-JAR
packaging. Incremental build runs `shadowJar` after changing a startup log string
in `App.kt`. Tests and downloads are excluded. Native compilation explains the
higher clean-build time than the previous 3.88s version using a preinstalled
SQLite library. [Timing script](kotlin-vertx-panama/measure-build.py).

Go build timings retain the earlier same-day measurements: medians of three
`go build -trimpath -o bench .` runs. Clean builds use a fresh `GOCACHE` each time,
compiling the standard library, dependencies, bundled SQLite C and application,
then linking. Incremental builds use a warm cache and change a startup log string,
forcing application recompilation and relinking. Downloads and tests are excluded;
a temporary source copy preserves the working tree and default Go cache. These
clean-build definitions differ between languages. [Timing script](go/measure-build.py).

Binary start is the median wall time from launching the built executable (Go) or
`java -jar` (Kotlin) to receiving the first post-bind listening log. Five fresh
processes per language were launched in alternating order, each with an already
migrated fresh database. This includes runtime startup, native-library loading,
database initialization and socket binding, but excludes builds and migrations.
The Kotlin marker follows a successful Vert.x listen; Go uses Hertz's transport
log because the application's earlier log precedes binding. An echo request checks
readiness after timing ends. Both endpoints have the same startup time.

Filesystem caches were not flushed. Go's first launch took 441.1ms and the remaining
four took 8.5–9.1ms; Kotlin ranged from 1.207s to 1.255s. These are process-start
measurements, not guaranteed cold-cache startup times. Reproduce them with
`python3 measure-startup.py results/startup` after building both applications and
setting `JAVA_HOME` to JDK 26. [Startup timing script](measure-startup.py).
Raw logs and all five samples are in `results/jvm-optimization/startup-final/`
(gitignored).

RAM is the server process's maximum RSS sampled with `ps` approximately every
100 ms during each workload, in MiB (2²⁰ bytes). It includes native SQLite memory
and excludes the load generator. It is a sampled peak, not managed heap size or
an exact kernel high-water mark. Echo follows `/posts` in the same process and
includes retained database memory. `—` means unmeasured.

## Won't do

- Kotlin Native Ktor: can't listen on Unix domain sockets

## Bonus

HTTP POST JSON echo

```sh
oha http://localhost/echo --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "foo@gmail.com" }'
```

| Framework         | RPS    | p50 latency | Peak RAM (RSS) | Binary start | Clean build | Incremental build |
| ----------------- | ------ | ----------- | -------------- | ------------ | ----------- | ----------------- |
| Kotlin Vert.x     | 364.6K | 0.097ms     | 316.7 MiB      | 1.228s       | 13.86s      | 2.01s             |
| Go Hertz          | 288.0K | 0.152ms     | 73.1 MiB       | 8.73ms       | 22.31s      | 1.33s             |
| Rust Actix-Web    | 266K   | 0.2ms       | —              | —            | —           | —                 |
| Zig http.zig      | 264K   | 0.2ms       | —              | —            | —           | —                 |
| Python Blacksheep | 192K   | 0.2ms       | —              | —            | —           | —                 |
| C# ASP.NET Core   | 190K   | 0.3ms       | —              | —            | —           | —                 |
| JS Bun Hono       | 156K   | 0.3ms       | —              | —            | —           | —                 |
| Elixir Bandit     | 139K   | 0.3ms       | —              | —            | —           | —                 |

Each implementation's two endpoints share one artifact and therefore build timings.
The final Kotlin and Go runs used fresh databases, `/posts` followed by `/echo`,
with no separate HTTP warm-up. All completed requests returned the expected status.
Across three runs, Kotlin returned **1,288,329 HTTP 201** and **10,704,160 HTTP 200**
responses; Go returned **1,310,287 HTTP 201** and **8,740,901 HTTP 200** responses.
All databases passed integrity, foreign-key and stored-content checks. Kotlin's
user AUTOINCREMENT sequence was also checked against the number of committed posts.

oha cancels in-flight requests at its deadline. Kotlin committed 1,288,478 posts
and Go committed 1,310,435 posts, within 50 additional commits per write run.
Each database had one user. Reproduce the alternating comparison with
`python3 kotlin-vertx-panama/measure-comparison.py results/comparison` after building
both applications; choose a fresh output directory.
