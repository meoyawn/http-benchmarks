# Zig HTTP benchmark

Zig **0.16.0**, **std.http.Server + zio 0.17.0**, **yyjson 0.12.0**, and
**zqlite** over the shared **SQLite 3.53.4** engine. Dependencies are pinned by
revision and content hash in [build.zig.zon](build.zig.zon).

The default is **one HTTP coroutine executor and one dedicated SQLite writer**.
`-workers 4` selects the echo configuration. Each configuration keeps the same
executor count for both endpoints, like the two Rust configurations.

## Architecture

The architecture follows [Rust](../rust/): HTTP validates and decodes a typed
`NewPost`, submits to a **bounded 1,024-entry queue**, and asynchronously waits for
its own reply. One native writer thread exclusively owns the SQLite connection
and prepared `BEGIN IMMEDIATE`, user insert, post insert, and `COMMIT` statements.
It reads all five `RETURNING` columns, copies the content before resetting the
statement, checks the final step, commits, and signals that request immediately.
There is no batching, response grouping, user cache, or transaction shortcut.
Failed transactions roll back and the connection remains usable.

zio implements Zig's **std.Io** interface with stackful coroutines and **kqueue**
on this Mac. Each keep-alive connection is a coroutine; waiting for the writer
suspends that coroutine while its executor handles other connections. The writer
uses ordinary thread synchronization. A zio event carries its completion back to
the HTTP coroutine. Request data remains alive through commit even when a client
disconnects or shutdown cancels the connection. SIGINT/SIGTERM cancel the accept
loop, finish accepted writes, join the writer, and remove the owned socket path.
An existing socket is never intentionally unlinked or replaced on startup.

Zig 0.16 has the I/O abstraction, but the installed macOS `std.Io.Evented`
(Dispatch) backend's `netListenUnix` returns `AddressFamilyUnsupported` and its
accept implementation returns `NetworkDown`. zio supplies the working evented
backend. See the [zio author's explanation](https://lalinsky.com/2026/05/11/async-io-in-zig-016-today.html).

JSON parsing and serialization use yyjson's ordinary reader/writer through a
small **comptime struct mapper** in [yyjson_codec.zig](src/yyjson_codec.zig).
The adapter maps fields and ownership; yyjson handles JSON syntax, UTF-8,
escaping, numbers, and encoding. Unknown fields are ignored. JSON Pointer/Patch
utilities are disabled because Zig 0.16's C translator emits invalid unused
locals for those unused inline functions; the reader/writer stay enabled.

zqlite accepts Zig byte slices for SQL and parameter values and returns slices
for text columns, including embedded NULs. Only the database filename needs a
sentinel terminator; Zig process arguments already have one. The application
uses the wrapper for statements, binding and rows. The small runtime module uses
C APIs for shared engine configuration and diagnostics.

All [shared SQLite settings](../db/sqlite-config.json) are read from the embedded
configuration: disabled memory accounting, aligned page pool, `NOMUTEX`, WAL,
NORMAL synchronization, foreign keys, busy timeout, cache/checkpoint settings,
temporary storage and disabled mmap. It links the repository engine instead of
the wrapper's bundled SQLite. SQL uses the same indexed insert/select as Rust;
parameter names replace positional placeholders without changing the work.
An ASCII scanner implements the shared whole-string email rule exactly, checked
against every [shared fixture](../testdata/email-validation.json). Content must
be nonempty; request bodies are limited to 2 MiB.

## Local selection evidence

Measured on this repository's M1 Pro, Zig 0.16.0 `ReleaseFast`, using the shared
65,536-entry randomized Unicode corpus. These are **fastest among the measured
candidates on this workload**, not universal library rankings. Downloads, builds,
tests and corpus generation finish before timed comparisons; loads run serially.

Five rotating rounds of the JSON round-trip microbenchmark (1,048,576 operations
per sample, request-arena reset/reuse, actual typed parse plus serialization):

| JSON library | Median ns / round trip |
| --- | ---: |
| yyjson 0.12.0 | **910.47** |
| serde.zig 1.2.2 | 1,040.96 |
| std.json 0.16.0 | 1,999.51 |

serde uses its allocating decoder because its borrowed-string decoder rejects
escaped strings. All three tested adapters pass malformed-input and Unicode/NUL
round-trip checks. Both alternative codecs remain build options for reproduction.

Five rotating rounds of **131,072 complete file-backed transactions per sample**,
with the same SQLite binary, configuration, SQL, result copies and individual
commits, but without HTTP or JSON in the timed interval:

| SQLite wrapper | Median µs / transaction |
| --- | ---: |
| zqlite `b519eea9b1a2` | **22.456** |
| nDimensional/zig-sqlite `13ae705f63fd` | 22.612 |

The 0.7% difference is effectively a tie. zqlite had the lower median and its
recoverable error API passes the forced-rollback/reuse test. Neither wrapper
requires C-string conversion for bound content. `vrischmann/zig-sqlite`'s old
Zig API was replaced instead of locally maintaining a compiler compatibility fork.

HTTP comparisons use [loadgen](../loadgen/), 50 concurrent requests, five client
processes, three seconds per endpoint, fresh databases and processes, `/posts`
then `/echo`, no warm-up, exact response/commit and corpus checks. Libraries share
the same application/SQLite/JSON code. HTTP executor counts are fixed across both
endpoints. The initial Zap/http.zig sweep tried 1/2/4/8 request threads; Zap's
single-thread reactor leaves external writer replies waiting for its poll timeout
(~1 second p50), so it is not viable for this architecture.

The next six-round rotating sweep compared both finalists with Dusty/zio:

| HTTP stack / configuration | Writes/sec | Echo RPS |
| --- | ---: | ---: |
| http.zig, 1 I/O + 4 request threads | 25,312 | 352,858 |
| Dusty/zio, 1 executor | 25,249 | 205,223 |
| Dusty/zio, 2 executors | 23,546 | 286,570 |
| Dusty/zio, 4 executors | 22,088 | 352,952 |
| Dusty/zio, 8 executors | 20,501 | 308,471 |
| Zap, 2 HTTP threads | 24,091 | 194,341 |

http.zig's ordinary handler occupies a request thread while waiting for the
writer; its network I/O still uses kqueue. Dusty and std.http/zio suspend
coroutines, which matches the requested architecture. A final four-round rotating
comparison held the runtime fixed and replaced Dusty's llhttp-based parser with
`std.http.Server`; std.http preserved write throughput and improved echo.

| HTTP stack / configuration | Writes/sec | Echo RPS |
| --- | ---: | ---: |
| std.http/zio, 1 executor | 25,225 | 231,992 |
| std.http/zio, 2 executors | 23,813 | 307,626 |
| std.http/zio, 4 executors | 22,603 | 382,493 |
| Dusty/zio, 1 executor | 25,248 | 204,835 |

Raw comparisons, build commands, hashes, logs and databases are in the ignored
`results/zig-016-libraries-final/`, `results/zig-016-http/`,
`results/zig-016-async-comparison/`, and `results/zig-016-std-comparison/` directories.
All comparison adapters remain checked in. Dusty `1d7139b80c21`,
http.zig `9b14af98fde5`, and Zap `b12c07dd8cbb` are pinned alongside the selected
zio `54cb18f05d3f`, yyjson and zqlite dependencies.

## Build and verify

From this directory, with the installed `zig version` reporting `0.16.0`:

```sh
task build
task test
# Equivalent:
python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite
python3 zig.py build -Doptimize=ReleaseFast
python3 zig.py build test --summary all
python3 test-http.py --workers 1
python3 test-http.py --workers 4
./zig-out/bin/zig -db ../db/db.sqlite -socket /tmp/benchmark.sock -workers 1
./zig-out/bin/zig -check-config
```

`zig.py` invokes **installed Zig**, not pkgx. On this Apple Silicon installation
it selects SDK 15.4 because the installed SDK 26.5 stubs omit `arm64-macos`.
Only a project-local `.tools/` xcrun shim changes; no system SDK settings change.
Zig 0.16 dependencies need no HTTP or SQLite source patches.

Tests cover the shared email fixtures, malformed JSON, Unicode and embedded NULs,
chunked bodies, all five returned columns, concurrent writes, read-after-commit,
rollback recovery, occupied sockets, the body limit, and **50 suspended writes
while echo remains responsive**. Both selected executor configurations pass.

## Reproduce measurements

Build the [load generator](../loadgen/README.md) first. From the repository root,
use fresh output directories:

```sh
python3 zig/compare.py libs results/zig-libs --rounds 5
python3 zig/compare.py http results/zig-async --rounds 6 --duration 3s \
  --config dusty-1 dusty-2 dusty-4 dusty-8 httpz-4 zap-2
python3 zig/compare.py http results/zig-std --rounds 4 --duration 3s \
  --config std-1 std-2 std-4 dusty-1
python3 loadgen/test-servers.py --config zig zig-4
python3 loadgen/measure.py results/zig-final --config zig zig-4 --rounds 4
python3 zig/measure-startup.py results/zig-start-1 --workers 1
python3 zig/measure-startup.py results/zig-start-4 --workers 4
python3 zig/measure-build.py results/zig-builds
```

Use `-Dhttp=std|dusty|httpz|zap`, `-Djson=yyjson|serde|std`, and
`-Dsqlite=zqlite|ndsqlite` with `python3 zig.py build` to reproduce alternatives.
The defaults select the measured final stack. The comparison scripts install
separate artifacts; rebuild the default before running the shared final runner.

The published final runs use **four rotating rounds, ten seconds per endpoint**,
with the same load methodology as the other randomized rows. `zig` is one
executor; `zig-4` is four. Five independent process starts per setting measure
launch through the post-bind listening log, with an untimed echo readiness check.
Clean release builds have fresh local **and global** Zig compiler caches and
compile shared SQLite, yyjson, runtime/application code, link and strip; compiler
installation and downloaded sources are warm. Debug rebuilds rename public
`NewPost` and all consumers in a disposable copy; three real edits and changed
binary hashes, excluding warm-up and a no-change control. All startup/build
commands, raw samples and definitions are emitted by the checked-in scripts.

## Final measured results

Four rounds on **2026-09-18**; medians except maximum sampled RSS. CPU is server
CPU, with 100% representing one core. The executor count stays fixed for both
endpoints; the dedicated writer adds one native thread.

| Executors | Writes/sec | Write p50 | Write RSS | Write CPU | Echo RPS | Echo p50 | Echo RSS | Echo CPU | Startup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 (default) | **31,823** | 1.201ms | 15.8 MiB | 123% | 230,585 | 0.203ms | 15.9 MiB | 99% | 6.09ms |
| 4 | 26,816 | 1.493ms | 14.9 MiB | 187% | **377,759** | 0.104ms | 15.2 MiB | 329% | 5.84ms |

The same **835,888-byte (0.80 MiB)** stripped release executable serves both
settings. Shared SQLite adds **1,697,632 bytes (1.62 MiB)**: **2.42 MiB combined**,
excluding system libraries. Clean release builds took **34.64 / 34.22 / 34.25s**,
median **34.25s**. Real debug edits took **2.99 / 3.08 / 3.05s**, median **3.05s**;
the excluded no-change control took **0.34s**.

All eight final server runs passed exact response/commit counts, corpus-content,
integrity, foreign-key, timestamp and AUTOINCREMENT checks, then exited cleanly
and removed their sockets. Each configuration also passed the separate HTTP
correctness suite, including 261 verified commits, rollback/recovery and concurrent
writer-wait/echo checks. Startup, load and size measurements have identical
executable hashes.

Raw final evidence is in `results/zig-016-final/summary.json`,
`results/zig-016-start-1/`, `results/zig-016-start-4/`,
`results/zig-016-builds/`, and `results/zig-016-artifact.json` (gitignored).

These remain same-host, unpinned desktop measurements. See the
[root result tables](../README.md) for every metric and comparison limits.
