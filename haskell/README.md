# Haskell

**Warp 3.4.16 + Aeson 2.3.2.0 decoding + jsonifier 0.2.1.3 encoding +
direct-sqlite 2.3.29**, compiled with GHC `-O2`. Warp uses **time-manager 0.4.0**
to avoid contention in the older timer implementation. The default is
`+RTS -N2 -A8m`; `-N4` is the echo configuration. Connection threads are assigned
round-robin to capabilities; one bound writer owns SQLite.

HTTP/1.1 over a Unix socket, JSON decoding/encoding, the shared email validation
rule, and one independently committed SQLite transaction per post. The application
lives in Haskell; a small C file adapts SQLite startup configuration and supplies
disabled-feature compatibility symbols required by direct-sqlite.

## Toolchain and reproduction

Install `pkgx`, then from the repository root:

```sh
python3 haskell/build.py --setup
python3 haskell/test.py
cd loadgen
go build -trimpath -o bombard .
cd ..
python3 loadgen/test.py
```

[toolchain.sh](toolchain.sh) requires **pkgx GHC 9.14.1 and Cabal 3.14.2.0**.
It does not fall back to a different installed compiler. The Cabal store is local
to `.tools/cabal`; the freeze file pins the dependency graph and Hackage index.
Cabal 3.14 reports that GHC 9.14 is newer than its tested compiler range; the
build and correctness checks here run with that exact combination.

Subsequent release builds use `python3 haskell/build.py`; development builds use
`python3 haskell/build.py --debug` (`-O0`, debug information retained). The root
Taskfile exposes `task haskell:setup`, `task haskell:build`, `task haskell:debug`,
`task haskell:test` and `task haskell:run -- ...`. The freeze file intentionally
does not constrain this application's `dev`/`comparisons` flags, so both remain
usable with the same pinned external dependencies.

The database must already contain the [shared migration](../db/migrations/001_init.up.sql).
For example, after creating a migrated database:

```sh
haskell/bin/haskell-benchmark -db /path/to/bench.sqlite -socket /tmp/benchmark.sock
```

The release links one HTTP server; `python3 haskell/build.py --comparisons` builds
the separate `bin/haskell-comparison` executable containing Snap as well.
Warp's unused TLS-client-certificate dependency is disabled with `-x509`.
The server refuses an existing socket path and removes its own socket on shutdown.
`-inspect -db PATH` reports the SQLite version, compile options and connection pragmas.

## Selection evidence

The starting candidates were selected before implementation from primary sources:

- **HTTP:** Warp's authors publish [throughput comparisons with Snap and other servers](https://aosabook.org/en/v2/yesod.html)
  and describe its [I/O and allocation optimizations](https://aosabook.org/en/posa/warp.html).
  Those historical TCP results justify testing Warp, but cannot establish the
  winner for this Mac's current UDS workload. Snap is therefore measured locally too.
- **JSON:** [jsonifier's published encoder benchmark](https://github.com/nikita-volkov/jsonifier)
  reports 2.054 µs versus Aeson's 6.456 µs for a roughly 1 KiB example. Jsonifier
  only encodes; Aeson supplies full-document decoding and generated record mapping.
  Aeson's Template Haskell encoder is the local alternative. Both use library
  escaping and serialize the actual decoded fields; neither returns the request bytes.
  [Hermes](https://github.com/velveteer/hermes) has impressive partial-decoding
  results, but its on-demand interface does not validate every part of a document.
  Its published full `Aeson.Value` person test is slightly slower than Aeson.
  That is a poor fit for preserving full JSON validation on these small requests.
- **SQLite:** [sqlite-simple's author's binding comparison](https://nurpax.github.io/posts/2013-08-17-sqlite-simple-benchmarking.html)
  measured direct-sqlite at 3.6M scalar rows/sec versus sqlite-simple at 1.8M,
  and identified FFI and allocation overhead. Direct-sqlite is sqlite-simple's
  underlying library and exposes reusable statements and typed column access.
  This is historical row-reading evidence, not a claim that today's write workload
  gains 2×. The whole transaction path is measured here with **direct-sqlite 2.3.29**.

All comparisons use the same SQLite engine, SQL, request body, response fields,
validation and commit policy. Short diagnostic runs guide selection; final
README results use the full shared benchmark procedure.

Three rotating 3-second rounds at `-N2`, using ordinary SQLite stepping and the
default HTTP scheduler, initially measured:

| HTTP / encoder | Writes/sec | Echo/sec |
| --- | ---: | ---: |
| Warp / jsonifier | 13.9K | 26.2K |
| Warp / generated Aeson | 13.5K | 26.0K |
| Snap / jsonifier | 10.5K | 48.3K |

The independent codec test makes the small JSON difference clearer: after the
warm-up pair, median full decode/encode time is **1.351 µs with jsonifier** versus
**1.598 µs with Aeson**, a **15.4% reduction**. Both produced the same byte-count
checksum in every sample. This supports jsonifier for this corpus; the older
published 3× encoder result does not transfer to the complete round trip.

The HTTP result depends strongly on capability count. Two short scaling rounds
with the original ordinary SQLite stepping measured Warp at **103.0K echo/sec
with one capability**, versus Snap's best measured **91.3K with eight**. Warp
also led writes at every matched count. Additional scheduler tests placed
connections round-robin on GHC capabilities using `forkOnWithUnmask`, compared
bound/unbound writer threads, and tried `-qm`. None restored useful multicore
echo scaling. A controlled 3.4.12/3.4.16 check also retained the slowdown because
both versions used the same contended timer dependency. The timer diagnosis
below supersedes the initial one-capability selection.

Direct-sqlite's documented `stepNoCB` removes safe-FFI scheduler transitions for
SQL without Haskell callbacks. A one-round diagnostic at `-N1` improved writes
from **12.0K to 19.6K/sec**; using it for inserts while keeping ordinary stepping
for transaction control reached **15.5K**. Full callback-free stepping became
slower at eight capabilities. These initial results used the contended timer
dependency; they do not establish an intrinsic limit on Haskell multicore scaling.

Reproduce diagnostic HTTP/library/scheduler comparisons with fresh output directories.
These use the final timer dependency; the initial selection figures above preceded
that fix and release dependency pruning:

```sh
python3 haskell/build.py --comparisons
python3 haskell/test.py --comparisons
python3 haskell/compare.py libraries results/haskell-libraries --rounds 3
python3 haskell/compare.py scaling results/haskell-scaling --rounds 2
python3 haskell/compare.py scheduling results/haskell-scheduling --rounds 2
python3 haskell/compare.py migration results/haskell-migration --rounds 1
python3 haskell/compare.py sqlite results/haskell-sqlite --rounds 1
python3 haskell/compare.py writer results/haskell-writer --rounds 1
python3 haskell/compare.py heap results/haskell-heap --rounds 2 --duration 5s
```

The independent codec benchmark decodes and encodes the entire 65,536-entry
randomized corpus four times per sample, rotating encoder order for six samples.
The first pair is warm-up; every serialization is forced and contributes to a
printed byte-count checksum. Build/run from `haskell/` after generating the load corpus:

```sh
./toolchain.sh cabal run exe:codec-benchmark -- ../.tools/random-json-v1.jsonl +RTS -A8m -RTS
```

## Multicore timer regression

Profiling identified contention in GHC's shared timer queue. In **time-manager
0.3.2**, Warp's per-request pause/resume/tickle operations repeatedly unregister,
register and update that queue. Adding capabilities increased cross-thread
coordination while useful HTTP throughput fell. GC was a small fraction of elapsed
time. Pinning HTTP threads to capabilities, disabling migration, changing writer
thread binding and removing Warp's explicit post-response yield did not fix it.

Upstream [time-manager 0.4.0](https://github.com/yesodweb/wai/pull/1109) removes
hot-loop timer updates, rate-limits renewals and makes pause independent of the
shared timer queue. A controlled one-round diagnostic, changing only that dependency
and retaining `-fork pinned -sqlite-step hybrid`, measured:

| GHC capabilities | Echo/sec, timer 0.3.2 | Echo/sec, timer 0.4.0 |
| --- | ---: | ---: |
| 1 | 97.1K | 143.5K |
| 2 | 36.8K | 238.1K |
| 4 | 24.2K | 298.7K |
| 8 | 16.1K | 215.0K |

The release pins **0.4.0**. Warp 3.4.16 permits that version, but the transitive
HTTP/2 library 5.4.4 still declares `<0.4`. The project relaxes only
`http2:time-manager`; all versions remain frozen. The complete application builds
and passes HTTP/1.1 correctness, rollback, shutdown and idle-timeout checks.
HTTP/2 is outside this benchmark. Timer 0.4 may expire an active timeout up to
one second earlier than its latest renewal; it still enforces timeouts.

## Correctness and SQLite

The engine is the same **SQLite 3.53.4** amalgamation, checksum and `-O3 -DNDEBUG`
compile options as the other modernized stacks. `+systemlib` tells direct-sqlite
to use that engine. The local Cabal configuration points both compilation and
runtime linking to it. Startup disables memory accounting and installs the same
1,024-slot page pool before initialization. The connection uses `NOMUTEX`, WAL,
`synchronous=NORMAL`, foreign keys, a 10-second busy timeout, a 2,000 KiB cache,
1,000-page auto-checkpoint, memory temporary storage and no mmap.

One writer owns the connection and five reusable statements. A bounded STM queue
holds requests; each has its own MVar reply. Every write performs `BEGIN IMMEDIATE`,
`INSERT OR IGNORE` for the user, the indexed post insert/select with all five
`RETURNING` columns, and `COMMIT`, then replies immediately. There is no batching,
user cache or response grouping. Failed writes reset statements and roll back;
a rollback failure disables subsequent writes.

The default uses the library's `stepNoCB` for all five statements. No SQL
function, collation or tracing callback enters Haskell. These foreign calls
hold a capability and delay GHC collection until they return; a long lock wait
or filesystem stall can therefore delay other Haskell work. `-sqlite-step safe`
and `-sqlite-step hybrid` reproduce the tradeoff without changing SQL, locking,
timeouts, commits or durability. They are also available in the ordinary release.

The shared engine omits extension loading, shared-cache support and deprecated
tracing. Direct-sqlite still references three associated C symbols when linking;
the compatibility definitions keep those APIs disabled. They perform no queries
and the application never calls them. SQLite itself retains the shared compile options.

`test.py` checks exact engine/compile-option parity, every connection pragma,
the shared email fixtures, malformed/trailing JSON, Unicode/NUL round trips,
route/method handling, idle timeout enforcement, commit visibility, rollback after an injected insert
failure, continued writes after rollback, and socket ownership. The shared
preflight also verifies 64 randomized echo and post responses per configuration.
Timed runs check every HTTP status, drain outstanding requests, and verify that
committed posts exactly equal 201 responses, including every persisted email/content
pair, timestamps, foreign keys, AUTOINCREMENT and integrity.

## Measured results

**2026-09-22**, Apple M1 Pro (8 performance + 2 efficiency cores), 16 GiB,
macOS 26.4. Four rotating rounds per configuration; ten seconds of writes followed
by ten seconds of echo, fresh process/database per run, 50 concurrent requests.
These are separate measurements from the other languages' September 18 sweep.

| Configuration | Writes/sec | Write p50 / p99 | Write RSS / CPU | Echo/sec | Echo p50 / p99 | Echo RSS / CPU |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `-N2` (default) | 31.3K | 0.609 / 24.247ms | 150.8 MiB / 149% | 243.9K | 0.179 / 0.542ms | 153.7 MiB / 194% |
| `-N4` | 29.4K | 0.993 / 22.495ms | 101.2 MiB / 176% | 304.1K | 0.113 / 0.609ms | 103.0 MiB / 374% |

Two-capability writes ranged **30.1K–31.4K**, echo **243.2K–245.4K**.
Four-capability writes ranged **29.0K–29.6K**, echo **302.8K–305.1K**.
Every timed request returned the expected status; all eight databases passed
integrity, foreign-key, exact email/content, timestamp and AUTOINCREMENT checks.
Every committed row corresponds to one drained 201 response.

After the timer fix, a short SQLite stepping comparison at `-N2` measured
**13.9K writes/sec with ordinary stepping**, **15.6K mixed**, and **24.5K
callback-free**. At `-N4` these were **16.7K / 17.6K / 23.8K**. Bound/unbound
writer results were similar, so the writer remains bound. The faster callback-free
path has longer write tails; the full-run p99 figures above are deliberately
retained. No transaction batching or weakened durability is involved.

| Whole-stack measurement | Result |
| --- | ---: |
| Launch to UDS bind, `-N2` | 13.75ms median (13.38–13.87ms) |
| Launch to UDS bind, `-N4` | 14.75ms median (13.78–15.27ms) |
| Clean application release build | 6.79s median, three samples |
| Warm debug rebuild after public type change | 2.78s median, three edits |
| Stripped release executable | 41,155,136 bytes / 39.25 MiB |
| Shared SQLite library | 1,697,632 bytes / 1.62 MiB |
| Combined application + SQLite | 42,852,768 bytes / 40.87 MiB |

The executable includes the GHC runtime and Haskell dependencies; the only
non-system dynamic library is the shared SQLite engine. macOS system libraries
are excluded from distribution size. GHC and Cabal are build tools, not required
at launch. The compiled SQLite rpath points to this checkout's `.tools/sqlite/lib`.

After setup, reproduce the complete published measurements from the repository root:

```sh
python3 haskell/build.py
python3 haskell/test.py
python3 loadgen/test-servers.py --config haskell-2 haskell-4
python3 haskell/measure-build.py results/haskell-build
python3 loadgen/measure.py results/haskell-final --config haskell-2 haskell-4 --rounds 4
python3 haskell/measure-startup.py results/haskell-startup
```

The committed [measurement record](measurements.json) retains compact samples,
aggregates, correctness results and hashes. Full logs, databases, histograms and
RSS samples are local and gitignored:

- Final HTTP: `results/haskell-multicore-final-2026-09-22/`.
- Startup/build/profile: `results/haskell-multicore-{startup,build,profile}-2026-09-22/`.
- Artifact bytes/linkage: `results/haskell-multicore-artifact-2026-09-22.json`.
- Controlled timer comparison: `results/haskell-timer-2026-09-22/`.
- Writer comparison: `results/haskell-writer-fixed-timer-2026-09-22/`.
- Initial libraries/scaling/scheduling/SQLite experiments:
  `results/haskell-{selection,scaling,scheduling,sqlite}-2026-09-22/`.
- Codec round trips: `results/haskell-codecs-2026-09-22.csv`.

## Measurement definitions

The [shared load runner](../loadgen/README.md) uses five independent single-processor
Vegeta clients, 50 total in-flight requests and the seeded randomized corpus.
Each fresh process/database runs `/posts` for 10 seconds, then `/echo` for 10 seconds,
without HTTP warm-up. RPS, latency and CPU are medians; RSS is the largest sampled
whole-process resident set, including SQLite and the GHC runtime. Echo retains
allocations from writes. These are same-host, unpinned M1 Pro measurements.

`+RTS -Nn` controls GHC capabilities, not a fixed number of HTTP threads or OS threads.
`-A8m` gives each capability an 8 MiB allocation area. Connection placement on GHC
capabilities is not physical CPU affinity. macOS still schedules OS threads.

[measure-startup.py](measure-startup.py) times five fresh process launches through
the first post-bind log, with migrations and pkgx resolution outside timing and
an untimed echo readiness check afterward. Filesystem caches are not flushed.
[measure-build.py](measure-build.py) takes three fresh application release builds,
including C shim compilation, linking, stripping and signing; the compiler,
optimized Haskell dependencies and shared SQLite remain cached, matching the
OCaml application's clean-build scope. Setup/downloads are excluded.

Debug rebuilds rename the public `NewPost` type/constructor and consumers across
modules in a disposable copy, using `-O0` with debug metadata. Three real edits
must each change the executable hash. Warm-up and a no-change control are excluded.
This is a development executable rebuild, not an optimized release rebuild.

## Profiling

`profile.py` uses macOS `sample`, GHC `+RTS -s` statistics and `vmmap` summaries.
Profiler runs are diagnostic and excluded from the published throughput table.
The initial four-capability echo profile spent only **0.070 seconds of 10.173
elapsed seconds in GC**. Native stacks showed substantial scheduler wake-ups,
condition-variable waits and capability hand-offs. The one-capability echo
profile spent **0.407 of 10.127 seconds in GC**, despite much higher throughput.
These measurements motivated scheduling/FFI experiments rather than larger heaps.
After the timer fix, the four-capability echo profile spends **0.229 of 10.851
elapsed seconds in GC** (about 2%). The active stacks now emphasize socket I/O,
UTF-8 decoding/encoding and ordinary request processing. The two/four-capability
write profiles spend **0.059 / 0.028 seconds in GC**, respectively. Their native
stacks emphasize `pwrite`, `fsync`, file locking, SQLite VM execution and WAL
checksums. The shared WAL/checkpoint policy is retained.

A separate heap diagnostic found that `-G1` reduced one-capability echo RSS from
198.5 to 37.3 MiB but reduced throughput by about 10%; a smaller allocation area
also lost throughput. These were two short rounds before the timer fix, not
publication results. The final multicore configurations keep generational GC;
GHC live-heap size and native `malloc` allocations are distinct from process RSS.

```sh
python3 haskell/profile.py results/haskell-profile --capabilities 2 4
python3 haskell/build-timer-baseline.py
python3 haskell/compare.py timer results/haskell-timer-control --rounds 2
```

The first command profiles the selected implementation. The last two rebuild
the same application with timer 0.3.2 and compare it against the release, using
identical explicit capability, connection-placement and SQLite-step settings. Native stack counts include waiting threads and
must not be read as percentages of CPU time.
