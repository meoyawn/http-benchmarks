# Go / Hertz / native SQLite

`POST /posts` parses and validates JSON, inserts or reuses a user, inserts a post,
and returns the persisted row after committing. `POST /echo` parses and serializes
the two request fields. Both endpoints use HTTP/1.1 over a Unix domain socket by
default. This directory was previously named `go-net`.

## Versions

Updated on 2026-09-17. The HTTP and SQLite choices come from the local comparisons
below, including alternatives to the original FastHTTP and gosqlite libraries.

| Component | Version |
| --- | --- |
| [Go](https://go.dev/doc/devel/release) | 1.27.1 |
| [Hertz](https://github.com/cloudwego/hertz) | 0.10.6 |
| [CloudWeGo netpoll](https://github.com/cloudwego/netpoll) | 0.7.5 |
| [Tailscale SQLite](https://github.com/tailscale/sqlite/tree/acbe2dadf94c) | `v0.0.0-20260910121735-acbe2dadf94c` |
| SQLite, bundled in Tailscale's C bindings | 3.53.4 |
| [goccy/go-json](https://github.com/goccy/go-json) | 0.10.6 |
| golang.org/x/sync | 0.23.0 |
| oha, through pkgx | 1.16.0 |

Direct and transitive versions are pinned in [go.mod](go.mod) and [go.sum](go.sum).
Hertz also depends on Sonic internally; application request/response JSON uses
goccy, which won this machine's small-payload comparison. The SQLite module has no
tagged release, so it is pinned to the latest upstream commit available at the
time of the update. Its native `cgosqlite` API avoids `database/sql` pooling and
reflection for this single-writer workload.

## Build and run

Requires Go 1.27.1 and a C compiler (`CGO_ENABLED=1`, Go's native-build default).
SQLite is compiled into the binary; no system SQLite headers or dynamic library
are needed to build or run it. The `sqlite3` CLI below is only for the initial
shared migration.

```sh
cd go
go mod download
go test -race ./...
env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -o bench .

# Once, for a fresh database; reuse an already migrated database as-is.
sqlite3 -bail ../db/db.sqlite < ../db/migrations/001_init.up.sql
./bench
```

`task start` builds the same binary before running it. Optional flags:

```sh
./bench -db /absolute/path/to/migrated.sqlite -socket /tmp/go-benchmark.sock
./bench -port 8080
```

An existing socket/file is not overwritten. Stop with Ctrl-C or SIGTERM; the
server drains requests before stopping the writer and closing SQLite. A missing
database or migration fails startup before HTTP begins serving.

SQLite uses WAL, `synchronous=NORMAL`, foreign keys, a 10-second busy timeout and
the explicit 1,000-page WAL autocheckpoint. Its optimized compiler options, page
pool, cache and temporary-storage settings match [Kotlin and OCaml](../db/README.md). One writer goroutine reuses prepared
`BEGIN IMMEDIATE`, user insert, post insert with `RETURNING`, `COMMIT` and
`ROLLBACK` statements. A bounded queue and pooled reply objects avoid allocating
channels per request. Every request still performs its own two SQL writes and
commit; there is no transaction batching or cache of users/posts. Failed writes
reset statements and roll back before the next request.

Email validation uses the same precompiled, whole-string ASCII regex as Kotlin
and OCaml: `^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`. Content must be
nonempty. This deliberately simple rule replaces `net/mail.ParseAddress`;
[shared test cases](../testdata/email-validation.json) keep all three aligned.

Tests use temporary databases and actual Unix-socket HTTP connections. They cover
JSON parsing and validation, response and persisted content (including embedded
NULs, escaping and Unicode), case-insensitive user reuse, concurrent writes,
rollback/recovery, foreign keys, shutdown with a blocked write, and startup
failures.

## HTTP throughput and RAM

The final three-run medians are **45.2K writes/sec** and
**297.9K echo RPS**, measured in rotating order with Kotlin
and OCaml under the shared SQLite configuration. Peak RSS is
**73.3 / 74.6 MiB** respectively.
See the [root tables](../README.md). Individual samples are retained locally in
`ocaml/measurements.json` at the repository root (gitignored).

With the server stopped, run from this directory:

```sh
env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -o bench .
python3 measure-http.py ../results/go-http
```

The script creates a fresh database from the shared migration, starts the binary,
then runs the root README's exact payloads with `pkgx oha`, 50 connections and
10 seconds each: `/posts` followed by `/echo`, without an HTTP warm-up. It adds
only `--output-format json` to capture the measurements. Use `--oha oha` if oha is
installed directly, or `--socket /tmp/another.sock` to select another socket.
Choose a fresh output directory for each run; an existing database is rejected.

RAM is the server process's maximum sampled resident set size (RSS), queried with
`ps` approximately every 100 ms during each workload. It includes Go, native code
and SQLite memory, excludes oha, and is a sampled peak rather than the exact
kernel high-water mark. The echo sample uses the same process after `/posts`, so
it includes retained database memory. RSS is not Go heap size or macOS physical
footprint.

Each run saves oha JSON, server logs, individual RSS samples, endpoint summaries
and database checks. All completed requests must have the expected status. oha
aborts up to 50 in-flight requests at its 10-second deadline; committed posts may
therefore exceed received HTTP 201 responses by up to 50. Integrity, foreign keys,
post contents and row counts are checked after a clean server exit.

## Binary startup

Five fresh processes, alternating with Kotlin, reached Hertz's post-bind
`HTTP server listening on address=…` log in a median **9.52ms**.
Filesystem caches were not flushed.
The application's earlier `Listening on…` line precedes binding and is not used
as the readiness marker. An echo request confirms readiness after timing ends.

With both artifacts built and `JAVA_HOME` pointing to JDK 26 for the Kotlin runs:

```sh
python3 ../measure-startup.py ../results/startup
```

The measurement starts immediately before spawning the built binary and includes
runtime, SQLite initialization and socket binding. Builds and database migrations
are excluded. The [root tables](../README.md) repeat this startup time for both
endpoints; the script records every sample, log, command and artifact hash.

## Build timings

```sh
python3 measure-build.py ../results/go-build
python3 ../measure-debug-build.py ../results/go-debug-build --language go
```

The first script supplies the **21.11s clean release** median using
`env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -o bench .`.
Each of three clean samples uses a separate empty `GOCACHE`, including compilation of the
standard library, dependencies, bundled SQLite C source, application, and linking.
Its additional release rebuild samples are not used in the root tables.

The second script supplies the **1.23s warm debug rebuild** median, remeasured on
2026-09-18. After an excluded warm-up and no-change control, each of three samples
renames the exported `NewPost` type in `main.go` and its consumers in `store.go`.
It runs `go build -gcflags='all=-N -l' -o bench-debug .`, disabling Go optimizations
and inlining while retaining debug information. The warm cache retains dependencies
and optimized SQLite C; the application is recompiled and linked. Changed binary
hashes confirm that every sample rebuilt.

Both scripts use disposable source copies and separate caches. Tests, downloads,
setup and source edits are excluded from timing. Raw debug rebuild samples and
patches are in `results/debug-rebuild-2026-09-18/` at the repository root
(gitignored); the earlier clean release samples are in the local generated
`ocaml/measurements.json` report.

## Why these libraries

These library-selection measurements were collected during the update. They are
specific to this Apple M1 Pro, macOS 26.4, Go 1.27.1 and this workload; they do not
establish a universally fastest HTTP or SQLite library.

### HTTP

Three 10-second Unix-socket echo runs per candidate, in rotating order, with the
same JSON library, payload and 50 connections. Medians:

| HTTP implementation | Version | RPS | p50 latency |
| --- | --- | ---: | ---: |
| Hertz / netpoll | 0.10.6 / 0.7.5 | 297.4K | 0.150ms |
| net/http | Go 1.27.1 | 237.6K | 0.155ms |
| Fiber | 3.5.0 | 231.3K | 0.190ms |
| FastHTTP | 1.74.0 | 229.1K | 0.191ms |

These isolate HTTP/JSON handling; the root tables measure the complete application.
[Gnet](https://github.com/panjf2000/gnet) is a networking engine, not a complete HTTP implementation, so it would need a
separate HTTP parser and protocol handling to run this comparison.

### SQLite and JSON

The SQLite measurements used the shared schema, a fresh on-disk WAL database,
`synchronous=NORMAL`, foreign keys, prepared statements and one transaction per
operation. Each operation inserted/reused a user, inserted a post, read all five
returned columns and committed. Candidates used their direct APIs; mattn also
disabled its connection mutex because the test had one owner. Each benchmark ran
three times for two seconds, with packages run sequentially.

| SQLite library / direct API | Version | Median µs/transaction | Allocations/op |
| --- | --- | ---: | ---: |
| Tailscale cgosqlite | `acbe2dadf94c` | **19.05** | 1 |
| go-llsqlite/crawshaw | 0.7.0 | 20.02 | 4 |
| eatonphil/gosqlite (original dependency) | 0.10.0 | 20.14 | 5 |
| mattn/go-sqlite3, mutex disabled | 1.14.52 | 20.43 | 19 |
| ncruces/go-sqlite3 | 0.35.5 | 24.75 | 2 |
| zombiezen.com/go/sqlite | 1.4.2, modernc 1.59.0 | 24.83 | 1 |
| modernc.org/sqlite | 1.59.0 | 26.89 | 36 |

These compare the latest available library versions with their supplied engines.
Tailscale, mattn, ncruces and modernc use SQLite 3.53.4; crawshaw bundles 3.53.0,
and the original gosqlite still bundles 3.46.0. Tailscale's combined step/reset
calls and native typed-column access won this workload.

The JSON comparison parses and serializes the echo payload. Its medians were
**151 ns/op** for goccy/go-json, **438 ns/op** for the standard library and
**651 ns/op** for Sonic's standard-compatible configuration.

Local raw results live under `results/2026-09-17-go/` at the repository root and
are gitignored, including dependency audits and HTTP comparison medians.
