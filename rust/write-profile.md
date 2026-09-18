# SQLite write-path investigation — 2026-09-18

Go's write advantage persisted across the tested HTTP libraries and schedulers.
The strongest evidence points to the cost of notifying the HTTP executor from
the SQLite writer on this macOS machine. No tested replacement improved the whole
service enough to select it over Actix. The production Rust server, SQLite policy
and published ranking remain unchanged.

The final unprofiled control uses three rotating rounds, ten seconds per endpoint,
fresh processes/databases, and the normal release binaries. Every run passed
response and database verification:

| Configuration | Write RPS, median (range) | Echo RPS, median (range) |
| --- | ---: | ---: |
| Rust Actix, 1 HTTP worker + writer | 48.52K (47.79K–48.94K) | 223.12K (221.46K–223.47K) |
| Rust Actix, 3 HTTP workers + writer | 46.31K (45.67K–46.44K) | 384.37K (377.76K–402.02K) |
| Go FastHTTP, `GOMAXPROCS=2` | 49.53K (48.24K–49.77K) | 269.20K (264.78K–270.53K) |

Go's median write lead is about 2.1%; individual run ranges overlap. This supports
retaining the simpler implementation rather than claiming a stable Rust win from
a favorable individual sample. Go's four-processor echo configuration was not
rerun here. These controls do not replace the root README's earlier full
measurement set, which also includes startup and builds. Exact samples and
artifact/build metadata are in `final-control/summary.json` under the raw-results
directory noted below.

## What the measurements isolate

A no-HTTP control runs 150,000 real transactions per sample against fresh,
migrated databases. Three rounds rotate Rust/Go and direct/queued modes. Queued
mode has 50 concurrent callers and uses each application's existing writer.
Both modes execute the same SQL, read the five RETURNING columns, and check
committed row counts, integrity and foreign keys.

| Median elapsed time per completed transaction | Rust | Go |
| --- | ---: | ---: |
| Direct database call, no HTTP or reply channel | 16.413 µs | 17.519 µs |
| Existing writer queue, 50 callers, no HTTP | 18.851 µs | 20.703 µs |

Rust wins both controls. This argues against rusqlite being the source of Go's
HTTP advantage. It also rules out a blanket claim that Go's scheduler always
handles this queue faster. These are throughput-derived times; subtracting the
rows does **not** isolate a single request's channel latency. The tight caller
loops have different scheduling and contention from a real HTTP workload.

Separate diagnostic binaries time the transaction and the immediate reply send
inside the writer during real HTTP load. Two rotating five-second runs produce:

| Mean wall time per operation, range of two runs | Rust / Actix | Go / FastHTTP |
| --- | ---: | ---: |
| Transaction, including COMMIT | 18.590–18.594 µs | 19.276–19.390 µs |
| Reply-channel send after COMMIT | 2.082–2.138 µs | 0.529–0.530 µs |

The Rust transaction is faster, but the reply send costs about 1.6 µs more. That
operation synchronously wakes Tokio's executor through mio/kqueue. It runs on the
single writer, so its cost delays the next transaction. The figures include clock
instrumentation and possible preemption; they are diagnostic, not ranking runs
or a measurement of client-observed commit-to-response latency.

The previous [JSON measurements](README.md#json-and-sqlite-api-selection) put the
complete typed write JSON work near 0.215 µs. The new stacks do not point to serde
as the dominant bottleneck. Replacing the codec is not supported by this evidence.

## Profiles

Go was recorded with fgprof 0.9.5 and, separately, the built-in CPU profiler.
Rust was recorded as an optimized executable with symbols using macOS `sample`
and Instruments' Time Profiler via `xctrace`.

- Go's fgprof stacks show handlers waiting in `postStore.create`, while the
  writer spends almost all its wall time in the SQLite C calls. Totals sum time
  over goroutines, so they can greatly exceed the recording's elapsed duration.
- Rust's `sample` stacks show the writer in SQLite COMMIT/checkpoint I/O and in
  `oneshot::send` → LocalSet wake → mio → `kevent`. Around 7% of its wall-stack
  samples were in that notification path.
- Instruments independently places the notification syscall on the writer's
  hot path (about 11% of its timed CPU samples). SQLite filesystem calls dominate
  the remaining writer samples. Only `Timer Fired` samples were counted for this
  comparison; blocked-thread `Stackshot` records were excluded.

`sample` reports native thread stacks, including waits. It does not enumerate
suspended Rust async tasks as fgprof enumerates goroutines. Instruments provides
the complementary CPU view. These percentages have different denominators and
must not be directly compared or treated as predicted speedups.

The implementation details are narrower than “FastHTTP is superior.” Go's
[runtime scheduler](https://go.dev/src/runtime/proc.go) decides when runnable work
needs another thread; Tokio's
[current-thread runtime](https://docs.rs/tokio/1.53.1/src/tokio/runtime/scheduler/current_thread/mod.rs.html)
and mio wake the Actix worker's I/O loop on this path. Changing HTTP parsers alone
did not remove that cost. Nor did changing the wake mechanism alone yield a win.

## Controlled experiments

Exploratory runs use 50 connections, fresh processes/databases, rotating order,
no HTTP warm-up, and five seconds per endpoint. Numbers below are medians of two
runs unless noted. Each row's controls ran in the same sweep; comparisons across
rows are less reliable because other desktop applications remained running.
No profiler ran during these comparisons. Builds and benchmark loads were
sequential. All database settings and per-request transactions were preserved.

| Candidate configuration | Candidate writes/s | Actix control | Go control (`GOMAXPROCS=2`) |
| --- | ---: | ---: | ---: |
| Actix entered from plain Tokio, 1 worker | 48.12K | 47.12K | — |
| Axum 0.8.9, 1 worker | 48.77K | 48.12K | 49.99K |
| Direct Hyper 1.11.1, 1 worker | 49.02K | — | 50.06K |
| Actix with multithreaded worker runtimes, 1 HTTP worker | 48.67K | 48.25K | 50.39K |
| Hyper over Smol, executor parked separately from socket polling, 1 worker | 48.80K | 48.88K | 50.69K |
| tiny_http 0.12.0, 4 blocking HTTP workers | 48.26K | 48.29K | 50.57K |
| Hyper with SQLite task on the same 2-thread pool | 47.89K | 48.16K | 50.24K |
| tiny_http, SQLite writer sends the HTTP response directly | 41.80K | 48.16K | 50.24K |
| Separate reply-relay thread, Actix 1 worker | 47.24K | 47.96K | 49.03K |
| Persistent kqueue wake event in experimental mio patch | 48.29K | 49.00K | 49.54K |
| mio's alternative pipe waker | 47.83K | 49.00K | 49.54K |
| SQLite `PREPARE_PERSISTENT` (three runs) | 47.55K | 47.99K | 49.22K |
| Write queue capacity 64 / 128 (control: 1024) | 48.39K / 48.43K | 48.49K | 50.31K |

Increasing workers also failed to help writes: Axum and Hyper were tested at
1/2/3, the modified Actix runtime at 1/3, Smol at 1/3, tiny_http at 4/8/16, and the
shared Hyper/SQLite pool at 2/3. Their echo results did not justify losing Actix's
existing three-worker configuration. For example, direct Hyper with three
workers reached 313.9K echo RPS, and Smol with three reached 218.4K; the preceding
Actix measurement was 400.4K. These are separate sweeps, not a fresh simultaneous
ranking of their best echo configurations.

The pooled SQLite experiment uses rusqlite's statement cache so the connection
can move safely between Tokio workers. Cache access and scheduling change
alongside writer placement; its result does not isolate either one's overhead.
The direct-response experiment places socket output on the writer's critical
path, and a slow reader can then hold up other writes. It was slower even with
the benchmark's fast local readers.

The kqueue patch registers one persistent user event instead of recreating a
one-shot event at each wake. It and the pipe-waker build passed the HTTP checks,
but neither improved throughput. No dependency fork or unsupported build flag
was retained. ntex and xitca were not revisited.

The stackful-coroutine alternative may_minihttp 0.1.11 was inspected but not
ranked: its published server API accepts TCP addresses, and its request body
reader only implements Content-Length framing. Adding UDS and chunked-body
support would require a framework fork before it could meet this benchmark's
existing HTTP checks.

The subsequent [MAY evaluation](may-evaluation.md) implements that fork and
compares it against fresh Actix and Go controls. It retains Actix and keeps MAY
as an opt-in experiment; the results above describe the earlier investigation.

Axum, direct Hyper, Smol, tiny_http, the shared Hyper/SQLite pool and both modified
waker builds passed the existing real-HTTP checks: 211
verified commits, 50 concurrent clients, validation fixtures, malformed input,
Unicode/NUL round trips, chunked and large bodies, all RETURNING fields, and echo
while a separate connection holds a SQLite write lock. Database checks also ran
after every measured load. The profiler-only binaries are excluded from ranking.

## Reproduce profiling and the Go control

Run Cargo from `rust/` to retain the pinned toolchain and SQLite linker settings.
Keep optimization enabled and retain symbols for readable stacks:

```sh
cd rust
python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite
env CARGO_PROFILE_RELEASE_STRIP=false CARGO_PROFILE_RELEASE_DEBUG=line-tables-only cargo build --release --locked
cd ..
python3 rust/profile.py results/rust-sample --profiler sample --seconds 10
python3 rust/profile.py results/rust-instruments --profiler instruments --seconds 10
python3 go/profile.py results/go-wall --profiler fgprof --seconds 10
python3 go/profile.py results/go-cpu --profiler cpu --seconds 10
```

Use fresh output directories. `rust/profile.py` requires macOS; Instruments needs
full Xcode. If `DEVELOPER_DIR` is unset and Xcode is installed in `/Applications`,
the script sets it only for the recorder subprocess. It does not change the
system's active developer directory. `--binary` and `--workers` select the Rust
artifact/configuration; `--gomaxprocs` selects Go's scheduler setting.

`go/profile.py` builds a disposable source copy with an opt-in profiling hook and
pinned fgprof dependency. It leaves production sources and go.mod unchanged.
Both scripts retain commands, artifact hashes, profiler output and database
verification. Go also emits `top.txt`. Rust emits `stacks.txt` or `cpu.trace`.
Their displayed throughput includes profiler overhead and must not enter the
ranking. The scripts use Python 3 and `pkgx oha` by default (`--oha` overrides it).

After building ordinary optimized binaries, run a rotating comparison without
profilers from the repository root:

```sh
python3 rust/measure-http.py results/rust-go-control --workers 1 3 --go-binary go/bench --gomaxprocs 2
```

This keeps each configuration fixed for both endpoints and records `GOMAXPROCS`
explicitly alongside binary hashes and Go build metadata. Defaults are three
rounds and ten seconds per endpoint. Restore the normal Rust release artifact
with `cd rust && cargo build --release --locked` after profiling builds.

The local raw runs, source snapshots, stage instrumentation, no-HTTP controls,
profiles and experimental dependency copies are under
`../results/rust-write-2026-09-18/` (gitignored). In particular, `core-results/`,
`stage-test/`, `profile-rust/`, `profile-go-wall/`, `profile-instruments/`, and each
`*-test/` directory retain the evidence summarized above. Raw traces can include
local paths and process environment metadata; they are not checked in.
