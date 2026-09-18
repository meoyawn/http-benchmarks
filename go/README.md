# Go / FastHTTP / native SQLite

`POST /posts` automatically decodes JSON into a struct, validates it, inserts or
reuses a user, inserts a post, and serializes the persisted row after committing.
`POST /echo` automatically decodes and serializes the two request fields.
Both endpoints use HTTP/1.1 over a Unix domain socket by default.

## Versions and selection

Remeasured on 2026-09-18, using all ten available cores of the Apple M1 Pro.

| Component | Version |
| --- | --- |
| Go / `encoding/json/v2` | 1.27.1 |
| [FastHTTP](https://github.com/valyala/fasthttp) | 1.74.0 |
| [Tailscale SQLite](https://github.com/tailscale/sqlite/tree/acbe2dadf94c) | `v0.0.0-20260910121735-acbe2dadf94c` |
| SQLite, compiled into Tailscale's C bindings | 3.53.4 |
| golang.org/x/sync | 0.23.0 |
| Randomized load driver | [Vegeta 12.13.0](../loadgen/README.md) |

The whole-stack library comparison covered Hertz,
FastHTTP and `net/http` with goccy, Sonic and both standard JSON APIs. It included
`net/http` + `encoding/json/v2`, Hertz's netpoll and standard network backends,
poller/buffer tuning, and processor sweeps. Only automatic struct mappers qualify;
GJSON and fastjson are excluded as application codecs.

The longer selection runs favored FastHTTP/v2: **50.7K writes/sec** at two
processors and **335.7K echo RPS** at four. Hertz's standard backend with a 16 KiB
read buffer was close at **50.0K / 333.4K**. FastHTTP also needs no framework patch
and has a smaller binary. These selection samples are distinct from the final
release measurements below. The old 2026-09-17 HTTP-only and JSON microbenchmark
rankings are superseded by this complete-stack comparison; goccy's earlier
151 ns/op result did not establish the fastest eligible HTTP/JSON/SQLite stack.

## Build and run

Requires Go 1.27.1 and a C compiler (`CGO_ENABLED=1`, the native-build default).
SQLite is compiled into the executable; system SQLite headers and a dynamic
SQLite library are unnecessary. The CLI below is only for migration.

```sh
cd go
go mod download
env CGO_CFLAGS='-O3 -DNDEBUG' GOMAXPROCS=2 go test -race ./...
env CGO_CFLAGS='-O3 -DNDEBUG' GOMAXPROCS=4 go test -race ./...
env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -ldflags='-s -w' -o bench .

# Once, for a fresh database; reuse an already migrated database as-is.
sqlite3 -bail ../db/db.sqlite < ../db/migrations/001_init.up.sql
./bench
```

`task start` builds the same stripped executable. The default is `GOMAXPROCS=2`
when that environment variable is unset or empty. Override it for the echo
configuration:

```sh
env GOMAXPROCS=2 ./bench -db /absolute/path/migrated.sqlite -socket /tmp/go.sock
env GOMAXPROCS=4 ./bench -db /absolute/path/migrated.sqlite -socket /tmp/go.sock
./bench -port 8080
```

`GOMAXPROCS` controls Go scheduler processors, not HTTP worker count or an OS
thread limit. Both settings retain one dedicated SQLite writer goroutine. The
same executable and setting handle both endpoints in each run. Two processors
favor serialized writes; four favor echo. Read and idle timeouts remain ten
seconds, keep-alive stays enabled, and FastHTTP's buffer sizes remain at defaults.

An existing socket/file is not overwritten. Ctrl-C or SIGTERM closes the listener,
drains accepted requests, then stops the writer and closes SQLite. Cancellation
during HTTP startup also closes the listener. Missing databases or migrations
fail before HTTP begins serving.

## Persistence and correctness

SQLite uses WAL, `synchronous=NORMAL`, foreign keys, a ten-second busy timeout,
and explicit 1,000-page WAL autocheckpoint. The optimized C compiler options,
page pool, cache and temporary-storage settings match the other implementations'
[shared configuration](../db/README.md). Tailscale's direct `cgosqlite` API avoids
`database/sql` pooling and reflection for this single-writer workload.

One writer reuses prepared `BEGIN IMMEDIATE`, user insert, post insert with five
`RETURNING` columns, `COMMIT` and `ROLLBACK` statements. A bounded queue and pooled
reply objects avoid per-request channel allocation. Every request performs its
own transaction and receives its response after its own commit. There is no
batching, user cache, precomputed response or manual JSON field mapping. Failed
writes reset statements and roll back before the next request. The SQLite writer
and C configuration are unchanged by the HTTP/JSON switch.

Email validation uses the shared whole-string ASCII regex
`^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`; content must be nonempty.
[Shared examples](../testdata/email-validation.json) keep implementations aligned.
JSON uses v2's defaults, including case-sensitive field matching and duplicate-key
rejection; unknown fields are ignored. The benchmark sends the declared lowercase
field names.

Race-enabled tests run at both processor settings. They use temporary databases
and actual Unix-socket HTTP connections, covering malformed JSON/types, validation,
response/persisted content (Unicode, escaping and NUL), case-insensitive user reuse,
concurrent writes, rollback/recovery, foreign keys, blocked-write shutdown, startup
failure and cancellation during startup.

## Final release measurements

Six rotating rounds per configuration on 2026-09-18, measured together with
both Rust and OCaml configurations using the [randomized valid JSON workload](../loadgen/README.md).
Each configuration appears once in every order position, with fresh processes
and databases, 50 concurrent requests, ten seconds of `/posts` then ten seconds
of `/echo`, and no HTTP warm-up. RPS, p50 and CPU are medians; RSS is the largest
100 ms sample. CPU sums all server threads; 100% is one core. Client CPU is
reported separately. Echo retains memory allocated during writes. Startup below
retains the earlier measurement. These are same-host, unpinned Mac measurements.

| `GOMAXPROCS` | Endpoint | RPS | p50 | Peak RSS | CPU | Start + bind |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 2 | `/posts` | 31.2K | 1.222ms | 24.8 MiB | 139% | 9.26ms |
| 2 | `/echo` | 248.9K | 0.171ms | 24.9 MiB | 197% | 9.26ms |
| 4 | `/posts` | 28.6K | 1.372ms | 26.1 MiB | 183% | 8.82ms |
| 4 | `/echo` | 300.3K | 0.117ms | 26.4 MiB | 345% | 8.82ms |

Both configurations share one freshly built executable. No production Go code
or SQL was changed. Every response and database check must pass, including exact
email/content pairs and drained response/commit counts. Current results use a
finite corpus of 65,536 randomly selected pairs, so they are not directly
comparable to historical static-payload results.

From the repository root, after building all three servers and the load driver:

```sh
python3 loadgen/measure.py results/random-json --rounds 6
python3 go/measure-configs.py results/go-http --gomaxprocs 2 4
python3 go/measure-startup.py results/go-startup --gomaxprocs 2 4
```

`task benchmark` in this directory builds the shared load driver and runs Go alone.
All current measurement and
profiling entrypoints use the same randomized workload. The
`results/random-json-2026-09-18/summary.json` report (gitignored) retains
samples and aggregates; full logs and databases are locally under
`results/random-json-2026-09-18/` (gitignored).

Startup uses five alternating fresh processes per setting, timed immediately
before launching the executable until receipt of the post-bind `Listening on…`
log. It includes runtime and SQLite initialization plus UDS binding, excludes
builds/migration, and checks echo readiness afterward. Filesystem caches are not
flushed. Medians are **9.26 / 8.82 ms** for two/four processors.

## Build timings

From the repository root:

```sh
python3 go/measure-build.py results/go-build
python3 measure-debug-build.py results/go-debug --language go
```

The clean release median is **19.87s**, using
`env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -ldflags='-s -w' -o bench .`.
Each of three samples uses a fresh `GOCACHE`, compiling the standard library,
dependencies, bundled SQLite C, application and stripped executable. Dependency
and toolchain downloads are excluded. The extra release rebuilds measured by
that script are not used in the root tables.

The warm debug rebuild median is **1.03s**. After an excluded warm-up and
no-change control, each of three samples renames exported `NewPost` and its
consumers across `main.go` and `store.go` in a disposable source copy. The command
is `go build -gcflags='all=-N -l' -o bench-debug .`: Go optimization and inlining
are disabled, debug information retained, and native SQLite stays cached with
`-O3 -DNDEBUG`. Output hashes confirm every edit causes a rebuild. Source copying,
edits, tests and application startup are excluded. Both configurations share
these build times and the same executable.

## One HTTP server, one JSON implementation, one SQLite engine

The stripped release is **7.78 MiB (8,159,570 bytes)**, including static SQLite,
down from the original Hertz/goccy executable's 10.12 MiB. The optimized symbol
companion and release build metadata confirm:

- One HTTP server: FastHTTP. `net/http` support types used by dependencies do not
  retain `net/http.Server.Serve` as a second server.
- One JSON engine: the standard library. On Go 1.27, `encoding/json` is a v1
  compatibility API over the same v2 engine; it is not a second implementation.
  Tailscale's `expvar` statistics retain that compatibility API. No goccy, Sonic,
  GJSON, jsoniter, segmentio or fastjson implementation is linked.
- One SQLite engine: Tailscale `cgosqlite`, statically linked. There is no second
  Go SQLite driver or dynamically loaded SQLite library.

The production [module manifest](go.mod) has no Hertz or alternate JSON dependency.

Reproduce the binary audit on macOS with the same source and optimized flags:

```sh
env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -o bench-symbols .
python3 audit-binary.py bench-symbols bench
```

The script checks linked server entry points and codec symbols, one SQLite engine,
Tailscale's binding, and native library links. The companion differs only in debug
symbols; the release uses `-ldflags='-s -w'`. Exact bytes, hashes and build metadata
are retained in `results/go-stacks-2026-09-18/release-audit.json` locally (gitignored).

## Profiling writes

From the repository root, use fresh output directories:

```sh
python3 go/profile.py results/go-wall --profiler fgprof --seconds 10
python3 go/profile.py results/go-cpu --profiler cpu --seconds 10
```

The runner builds a disposable source copy with fgprof 0.9.5, exercises `/posts`
at `GOMAXPROCS=2` (override with `--gomaxprocs`), stops cleanly, and verifies the
database. It saves the pprof recording, `top.txt`, commands and artifact hashes.
Production sources and module dependencies stay unchanged. fgprof includes
goroutine waits; its summed wall time is not CPU utilization. Profiled throughput
is excluded from rankings. The [Rust/Go investigation](../rust/write-profile.md)
compares these profiles with native Rust stacks and controlled experiments.

## Historical SQLite selection

These 2026-09-17 direct-API measurements explain retaining Tailscale; the new
HTTP/JSON experiment holds SQLite fixed. They are workload/machine-specific.

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
