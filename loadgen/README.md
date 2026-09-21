# Randomized HTTP load

The shared load generator uses [Vegeta 12.13.0](https://github.com/tsenart/vegeta)
as a library. The small Go driver adds deterministic random target selection,
independent client processes, strict status checks and merged HDR histograms.
Vegeta supports HTTP/1.1 over Unix sockets directly, so the benchmark keeps its
existing transport. It also supports TCP via `run.py --socket '' --url URL`.
Its dependencies are pinned in `go.mod` and `go.sum`.

## Run

Go 1.27.1 and Python 3 are required. Build once, outside any measured interval:

```sh
cd loadgen
go build -trimpath -o bombard .
python3 test.py
cd ..
```

For an already-running server:

```sh
task post
task echo
# Equivalent without Task:
python3 loadgen/run.py posts --socket /tmp/benchmark.sock --output results/posts.json
python3 loadgen/run.py echo --socket /tmp/benchmark.sock --output results/echo.json
```

After setting up the [Rust](../rust/README.md), [Go](../go/README.md), and
[OCaml](../ocaml/README.md) toolchains, build all three releases and run all six
configurations, sequentially:

```sh
task benchmark -- results/random-json
# Or, with releases already built:
python3 loadgen/test-servers.py
python3 loadgen/measure.py results/random-json --rounds 6
```

The additional C# JIT/AOT, Kotlin JVM and old Zig implementations used a
four-configuration, four-round sweep. That Zig implementation is superseded.
To remeasure the remaining three, build [C# JIT and AOT](../csharp/README.md)
and [Kotlin JVM](../kotlin/README.md), set `JAVA_HOME` to the JDK used to build
Kotlin, and use six rounds so each occupies every order position twice:

```sh
python3 loadgen/test-servers.py --config csharp-jit csharp-aot kotlin-jvm
python3 loadgen/measure.py results/random-json-remaining --rounds 6 --config csharp-jit csharp-aot kotlin-jvm
```

The modernized **Zig 0.16.0 / std.http / zio / yyjson / zqlite** stack uses
one executor (`zig`) and four (`zig-4`), each plus its dedicated writer. Build
[Zig](../zig/README.md) and measure the two configurations independently of the
historical sweep:

```sh
python3 loadgen/test-servers.py --config zig zig-4
python3 loadgen/measure.py results/zig-final --rounds 4 --config zig zig-4
```

Four rounds alternate their order twice, with ten seconds per endpoint. Both
must exit gracefully and remove their own sockets. The report records the Zig
source hashes as well as the executable, SQLite library and corpus hashes.

The **Haskell Warp / Aeson + jsonifier / direct-sqlite** stack uses two GHC
capabilities (`haskell-2`) and four (`haskell-4`), with one SQLite writer in the
same process. Build with the [pkgx wrapper](../haskell/README.md), then run its
separate four-round sweep:

```sh
python3 haskell/build.py --setup
python3 haskell/test.py
python3 loadgen/test-servers.py --config haskell-2 haskell-4
python3 loadgen/measure.py results/haskell-final --rounds 4 --config haskell-2 haskell-4
python3 haskell/measure-startup.py results/haskell-startup
python3 haskell/measure-build.py results/haskell-build
```

Both endpoints retain the same capability count and `-A8m`. The report records
source, dependency-freeze, executable and native SQLite hashes, verifies all
committed rows and requires socket cleanup. Haskell's final measurements are
from **2026-09-22**, separate from the older stacks' sweeps. Profiling and short
configuration experiments are excluded from the published throughput medians.

Use a fresh output directory. Each round rotates configuration order by one
position; the default six-configuration sweep uses six rounds so every
configuration occupies every order position once.
Each process gets a fresh migrated database, then ten seconds of `/posts` and
ten seconds of `/echo`, with 50 concurrent requests and no HTTP warm-up. Server
settings remain fixed across both endpoints: Rust workers 1/3, Go processors
2/4, and OCaml HTTP domains 1/4 plus their writer. Startup, build times and binary
sizes are independent measurements and are not rerun by this workload.

The native **Bun.serve / bun:sqlite / Valibot** configuration is named `bun`.
It uses one HTTP event loop plus one dedicated SQLite worker in the same process,
and the default SQLite engine (system SQLite on macOS). Shared connection pragmas
are retained, with `journal_size_limit=-1` for WAL reuse. Build and test [Bun](../bun/README.md), then
measure it separately:

```sh
task bun:setup
task bun:check
task bun:test
python3 loadgen/test-servers.py --config bun
python3 bun/measure-build.py results/bun-build
python3 bun/measure-startup.py results/bun-startup
python3 loadgen/measure.py results/bun-http --rounds 4 --config bun
```

The report records Bun's SQLite version and compile options, source/lockfile and
executable hashes, and the exception to the custom shared SQLite configuration.
Shutdown must exit cleanly and remove its socket. The published Bun sweep is
from **2026-09-22**, with Chrome closed, independent of the other stacks' runs.

## Payloads and correctness

`workload.py` generates 65,536 JSON objects from seed **20260918**, before timing,
and caches them under `.tools/random-json-v1.jsonl`. Every object has one unique,
syntactically valid ASCII email at a reserved `.example.test` domain and **32–256
Unicode characters** of nonempty content. The alphabet contains letters, digits,
spaces, punctuation, quotes, backslashes, tabs, newlines and Unicode. Ordinary
JSON serialization handles escaping. Every generated email passes the shared
whole-string rule. No malformed or invalid payloads enter the benchmark.

Every request samples a corpus entry with replacement using a counter-based
SplitMix64 permutation; each client has an independent seed. The round number
changes the selection seed, equally for every server configuration. The corpus
hash is recorded. This is a finite randomized corpus: email/content pairs recur,
and emails are not unique on every request. It exercises new users and random
existing users, instead of the old single-user workload. Corpus generation and
JSON encoding happen before timing so Python cannot throttle requests.

All responses must be **201 for `/posts`** and **200 for `/echo`**. Redirects,
wrong success codes, transport failures, timeouts and zero-request runs fail the
command. The client drains in-flight requests after the issue deadline; committed
posts must equal received 201 responses **exactly**. Every database undergoes
integrity, foreign-key, timestamp, AUTOINCREMENT and exact corpus-content checks,
including the email/content relationship for every stored post. OCaml domain
counters must all be nonzero.

`test-servers.py` separately verifies 64 randomized echo round trips and all five
committed post response columns at each selected setting, using a separate
database. `test.py` checks the generator's failure gates against a synthetic
server, including redirects and disconnects. These checks are outside timing.
During timed runs, response bodies are fully read and discarded; statuses are
checked for every request, with persisted content verified afterward.

## Contention and CPU isolation

The measured M1 Pro has **8 performance + 2 efficiency cores**, **10 physical and
10 logical cores**, so it has no SMT siblings. `sysctl` reports four performance
cores per 12 MiB L2 cache and two efficiency cores sharing 4 MiB L2. macOS controls
thread placement. The actual `THREAD_AFFINITY_POLICY` probe returned **46,
`KERN_NOT_SUPPORTED`**. Even on platforms where that Mach policy works, it is a
cache-sharing hint, not hard CPU pinning. There is no enforced CPU/cache-domain
separation or NUMA memory placement in these Mac results. Apple describes its
[scheduler-controlled placement](https://developer.apple.com/news/?id=vk3m204o)
and [Apple silicon scheduling](https://developer.apple.com/videos/play/tech-talks/110147/).

Five separate Vegeta processes each use `GOMAXPROCS=1` and ten concurrent requests.
That is a concurrency budget, **not CPU affinity or an OS-thread limit**. Each
process owns its corpus storage, HTTP pool, target-selection counter and latency
histogram. No per-request counters, atomics, locks or result channels are shared
between client processes. Histograms merge after load finishes. The servers
already keep request state local; OCaml counters and JSON scratch buffers are
domain-local, and Rust's shared startup counter is outside the request path.
Each SQLite writer necessarily owns one serialized transaction stream. Transaction semantics are unchanged. [Zig](../zig/README.md) now uses coroutine
HTTP executors and its own dedicated SQLite writer with per-request completion.

Short echo calibration on the two fastest configurations selected this client
layout. These are diagnostic single samples, not the published six-round results:

| Client layout | Rust 3 workers | Go 4 processors |
| --- | ---: | ---: |
| One process, 4 Go processors | 201.7K | 188.6K |
| One process, 6 Go processors | 237.6K | 211.1K |
| One process, 8 Go processors | 228.5K | 203.1K |
| Four processes, 1 processor each | 322.6K | 300.4K |
| Five processes, 1 processor each | 401.3K | 301.5K |
| Six processes, 1 processor each | 407.8K | 245.5K |

Five clients removed most scheduler contention. A sixth added only 1.6% to Rust
in this check and reduced Go throughput. This is evidence for choosing five on
this host, not proof of isolation or unlimited generator headroom. Calibration
metrics are retained locally in `loadgen/measurements/client-calibration.json` (gitignored). Override
`--client-cpus`, `--client-processes` and `--client-shards` to repeat the sweep.

Only one server and its clients run at a time. Builds, correctness tests and
corpus generation finish first. Normal desktop applications remain running;
the runner records process snapshots, topology, affinity capability and thermal
status. It neither pins nor reconfigures unrelated applications. Unix sockets
avoid network-device traffic; NIC RSS/IRQ queues do not apply, but kernel socket
work and memory bandwidth remain shared. These are **same-host, unpinned UDS
measurements**, not isolated server-capacity measurements. A second physical
client host would require a separately measured TCP setup and must not be mixed
into these UDS tables. On a Linux test host, dedicated physical cores, verified
SMT separation, NUMA-local memory and explicit IRQ/background placement would
allow stronger isolation than this Mac exposes.

## Metrics and SQL

Throughput is completed requests divided by the time from the earliest client
load start through the final drained response. Percentiles come from merged HDR
histogram counts (1 µs resolution at the low end, three significant digits),
not averaged client percentiles. This fixed-concurrency, maximum-rate test is
closed-loop: its latency is not an open-loop arrival-rate SLO measurement.

Server and combined client CPU are recorded separately; **100% is one core**.
Server CPU uses a whole-process CPU-time delta over the load's wall duration;
client CPU sums the children's timed CPU use over that same window. Client
startup and corpus loading are excluded from throughput. RSS samples cover the
server and the complete client process group every approximately 100 ms. Echo
retains allocations from the preceding write phase. Published RPS, latency and
CPU are medians; RSS is the largest sample. Raw logs, databases, histograms and
sampling records stay in the ignored `results/` directory; diagnostic reports in
`loadgen/measurements/` are also ignored. Published aggregates live in the READMEs.

Before considering an insert change, `db/explain-inserts.py` inspected the exact
SQLite **3.53.4** library built for this benchmark. The post insert uses
`SEARCH users USING COVERING INDEX sqlite_autoindex_users_1 (email=?)`.
Full plans and bytecode remain in `loadgen/measurements/sqlite-explain.json` (gitignored).
No query-plan change was justified by this inspection. Zig retains the same
indexed insert/select structure, using named parameters and an equivalent `IS`
comparison for the non-null email. Any future rewrite must be measured and applied across
all implementations while preserving per-request commits and returned values.

## Published CPU use

Six-round medians from `results/random-json-2026-09-18/summary.json` (gitignored).
Client CPU includes all five load processes; 100% represents one core.

| Server configuration | Write server CPU | Write client CPU | Echo server CPU | Echo client CPU |
| --- | ---: | ---: | ---: | ---: |
| rust-1 | 130% | 84% | 99% | 428% |
| rust-3 | 141% | 90% | 269% | 492% |
| go-2 | 139% | 99% | 197% | 435% |
| go-4 | 183% | 108% | 345% | 460% |
| ocaml-1 | 148% | 89% | 94% | 144% |
| ocaml-4 | 226% | 104% | 316% | 409% |

Four-round medians from `results/random-json-remaining-2026-09-18/summary.json`:

| Server configuration | Write server CPU | Write client CPU | Echo server CPU | Echo client CPU |
| --- | ---: | ---: | ---: | ---: |
| csharp-jit | 278% | 114% | 300% | 464% |
| csharp-aot | 257% | 114% | 299% | 459% |
| kotlin-jvm | 183% | 89% | 380% | 473% |
| Zig (superseded http.zig / 0.15.2) | 114% | 74% | 310% | 482% |

Four-round medians for the modernized Zig stack from
`results/zig-016-final/summary.json`:

| Server configuration | Write server CPU | Write client CPU | Echo server CPU | Echo client CPU |
| --- | ---: | ---: | ---: | ---: |
| zig (1 executor) | 123% | 79% | 99% | 441% |
| zig-4 (4 executors) | 187% | 98% | 329% | 484% |

Four-round Haskell medians from **2026-09-22**,
`results/haskell-multicore-final-2026-09-22/summary.json`:

| Server configuration | Write server CPU | Write client CPU | Echo server CPU | Echo client CPU |
| --- | ---: | ---: | ---: | ---: |
| haskell-2 | 149% | 102% | 194% | 439% |
| haskell-4 | 176% | 113% | 374% | 453% |

Four-round Bun medians from **2026-09-22**, with Chrome closed,
`results/bun-worker-closed-chrome-2026-09-22/summary.json`:

| Server configuration | Write server CPU | Write client CPU | Echo server CPU | Echo client CPU |
| --- | ---: | ---: | ---: | ---: |
| bun (HTTP + SQLite worker) | 135% | 52% | 103% | 258% |
| bun-baseline (single event loop, control) | 84% | 41% | 103% | 270% |
