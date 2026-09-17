# HTTP server benchmarks

- HTTP over unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro, running everything on the same machine.

The Kotlin Panama and Kotlin HTTP rows were remeasured on **2026-09-17** with
Homebrew OpenJDK **26.0.2.1**, Kotlin **2.4.20**, Vert.x **5.1.8**, SQLite **3.53.4**,
and `pkgx oha` **1.16.0** on macOS **26.4** (16 GiB RAM). The previous Kotlin results
were 53K RPS / 0.9ms for SQLite
and 247K RPS / 0.2ms for echo; these runs alone do not isolate the effect of upgrades.

[Setup and versions](kotlin-vertx-panama/README.md). Raw measurements are saved locally
under `results/2026-09-17-kotlin-vertx-panama/` (gitignored).

The Go rows were also remeasured on **2026-09-17**, using **Go 1.27.1**, **Hertz
0.10.6 / netpoll 0.7.5**, **Tailscale SQLite (acbe2dadf94c, SQLite 3.53.4)** and
**goccy/go-json 0.10.6**, with the same machine, OS and oha version. The directory
is now [`go/`](go/). [Setup, dependency comparisons and measurement scripts](go/README.md).
Local raw HTTP/RAM measurements are in `results/2026-09-17-go/queued/`, with build
timings in `results/2026-09-17-go/build/` (gitignored). The historical Go FastHTTP
results were 43K RPS / 1.1ms for SQLite and 199K RPS / 0.2ms for echo; these runs
alone do not isolate upgrade effects. All remaining frameworks retain historical
results and were not rerun.

Use `pkgx oha` when `oha` is not installed. The commands, duration, payloads, and
socket below are unchanged.

```sh
oha http://localhost/posts --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

| Framework                   | RPS   | p50 latency | Peak RAM (RSS) | Clean build | Incremental build |
| --------------------------- | ----- | ----------- | -------------- | ----------- | ----------------- |
| Kotlin Vert.x SQLite Panama | 36.2K | 1.175ms     | —              | 3.88s       | 1.98s             |
| Rust Actix-Web              | 51K   | 0.9ms       | —              | —           | —                 |
| C# ASP.NET Core             | 45K   | 1ms         | —              | —           | —                 |
| Zig http.zig                | 43K   | 1ms         | —              | —           | —                 |
| Go Hertz / Tailscale SQLite | 44.3K | 1.018ms     | 71.1 MiB       | 22.31s      | 1.33s             |
| Kotlin Vert.x SQLite JNI    | 40K   | 1.1ms       | —              | —           | —                 |
| JS Bun Hono                 | 21K   | 1.9ms       | —              | —           | —                 |
| Python Blacksheep           | 19K   | 2.5ms       | —              | —           | —                 |
| Elixir Bandit               | 10K   | 4.9ms       | —              | —           | —                 |

Kotlin build times are the median wall time of three runs with warm Gradle/Kotlin
daemons,
dependencies already downloaded, and the build cache disabled. Clean build runs
`./gradlew --offline --no-build-cache clean shadowJar`, including jextract binding
generation, Kotlin/Java compilation, and fat-JAR packaging. Incremental build runs
`shadowJar` after changing a startup log string in `App.kt`; it recompiles Kotlin
and repackages the JAR. Tests are excluded. `—` means unmeasured.
[Timing script](kotlin-vertx-panama/measure-build.py). Individual timings are saved
locally as `results/2026-09-17-kotlin-vertx-panama/build-times.json`.

Go build times are medians of three `go build -trimpath -o bench .` runs. Clean
builds use a fresh `GOCACHE` each time, compiling the standard library, dependencies,
bundled SQLite C source and application, then linking. Incremental builds use a
warm cache and change a startup log string, forcing application recompilation and
relinking. Downloads and tests are excluded; a temporary source copy leaves the
working tree and default Go cache untouched. These clean-build definitions differ
between languages. [Timing script](go/measure-build.py).

RAM is the server process's maximum RSS sampled with `ps` approximately every
100 ms during each workload, in MiB (2²⁰ bytes). It includes native SQLite memory
and excludes the load generator. It is a sampled peak, not Go heap size or an
exact high-water mark. The echo run follows `/posts` in the same process and
includes retained database memory. `—` means unmeasured, including other
frameworks' RAM.

## Won't do

- Kotlin Native Ktor: can't listen on unix domain sockets

## Bonus

HTTP POST JSON echo

```sh
oha http://localhost/echo --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "foo@gmail.com" }'
```

| Framework         | RPS    | p50 latency | Peak RAM (RSS) | Clean build | Incremental build |
| ----------------- | ------ | ----------- | -------------- | ----------- | ----------------- |
| Rust Actix-Web    | 266K   | 0.2ms       | —              | —           | —                 |
| Zig http.zig      | 264K   | 0.2ms       | —              | —           | —                 |
| Kotlin Vert.x     | 173.2K | 0.271ms     | —              | 3.88s       | 1.98s             |
| Go Hertz          | 294.5K | 0.152ms     | 72.9 MiB       | 22.31s      | 1.33s             |
| Python Blacksheep | 192K   | 0.2ms       | —              | —           | —                 |
| C# ASP.NET Core   | 190K   | 0.3ms       | —              | —           | —                 |
| JS Bun Hono       | 156K   | 0.3ms       | —              | —           | —                 |
| Elixir Bandit     | 139K   | 0.3ms       | —              | —           | —                 |

Each implementation's two endpoints share one artifact, so their build timings
are the same. The 2026 Kotlin run used a fresh database and one server: `/posts`
first, then `/echo`,
with no separate HTTP warm-up and oha's default 50 connections. Other desktop
applications remained running. All completed requests returned the expected status:
362,017 × HTTP 201 and 1,732,594 × HTTP 200. The SQLite database contained 362,067
posts, one user, and passed integrity and foreign-key checks.

The Go run used the same sequence, fresh database and default 50 connections.
All completed requests returned the expected status: **442,709 × HTTP 201** and
**2,945,839 × HTTP 200**. The database contained **442,759 posts** and one user;
integrity, foreign-key and stored-content checks passed. oha cancels in-flight
requests at the 10-second deadline, accounting for the 50 additional committed
posts. Other desktop applications remained running during these local measurements.
