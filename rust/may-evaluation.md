# MAY coroutine evaluation

This implements [issue #12](https://github.com/meoyawn/http-benchmarks/issues/12),
following the [Actix/Go write investigation](write-profile.md). The experimental
backend is available separately from the default Actix build.

## Implementation

The [personal fork](https://github.com/meoyawn/may_minihttp/tree/codex/uds-bounded-http)
is cloned at `/Users/meoyawn/Developer/may_minihttp`. Cargo pins revision
`2876477511c91d04580068a66c5a8430b106b914`; no local-path dependency or MAY runtime
patch is required. The runtime is MAY 0.3.51, with its default work-stealing,
I/O cancellation and timeout features. The fork adds a separate buffered Unix
HTTP API; its older TCP API remains unchanged.

The Unix server refuses existing socket paths, bounds headers and decoded
bodies, validates Content-Length and chunked framing, handles trailers and
keep-alive/pipelining, and completes partial socket writes. Shutdown cancels only
the listener, interrupts idle/partial request reads, waits for in-flight handlers,
and removes only the socket inode it owns. The date cache no longer races a
background writer against HTTP response encoders.

Both Rust backends share the same typed models, validation, database connection,
prepared statements and transaction implementation in `src/lib.rs`. The only
writer-path difference is the reply channel: Tokio oneshot for Actix, MAY SPSC
for the coroutine backend. Each handler suspends in `recv()` without blocking
its scheduler thread; SQLite remains on one dedicated OS thread. Every reply
is sent immediately after that request's commit. There is no transaction
batching, grouped reply sending, reply polling, or user cache.

`-workers` selects MAY scheduler workers. The coroutine pool holds 64 reusable
stacks; each stack is configured as 65,536 machine words (512 KiB on this
machine). Worker CPU pinning is disabled. The same setting is retained for
both endpoints. The dedicated SQLite writer and MAY's timer thread are additional
OS threads. `may-release` inherits native CPU targeting, optimization level 3,
fat LTO, one codegen unit and stripping from `release`, but uses `panic=unwind`:
MAY's listener cancellation aborts the process under `panic=abort`. The fork
rejects that incompatible panic mode at bind time.

## Correctness

The fork's seven unit tests cover response encoding, malformed/overflowing
lengths, ambiguous framing, chunk decoding at every input split, trailer/header
limits, decoded-body limits and preservation of pipelined bytes. Its library
passes Clippy with warnings denied.

The benchmark retains seven database/JSON tests for both backends. The existing
real HTTP suite verifies 211 commits with 50 concurrent clients, shared email
fixtures, invalid JSON/types/UTF-8, large and chunked bodies, Unicode/NUL text,
five RETURNING columns, commit visibility and echo while SQLite is locked.
`test-may-http.py` additionally checks malformed framing, exact/oversized bodies,
fragmented chunks and trailers, persistent connections, pipelining, backpressure,
half-close, refusal of existing paths, replacement-path ownership and draining
50 accepted writes during SIGTERM while idle/partial requests are connected.

## Results and decision

**Retain Actix as the default.** MAY's one-worker write median is **55.12K/s**,
Go's two-processor control is **54.94K/s**, and Actix's one-worker control is
**55.14K/s**. MAY's 0.3% lead over Go is smaller than its own run variation, with
overlapping ranges: **54.31–55.13K** for MAY, **54.87–55.16K** for Go and
**54.81–55.33K** for Actix. This does not establish a reliable win over Go or an
improvement over the existing Rust backend.

MAY improves single-worker echo throughput by 7.8%, but its p50 is higher.
With three workers, echo throughput is essentially unchanged and MAY uses more
CPU. Startup and executable size improve, but the complete results do not
justify replacing Actix for the write-throughput objective. The experimental
backend remains opt-in; the published historical ranking is retained.

These are unprofiled runs on the M1 Pro/macOS host described in the root README.
Other desktop applications remained running. Absolute rates changed between
short exploratory sweeps and this final sweep; compare controls within the same
sweep, rather than treating the older 48–51K results as a framework speedup.
All 18 fresh-process/database runs passed response and database verification.
RPS, p50, p99 and CPU are three-run medians; RSS is the maximum sampled value.
100% CPU means one core. Full samples, configuration, hashes and verification
counts are checked in as [may-results.json](may-results.json).

### Writes

| Configuration | Requests/sec | p50 ms | p99 ms | CPU | Peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| MAY, 1 worker | 55.12K | 0.813 | 1.780 | 134% | 13.25 |
| Actix, 1 worker | 55.14K | 0.812 | 1.777 | 140% | 11.12 |
| Go, GOMAXPROCS=2 | 54.94K | 0.812 | 1.787 | 146% | 24.06 |
| MAY, 3 workers | 53.68K | 0.834 | 1.803 | 150% | 13.91 |
| Actix, 3 workers | 53.97K | 0.830 | 1.810 | 156% | 11.91 |
| Go, GOMAXPROCS=4 | 47.32K | 0.951 | 2.078 | 219% | 25.27 |

### Echo

| Configuration | Requests/sec | p50 ms | p99 ms | CPU | Peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| MAY, 1 worker | 251.13K | 0.195 | 0.298 | 95% | 13.34 |
| Actix, 1 worker | 233.05K | 0.163 | 0.390 | 95% | 11.48 |
| Go, GOMAXPROCS=2 | 273.69K | 0.166 | 0.414 | 188% | 24.14 |
| MAY, 3 workers | 401.00K | 0.107 | 0.422 | 254% | 14.05 |
| Actix, 3 workers | 403.25K | 0.098 | 0.442 | 238% | 12.20 |
| Go, GOMAXPROCS=4 | 349.75K | 0.129 | 0.391 | 327% | 25.47 |

### Startup and artifacts

Five fresh launches per configuration, measured through the post-bind listening
log with an untimed echo readiness check. These are cached-filesystem process
starts; builds and migration are excluded. Startup ran separately from the HTTP
loads. Actix's samples were bimodal, so the range is shown alongside the median.

| Configuration | Startup median ms | Range ms | Executable MiB |
| --- | ---: | ---: | ---: |
| MAY, 1 worker | 5.164 | 5.057–5.739 | 1.54 |
| Actix, 1 worker | 13.037 | 5.503–14.528 | 1.70 |
| Go, GOMAXPROCS=2 | 7.480 | 7.178–8.140 | 7.78 |
| MAY, 3 workers | 5.248 | 5.181–5.532 | 1.54 |
| Actix, 3 workers | 12.295 | 5.434–13.076 | 1.70 |
| Go, GOMAXPROCS=4 | 7.349 | 7.212–7.449 | 7.78 |

Both Rust executables additionally require the same shared SQLite library;
Go statically links SQLite. SHA-256 hashes of the actual measured files:

| Artifact | SHA-256 |
| --- | --- |
| MAY executable | `46d50b917804cdb98377bebdcae6291656ff721d8e895bb4a2b6eea7a5adf4c4` |
| Actix executable | `3f617d8a13263b941c2b8b5de0404555bbfcc9d858e40b074bc2f8883f848438` |
| Go executable | `9cdb319571df3b635d39f1fa1395ade3c1367e0670aad40949896b2ad84fa8f5` |
| Shared SQLite | `e3913b9299b1769a728fbd17a9c552dd9e42d442f718d1dcc3093ed2759b9057` |

## Profiling and rejected experiments

An eight-second macOS `sample` recording of an optimized MAY binary with debug
symbols ran separately from the ranking workload. SQLite commit/filesystem
operations dominate the writer. About 7% of its native wall-stack samples still
land in the SPSC reply notification's `kevent` call. These include blocked time
and do not measure client-observed commit-to-response latency or predict a
speedup. The profiler's database passed verification; its throughput is excluded
from ranking.

Three exploratory sweeps used two rotating rounds, 50 connections and five
seconds per endpoint. Every row compares against its own unmodified MAY and Go
controls. None justified carrying the extra change into the final build:

| Experiment | Candidate writes/sec | MAY control | Go control |
| --- | ---: | ---: | ---: |
| Suppress redundant scheduler wake syscalls | 54.56K | 54.72K | 54.60K |
| Persistent MPSC replies and handler-side input cleanup | 53.47K | 54.85K | 54.52K |
| Disable MAY work stealing | 53.92K | 54.37K | 54.14K |

The MPSC experiment changed channel type/reuse and input cleanup together; it
cannot isolate their individual costs. The local scheduler prototype and these
reply changes are absent from the pinned implementation. The initial 1/2/3-worker
pilot selected one worker for writes and three for echo; each selected setting
remains fixed for both endpoints in the final measurements.

## Reproduce

Build and run from `rust/` to use the pinned compiler and SQLite linker settings:

```sh
python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite
cargo build --release --locked
cargo build --profile may-release --no-default-features --features may --locked --bin may-benchmark
cargo test --release --locked
cargo test --profile may-release --no-default-features --features may --locked
cargo clippy --profile may-release --all-features --all-targets --locked -- -D warnings
python3 test-http.py --binary target/may-release/may-benchmark
python3 test-http.py --binary target/may-release/may-benchmark --workers 3
python3 test-may-http.py
python3 test-may-http.py --workers 3
```

From the repository root, build the Go control separately and run the comparison:

```sh
cd go
env CGO_CFLAGS='-O3 -DNDEBUG' go build -trimpath -ldflags='-s -w' -o bench .
cd ..
python3 rust/measure-http.py results/may-comparison \
  --variant may-1 rust/target/may-release/may-benchmark 1 \
  --variant may-3 rust/target/may-release/may-benchmark 3 \
  --variant actix-1 rust/target/release/rust-benchmark 1 \
  --variant actix-3 rust/target/release/rust-benchmark 3 \
  --go-binary go/bench --gomaxprocs 2 4
python3 rust/measure-startup.py results/may-startup --binary rust/target/may-release/may-benchmark --workers 1
```

The default comparison uses three rotating rounds, fresh processes and databases,
50 connections, and 10 seconds each for posts then echo. Run builds, profiling
and benchmark loads sequentially. Local evidence is under
`results/may-2026-09-18/` (gitignored), including the early pilot, diagnostic
profile, rejected experiments and final artifact copies.
