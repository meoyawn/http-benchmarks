# C# / .NET

.NET SDK **10.0.401**, runtime / ASP.NET Core **10.0.12**, Kestrel minimal APIs,
source-generated **System.Text.Json**, and the shared **SQLite 3.53.4** engine.
All .NET tooling runs through `pkgx dotnet`. These are the latest stable .NET
versions verified for this measurement; preview .NET 11 is not used.

Two measured deployment modes use the same application and SQLite implementation:

- **JIT:** framework-dependent single-file bundle containing the IL application
  and its dependencies, including native SQLite. The separately installed
  .NET/ASP.NET runtime uses its tiered compilation and dynamic PGO defaults.
- **Native AOT:** native executable, optimized for speed and the host CPU. No installed .NET runtime
  is needed.

## Measured results

On the root README's M1 Pro, 2026-09-18. Each mode has three fresh processes
and databases, 50 connections, 10 seconds of writes then 10 seconds of echo,
and no HTTP warm-up. The order rotates; medians are reported except maximum
sampled RSS. Other desktop applications remained running. Both endpoints use
the same two pool workers and three socket I/O threads in each process.

| Mode | Write RPS | Write p50 | Write RSS | Write CPU | Echo RPS | Echo p50 | Echo RSS | Echo CPU | Startup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| JIT | 48.4K | 0.908ms | 168.7 MiB | 320% | 368.8K | 0.116ms | 170.0 MiB | 258% | 166.47ms |
| AOT | 48.2K | 0.906ms | 105.3 MiB | 307% | 349.2K | 0.126ms | 105.9 MiB | 267% | 89.28ms |

Write ranges were 44.1K–48.8K (JIT) and 47.6K–48.7K (AOT); echo ranges were
362.2K–382.0K and 340.6K–350.8K. All three runs, including the slower JIT write
sample, remain in the medians. The variation prevents a confident ordering of
write throughput. The old 45K/190K row is historical, with a different runtime,
validation and measurement protocol.

These performance and startup figures retain the preceding measurements. Only
release size was updated for the bundle. Subsequent timing and throughput
measurements overlapped background video playback and are excluded. Retained
JIT startup and build timings predate bundling and exclude extraction/bundling.

| Mode | Clean release publish | Warm debug rebuild | Release artifact | With SQLite |
| --- | ---: | ---: | ---: | ---: |
| JIT | 15.83s | 0.707s (JIT) | 1.81 MiB | 1.81 MiB |
| AOT | 24.85s | 0.707s (JIT) | 9.19 MiB | 10.81 MiB |

JIT's 1.81 MiB executable bundles the application, dependency manifests and
native SQLite; the separately installed .NET/ASP.NET runtime is excluded, just
as the JVM is excluded from a fat JAR. There are no third-party managed runtime
packages. AOT's stripped executable includes its runtime and requires the
1.62 MiB SQLite library beside it. Debug symbols are excluded from both sizes.
ReadyToRun remains an optional build target; it is omitted from the comparison.

JIT uses [.NET's framework-dependent single-file deployment](https://learn.microsoft.com/en-us/dotnet/core/deploying/single-file/overview)
with native-library extraction enabled. SQLite is extracted into the runtime's
bundle cache on first launch; managed code loads from the bundle. Set
`DOTNET_BUNDLE_EXTRACT_BASE_DIR` to override the default `$HOME/.net` cache.
No runtime is embedded, and application IL is still JIT-compiled.

## Build and run

Python 3 and a C compiler prepare the shared SQLite engine. Run from `csharp/`:

```sh
python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite --extra-source sqlite-config.c
pkgx dotnet publish -c Release -r osx-arm64 --self-contained false -p:PublishSingleFile=true -o bin/jit
pkgx dotnet publish -c Release -r osx-arm64 -p:PublishAot=true -p:IlcOptimizationPreference=Speed -o bin/aot
pkgx dotnet publish -c Release -r osx-arm64 --self-contained false -p:PublishReadyToRun=true -o bin/r2r
pkgx sqlite3 -bail ../db/db.sqlite < ../db/migrations/001_init.up.sql
pkgx +dotnet bin/jit/csharp
```

Migrate a **fresh** database. Defaults are `../db/db.sqlite`, `/tmp/benchmark.sock`
and two pool workers plus three socket I/O threads. Override with
`-db PATH -socket PATH -threads N -io-threads M`; N must be at least two and M
at least one. Existing socket paths are refused. Deploy only `bin/jit/csharp`
for JIT; the installed ASP.NET Core runtime supplies the shared framework.
`pkgx +dotnet` selects that runtime when launching the executable. AOT and
ReadyToRun require the published SQLite library beside their executable.

Use `pkgx dotnet bin/r2r/csharp.dll` for ReadyToRun or `bin/aot/csharp` for Native AOT.
`task build`, `task build-aot`, `task build-r2r`, their corresponding `start` tasks,
and `test`, `test-aot`, `test-r2r` are also available.

## Transaction and scheduling behavior

A bounded channel feeds one dedicated SQLite OS thread. That thread owns its
`NOMUTEX` connection for its entire lifetime, including setup and disposal.
Statements are prepared once. Every request runs:

```sql
BEGIN IMMEDIATE;
INSERT OR IGNORE INTO users (email) VALUES (?1);
INSERT INTO posts (content, user_id)
SELECT ?1, id FROM users WHERE email IS ?2
RETURNING id, user_id, content, created_at, updated_at;
COMMIT;
```

Every returned column is read, including length-aware text preserving NULs.
The `RETURNING` statement is exhausted and reset before committing. Statement
and commit failures roll back the transaction and the writer remains usable.
Foreign keys, AUTOINCREMENT, WAL/NORMAL, automatic checkpoints, memory configuration
and all other [shared settings](../db/README.md) are retained.

Immediately after **each individual commit**, the writer completes that request's
ordinary `TaskCompletionSource`. Its HTTP continuation is scheduled immediately.
There is no batching, reply polling, response grouping, deferred commit, user
cache, or collected HTTP wake-ups. Shutdown drains accepted writes and closes SQLite.

Two pool workers handle asynchronous continuations, while three socket I/O threads
run HTTP parsing/serialization inline. SQLite retains its dedicated writer.
These counts exclude GC, JIT, timers and other runtime threads and are not CPU
affinity restrictions. Both endpoints retain the same configuration within a run.
A one-pool-worker experiment stalled shutdown and is rejected.

The only native application code is a three-function shim for SQLite's variadic
configuration API on Apple ARM64. Queries, text conversion, transaction handling,
and response construction remain C#. `LibraryImport` generates the interop;
normal GC transitions are retained for operations that can block or allocate.
Nonblocking scalar column/connection accessors use `SuppressGCTransition`. No database work
runs on Kestrel's HTTP workers.

## Regression check

This check predates the HTTP/JSON setup correction described below.

The earlier **47.4K AOT** result used SQLitePCLRaw 3.0.5 with
`DOTNET_PROCESSOR_COUNT=2`, two five-second runs (46.72K and 47.99K). The first
three-run report measured 44.34K, 44.32K and 49.51K, with a 44.3K median.
To check whether subsequent code changes regressed writes, an independent
comparison rebuilt the earlier request path and alternated it with the then-current
executable: three rounds, fresh processes/databases, 50 connections, ten
seconds per endpoint, and the same SQLite binary and .NET SDK/runtime.

| AOT configuration | Write samples (K RPS) | Median writes | Median echo |
| --- | --- | ---: | ---: |
| Earlier SQLitePCLRaw, original `DOTNET_PROCESSOR_COUNT=2` | 44.02 / 48.58 / 47.58 | 47.58K | 152.49K |
| Earlier SQLitePCLRaw, two-worker pool limit | 48.84 / 49.47 / 48.56 | 48.84K | 136.59K |
| Direct interop before the HTTP correction, two-worker pool limit | 49.78 / 43.74 / 48.89 | 48.89K | 138.46K |

This comparison detected no regression in median write throughput. Holding the
pool limit fixed, the SQLite implementations differ by only 0.1%, within observed
variation; it does not establish a separate AOT speedup from direct interop.
The runtime/pool configuration is the useful AOT change in this comparison.
It improves median writes by about 2.8% versus the earlier configuration, while
echo falls about 9.2%. Both compared implementations produced low write
samples, so the 44K observations cannot by themselves identify a code regression.
All variants retain immediate, individual post-commit replies.

The old executable was overwritten. Its database, ordinary reply writer, model,
and SQLite initialization source match the hashes from the 47.4K measurement.
The startup/routing wrapper was reconstructed from the later saved experiment
harness, so this is a comparison of the earlier implementation, not a byte-for-byte
replay of that executable. Both rebuilt configurations passed the current HTTP,
validation, commit visibility, rollback and shutdown checks. Source differences,
build settings and exact-match hashes are retained with the experiment.

Separately, five more runs of those **unchanged artifacts** measured 48.8K
JIT and 49.3K AOT write medians. Pooling those with the original
three yielded 48.5K JIT and 49.0K AOT across eight runs each, retaining the slower
original samples. Those reports remain available; the published tables now measure
the corrected HTTP setup.

## HTTP and JSON setup correction

The original two-worker cap constrained echo, and the default socket transport
added queue transfers between I/O and pool threads. Raising the pool alone moved
JIT echo from 145K to 225K while reducing writes from 48.7K to 44.2K in the initial
sweep. The selected setup enables Kestrel's `UnsafePreferInlineScheduling` and
.NET's `DOTNET_SYSTEM_NET_SOCKETS_INLINE_COMPLETIONS=1`, with
`DOTNET_SYSTEM_NET_SOCKETS_THREAD_COUNT=3`. The program sets the runtime variables
before creating managed sockets. SQLite operations stay on the dedicated writer.

[HttpJson.cs](HttpJson.cs) is generic over the JSON type. It uses ordinary
`ReadFromJsonAsync`, caches the framework-configured request metadata at startup,
and calls `JsonSerializer.SerializeToUtf8Bytes` for responses. Setting the actual
serialized byte count as `Content-Length` avoids streaming/chunked response
framing. Each response is flushed with `BodyWriter.WriteAsync`; responses are
never collected together. Buffering allocates the complete UTF-8 representation
of each response. There are no model-size assumptions, fixed response templates,
custom field parsers, or altered schemas. Case-insensitive request fields,
charsets, unknown properties, large bodies and chunked requests retain normal
framework handling. The small-body reader experiment was discarded.

A short JIT sweep with three inline socket threads measured 300.6K echo with the
original JSON result, 314.1K using direct asynchronous serialization, and 364.1K
using the generic buffered response. The subsequent comparison applied the
generic helper to **both endpoints**, with two rotating five-second runs:

| Scheduling | JIT writes | JIT echo | AOT writes | AOT echo |
| --- | ---: | ---: | ---: | ---: |
| Kestrel inline only, two pool workers | 46.9K | 242.9K | 49.0K | 210.1K |
| Kestrel inline only, three pool workers | 48.0K | 279.5K | 48.6K | 262.6K |
| Kestrel + runtime inline, two socket threads / two pool workers | 48.0K | 286.1K | 49.0K | 239.3K |
| Kestrel + runtime inline, three socket threads / two pool workers | 47.7K | 363.7K | 48.5K | 348.0K |

Three socket threads were selected for echo above 300K in both modes, with write
medians within about 1% of the other configurations clearing 250K echo. Increasing
socket threads to four or disabling receive-buffer deferral provided no benefit
in the initial sweep. The final tables use separate ten-second runs of the
retained code, including the normal ASP.NET request options.

References: [Kestrel inline scheduling](https://learn.microsoft.com/en-us/dotnet/api/microsoft.aspnetcore.server.kestrel.transport.sockets.sockettransportoptions.unsafepreferinlinescheduling?view=aspnetcore-10.0),
[.NET 10 socket engine configuration](https://github.com/dotnet/runtime/blob/v10.0.12/src/libraries/System.Net.Sockets/src/System/Net/Sockets/SocketAsyncEngine.Unix.cs).

## Selection experiments

The initial library and scheduling comparisons used fresh processes and databases,
50 UDS connections, 5 seconds of `/posts` followed by 5 seconds of `/echo`, two rotating rounds, and
no HTTP warm-up. These exploratory medians are separate from the final 10-second,
three-run JIT/AOT results. All choices retain immediate per-commit replies.

| Comparison | Write RPS | Echo RPS |
| --- | ---: | ---: |
| Kestrel minimal APIs, SQLitePCLRaw 3.0.5, default pool | 43.2K | 222.4K |
| Kestrel middleware, same SQLitePCLRaw | 43.6K | 219.4K |
| Microsoft.Data.Sqlite.Core 10.0.12, reused parameters/statements | 38.0K | 221.1K |
| SQLitePCLRaw, two HTTP pool workers | 47.2K | 145.3K |
| Direct .NET SQLite interop, two workers | 48.5K | 146.1K |
| Direct interop, three workers | 46.7K | 166.2K |
| Direct interop + strict SpanJson adapter, two workers | 48.1K | 153.1K |

The final pairwise comparison put direct interop at 48.2K–48.7K writes/sec versus
46.7K–47.6K for SQLitePCLRaw. Its explicit pointer lifetime is confined to the
single-owner database wrapper; the tests exercise rollback, text lengths and
connection settings. This removes all third-party managed application dependencies.

Kestrel's middleware path had no useful advantage over its source-generated minimal
APIs. GenHTTP's independent engine binds IP/TCP endpoints, so it does not fit this
repository's UDS protocol without modifying its transport. Layers built on Kestrel
would retain the same transport. This selects among the tested paths; it is not a
universal ranking of every C# framework.

I/O queue counts 0/1/2/4 measured 43.3K–43.9K writes/sec, without a clear gain.
Reporting 2/4 runtime processors, disabling worker spin, and enabling socket inline
completions also failed to improve JIT writes over the control in that sweep.
Pooling reply objects measured 43.2K versus 43.3K for ordinary task completions,
so the simpler ordinary completion is retained.

A separate mode comparison retained server GC. Workstation GC reduced peak write
RSS from about 172 to 101 MiB for JIT/ReadyToRun and 105 to 34 MiB for AOT, but
AOT writes fell from 49.4K to 48.5K; ReadyToRun fell from 48.0K to 47.7K. JIT
was effectively unchanged at 47.6K. The write-first selection keeps the default
server GC consistently across modes.

A final two-round, **10-second** reply-path comparison measured 48.36K writes/sec
for the ordinary async helper versus 48.44K for a `TryWrite` fast path returning
the commit task directly. This 0.16% difference was smaller than observed run
variation, so the simpler helper is retained. Both notify each request immediately
and independently after commit.

Five rotating rounds of one million typed parse + serialization operations,
after warm-up, compared the same UTF-8 request and response bytes:

| JSON library | Write JSON work | Echo JSON work |
| --- | ---: | ---: |
| System.Text.Json 10.0.12, generated | 305.0 ns/op | 267.0 ns/op |
| System.Text.Json 10.0.12, reflection | 333.6 ns/op | 285.4 ns/op |
| SpanJson 4.2.1 | 182.7 ns/op | 150.0 ns/op |
| Newtonsoft.Json 13.0.4 | 814.2 ns/op | 683.6 ns/op |

SpanJson's stream API accepted trailing garbage. The HTTP comparison adds full
input consumption and UTF-8 validation; it passed the integration checks but did
not improve writes. System.Text.Json works unchanged in all three deployment modes
and is selected. The table records the historical microbenchmark results.

The original SQLitePCLRaw string path and Microsoft.Data.Sqlite comparison also
truncated large text containing NUL. The corrected SQLitePCLRaw candidate uses
explicit-length binding/reading; the selected direct interop does the same.
Only conforming candidates were eligible for final selection.

Sources: [Kestrel](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/servers/kestrel?view=aspnetcore-10.0),
[JSON source generation](https://learn.microsoft.com/en-us/dotnet/standard/serialization/system-text-json/source-generation),
[SQLitePCLRaw](https://github.com/ericsink/SQLitePCL.raw),
[SpanJson](https://github.com/Tornhoof/SpanJson),
[GenHTTP transport](https://github.com/Kaliumhexacyanoferrat/GenHTTP/blob/main/Engine/Internal/Infrastructure/Endpoints/EndPoint.cs).

## Validation and reproduction

The real UDS tests cover the shared email fixtures, every SQLite pragma and compile
define, malformed JSON/types/UTF-8, empty and missing fields, chunked and large
bodies, Unicode/NUL round trips, case-insensitive fields, UTF-16, correct dynamic
content lengths, all five returned columns, 50 concurrent clients,
statement and deferred-constraint commit failures, recovery, and graceful shutdown.
An independent connection observes each successful transaction before its response
is counted. Echo remains responsive while an external SQLite write lock delays a post.

From `csharp/`, after publishing:

```sh
python3 test-http.py --bundle bin/jit/csharp
python3 test-http.py --command bin/aot/csharp
python3 test-http.py --command 'pkgx dotnet bin/r2r/csharp.dll'
```

The bundle test copies only the JIT executable to an isolated directory and
uses a fresh extraction cache, then runs the full HTTP and transaction checks.

From the repository root, with fresh output directories:

```sh
python3 csharp/measure.py results/csharp-http --mode jit aot --rounds 3
python3 csharp/measure.py results/csharp-startup --mode jit aot --startup --fresh-bundle-extraction --rounds 5
python3 csharp/measure-build.py results/csharp-build --mode jit aot
```

The mode runner resolves the runtime using `pkgx dotnet`, then launches built
apphosts directly; pkgx resolution is outside startup timing. It checks artifact
hashes, expected statuses, database integrity, foreign keys, content, timestamps,
user counts and both AUTOINCREMENT sequences. Up to 50 committed writes can lack
received responses when oha cancels at its deadline. RSS and CPU use the shared
measurement helpers; CPU 100% is one core, and echo retains write allocations.

Startup ends at the post-bind listening log and includes runtime/native-library
loading, SQLite initialization and HTTP setup. Five fresh launches use migrated
databases; an untimed echo verifies readiness. Filesystem caches are not flushed.
The retained startup samples precede bundling. For future bundled measurements,
`--fresh-bundle-extraction` includes SQLite extraction on each launch.

Clean release builds remove `bin`, `obj` and the native library in a disposable
source copy, recompile SQLite, and publish the application. The current JIT
recipe includes single-file bundling with SQLite, excluding the runtime; the
retained build timings precede that change. AOT includes native
compilation, linking and stripping; ReadyToRun includes crossgen2. The SDK/runtime
and dependency caches are retained, and downloads are excluded. Three rotating
samples per mode are reported. Warm debug rebuilds rename the public `NewPost`
type and its consumers across files three times after an excluded warm-up and
no-change control; output hashes verify recompilation. All modes share that
ordinary `pkgx dotnet build -c Debug --no-restore` development workflow.

Retained performance measurements and hashes: `../results/csharp-http-2026-09-18/`;
startup: `../results/csharp-http-startup-2026-09-18/`;
builds: `../results/csharp-http-build-2026-09-18/` (gitignored).
Exact bundled source and release executables are preserved in
`../results/csharp-bundle-source/final/`. The later
`csharp-bundle-2026-09-18/`, `csharp-bundle-startup-2026-09-18/` and
`csharp-bundle-build-2026-09-18/` runs are excluded because they overlapped
background video playback; only the new release size is reported.
The HTTP/JSON sweeps are in `../results/csharp-echo-workers/`,
`csharp-echo-transport/`, `csharp-echo-json/`, `csharp-http-json/`, and
`csharp-http-final-selection/`. Their source and artifact snapshots are in
`../results/csharp-http-source/`.

The earlier pre-correction runs remain in `../results/csharp-2026-09-18/` and
`../results/csharp-verification-2026-09-18/`; the combined eight-run report is
`../results/csharp-pooled-2026-09-18/`. The regression comparison is in
`../results/csharp-regression-2026-09-18/`, with rebuilt earlier source and
provenance in `../results/csharp-regression-source-2026-09-18/`.
Exploratory data lives in `../results/csharp-selection-1/`, `csharp-queues/`,
`csharp-runtime/`, `csharp-aot-selection/`, `csharp-code-selection/`,
`csharp-final-selection/`, `csharp-modes-selection-2/`, and `csharp-reply-selection/`.
The discarded HTTP/library experiment source snapshot is
`../results/csharp-selection-source/`; JSON samples are `../results/csharp-json.json`.
