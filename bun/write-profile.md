# Bun write CPU investigation — 2026-09-22

The original server used about 83% of one CPU core for writes and 102% for echo.
SQLite ran synchronously on the HTTP event loop, so filesystem waits paused
request parsing and response handling too. Raising process CPU use required
overlapping useful HTTP work with the serialized database work.

## Profile and controlled experiments

macOS `sample` recorded the original executable during randomized writes. Of
6,638 main-thread stack samples, 1,369 included SQLite's `unixSync`, 1,699 ended
in `guarded_pwrite_np`, and only 12 ended in `kevent64`. These are **wall-stack
samples including waits**, not CPU-time percentages or predicted speedups.
The native stacks placed the bottleneck in SQLite/file operations.
Raw profile: `results/bun-cpu-profile-2026-09-22/stacks.txt` (gitignored).

The macOS engine has two defaults that differ from upstream SQLite:
`journal_size_limit=32768` and `checkpoint_fullfsync=1`. Reusing the WAL avoids
repeatedly truncating it to 32 KiB and extending it on subsequent writes.
SQLite documents this [WAL reuse behavior](https://sqlite.org/pragma.html#pragma_journal_size_limit).
The [full-checkpoint-sync flag](https://sqlite.org/pragma.html#pragma_checkpoint_fullfsync)
uses macOS's stronger disk-flush operation.

Two five-second runs per variant, reversing configuration order, gave:

| SQLite settings | Write RPS | Server CPU |
| --- | ---: | ---: |
| Original defaults | 12.5K | 80% |
| Reuse WAL (`journal_size_limit=-1`) | 13.7K | 81% |
| Ordinary checkpoint fsync (`checkpoint_fullfsync=0`) | 13.7K | 88% |
| Both changes | 14.4K | 88% |

The checkpoint-fsync variants relax Apple's flush policy. They were diagnostic
experiments and were **not adopted**. The final server retains the original
checkpoint syncing, `synchronous=NORMAL`, and 1,000-page automatic checkpoints.

A separate two-round comparison then tested a dedicated SQLite worker:

| Architecture | Write RPS | Server CPU | Peak RSS |
| --- | ---: | ---: | ---: |
| HTTP and SQLite on one event loop | 12.3K | 81% | 48.1 MiB |
| One event loop, reused WAL | 13.4K | 80% | 49.6 MiB |
| HTTP event loop + SQLite worker | 14.3K | 125% | 71.0 MiB |
| HTTP event loop + SQLite worker, reused WAL | 14.9K | 126% | 70.2 MiB |

These short diagnostic sweeps had Chrome open and are excluded from the README
throughput medians. All used fresh processes/databases, 50 concurrent requests,
the shared corpus, one commit per request and exact persisted-row verification.
They ran sequentially, without a profiler. Reports are in
`results/bun-cpu-settings-2026-09-22/` and
`results/bun-cpu-worker-compare-2026-09-22/` (gitignored).

## Selected implementation

One native [Bun Worker](https://bun.com/docs/runtime/workers) owns SQLite and
its reusable statements. The HTTP thread validates each post, submits it to the
worker, and awaits that request's individual committed result. Each response
contains the same five columns. The worker reports SQL errors independently,
allowing subsequent requests to succeed. Shutdown drains HTTP requests before
closing SQLite and the worker. Unexpected worker failure rejects pending work.

`journal_size_limit=-1` permits WAL-file reuse. The engine and flush policy stay
the same. Retaining the larger WAL trades some disk space for less file growth
and truncation. There is no batching, user cache or relaxed transaction isolation.
The HTTP test also verifies that echo remains responsive while 50 write requests
wait for another connection's transaction lock.

The process now has **two cooperating execution threads**. CPU measurements sum
both, so exceeding 100% means using more than one core in total. This raises
throughput and RAM use; it does not make an individual SQLite writer stop
waiting for storage or improve CPU efficiency per request.

## Final comparison with Chrome closed

After the user closed Chrome, the original executable and the final worker
implementation were measured in four alternating rounds. Every fresh process
ran ten seconds of writes then ten seconds of echo, with 50 concurrent requests
and no HTTP warm-up. This comparison separates the code change from the reduced
background browser load. Other desktop applications remained active; neither
server nor clients were CPU-pinned.

| Implementation | Write RPS | Write CPU | Write peak RSS | Echo RPS | Echo CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original control | 13.5K | 84% | 50.0 MiB | 130.3K | 103% |
| HTTP + SQLite worker, reused WAL | 16.3K | 135% | 74.1 MiB | 127.3K | 103% |

Writes improve **20.6%** in this controlled sweep. Echo is **2.2% lower** and
write RSS increases by **24.2 MiB**. The README keeps this write-optimized
configuration for both endpoints.

[measurements.json](measurements.json) retains the final control/candidate
samples, aggregates, verification and source/artifact hashes, along with compact
diagnostic results. Full reports, databases and logs are in
`results/bun-worker-closed-chrome-2026-09-22/` (gitignored).
The primary [README](README.md#results--2026-09-22) publishes the final worker
configuration, with both threads included in CPU and RSS.
