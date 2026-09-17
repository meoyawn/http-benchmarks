# HTTP server benchmarks

- HTTP over unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro, running everything on the same machine.

The Kotlin Panama and Kotlin HTTP rows were remeasured on **2026-09-17** with
Homebrew OpenJDK **26.0.2.1**, Kotlin **2.4.20**, Vert.x **5.1.8**, SQLite **3.53.4**,
and `pkgx oha` **1.16.0** on macOS **26.4** (16 GiB RAM). All other rows are historical
and were not rerun. The previous Kotlin results were 53K RPS / 0.9ms for SQLite
and 247K RPS / 0.2ms for echo; these runs alone do not isolate the effect of upgrades.

[Setup and versions](kotlin-vertx-panama/README.md). Raw measurements are saved locally
under `results/2026-09-17-kotlin-vertx-panama/` (gitignored).

Use `pkgx oha` when `oha` is not installed. The commands, duration, payloads, and
socket below are unchanged.

```sh
oha http://localhost/posts --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

| Framework                   | RPS   | p50 latency | Clean build | Incremental build |
| --------------------------- | ----- | ----------- | ----------- | ----------------- |
| Kotlin Vert.x SQLite Panama | 36.2K | 1.175ms     | 3.88s       | 1.98s             |
| Rust Actix-Web              | 51K   | 0.9ms       | —           | —                 |
| C# ASP.NET Core             | 45K   | 1ms         | —           | —                 |
| Zig http.zig                | 43K   | 1ms         | —           | —                 |
| Go FastHTTP                 | 43K   | 1.1ms       | —           | —                 |
| Kotlin Vert.x SQLite JNI    | 40K   | 1.1ms       | —           | —                 |
| JS Bun Hono                 | 21K   | 1.9ms       | —           | —                 |
| Python Blacksheep           | 19K   | 2.5ms       | —           | —                 |
| Elixir Bandit               | 10K   | 4.9ms       | —           | —                 |

Build times are the median wall time of three runs with warm Gradle/Kotlin daemons,
dependencies already downloaded, and the build cache disabled. Clean build runs
`./gradlew --offline --no-build-cache clean shadowJar`, including jextract binding
generation, Kotlin/Java compilation, and fat-JAR packaging. Incremental build runs
`shadowJar` after changing a startup log string in `App.kt`; it recompiles Kotlin
and repackages the JAR. Tests are excluded. `—` means unmeasured.
[Timing script](kotlin-vertx-panama/measure-build.py). Individual timings are saved
locally as `results/2026-09-17-kotlin-vertx-panama/build-times.json`.

## Won't do

- Kotlin Native Ktor: can't listen on unix domain sockets

## Bonus

HTTP POST JSON echo

```sh
oha http://localhost/echo --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "foo@gmail.com" }'
```

| Framework         | RPS    | p50 latency | Clean build | Incremental build |
| ----------------- | ------ | ----------- | ----------- | ----------------- |
| Rust Actix-Web    | 266K   | 0.2ms       | —           | —                 |
| Zig http.zig      | 264K   | 0.2ms       | —           | —                 |
| Kotlin Vert.x     | 173.2K | 0.271ms     | 3.88s       | 1.98s             |
| Go FastHTTP       | 199K   | 0.2ms       | —           | —                 |
| Python Blacksheep | 192K   | 0.2ms       | —           | —                 |
| C# ASP.NET Core   | 190K   | 0.3ms       | —           | —                 |
| JS Bun Hono       | 156K   | 0.3ms       | —           | —                 |
| Elixir Bandit     | 139K   | 0.3ms       | —           | —                 |

The two Kotlin endpoints share one artifact, so the build timings are the same.
The 2026 run used a fresh database and one server: `/posts` first, then `/echo`,
with no separate HTTP warm-up and oha's default 50 connections. Other desktop
applications remained running. All completed requests returned the expected status:
362,017 × HTTP 201 and 1,732,594 × HTTP 200. The SQLite database contained 362,067
posts, one user, and passed integrity and foreign-key checks.
