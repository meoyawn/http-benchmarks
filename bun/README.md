# Bun HTTP and SQLite

Bun **1.4.2**, native [`Bun.serve` routes](https://bun.com/docs/runtime/http/server),
[`bun:sqlite`](https://bun.com/docs/runtime/sqlite), and **Valibot 1.5.0**.
Valibot is the only application dependency. It validates nonempty content and
the repository's whole-string ASCII email rule; all shared fixtures pass.
`/echo` parses JSON and serializes its two string fields without post validation.

One JavaScript event loop owns one synchronous SQLite connection. Prepared
statements and the transaction wrapper are created once. Every `/posts` request
runs an immediate transaction, inserts its user if needed, inserts its post with
five `RETURNING` columns, commits, and then returns HTTP 201. SQL failures roll
back the whole transaction. There are no worker threads, batches or user caches.
SQLite work blocks this event loop, including any wait for an external writer.

Connection settings match [the shared benchmark](../db/README.md): WAL,
`synchronous=NORMAL`, foreign keys, 10-second busy timeout, 2,000 KiB cache,
1,000-page automatic checkpoint, `temp_store=MEMORY`, mmap disabled, and startup/
shutdown optimization. Bun uses its default SQLite engine: **system SQLite on
macOS (3.51.0 on the measured host)**, rather than the other seven implementations'
custom SQLite 3.53.4.
The engine's compile options, memory accounting and allocator settings are not
overridden. This is a material difference when comparing write throughput.

## Run and verify

From the repository root, install the pinned dependencies and build:

```sh
task bun:setup
task bun:check
task bun:build
task bun:test
python3 loadgen/test-servers.py --config bun
```

`bun/bun.lock` is the text lockfile; installation uses `--frozen-lockfile`.
The executable contains the Bun runtime, application and Valibot. No separate
Bun installation is required to launch it, but macOS system libraries, including
SQLite, remain external. It runs JavaScript with Bun's runtime; `--compile`
packages the runtime rather than compiling the application to native machine code.

The database must be migrated before launch. To create a fresh local database
and run over a Unix socket from the repository root:

```sh
sqlite3 /tmp/bun-benchmark.sqlite < db/migrations/001_init.up.sql
./bun/dist/server -db /tmp/bun-benchmark.sqlite -socket /tmp/benchmark.sock
```

Use a new database path when repeating migration. Both `-db` / `-socket` and
`--db` / `--socket` work. `task bun:start` runs TypeScript directly with watch
mode, using the existing `db/db.sqlite` database and `/tmp/benchmark.sock`.
SIGINT/SIGTERM drain requests, finalize statements, close SQLite and remove the
socket. An occupied socket is rejected. Request bodies are limited to 2 MiB.

The HTTP test exercises shared email fixtures, malformed JSON, chunked requests,
large Unicode/NUL content, 50 concurrent clients, all returned columns,
visibility through a separate database connection before each response is
counted, transaction rollback/recovery, socket collisions and both stop signals.
The shared preflight adds 64 randomized round trips per endpoint and database
integrity checks.

## Reproduce measurements

Build and test first, then run these sequentially with fresh output directories:

```sh
task load:build
python3 bun/measure-build.py results/bun-build
python3 bun/measure-startup.py results/bun-startup
python3 loadgen/measure.py results/bun-http --rounds 4 --config bun
```

The [shared randomized workload](../loadgen/README.md) uses four fresh processes
and databases, ten seconds of writes followed by ten seconds of echo per round,
50 concurrent requests across five client processes, and no HTTP warm-up. All
outstanding requests drain; every committed row is checked against the corpus.
The same standalone executable serves both endpoints. These are same-host,
unpinned measurements on the README's M1 Pro, with normal desktop applications
active. Bun is measured separately from the other stacks.

Startup is launch-to-first-post-bind-log across five fresh processes, with
migration excluded and an untimed echo readiness check. Filesystem caches stay
warm. Clean release builds remove `dist` and run `bun run build`: transpile,
bundle/minify the application and Valibot, and package the prebuilt Bun runtime.
Runtime/SQLite compilation, installation, downloads and type checking are excluded.

The **warm debug rebuild is 0s**: `bun --watch src/server.ts` runs TypeScript
directly, so development requires no separate build or bundle. The clean release
build column reports a three-run median.

## Results — 2026-09-22

| Endpoint | RPS median (range) | p50 | p99 | Peak RSS | Server CPU | Client CPU |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `/posts` | 13.3K (13.2K–13.5K) | 2.716ms | 10.739ms | 50.8 MiB | 83% | 40% |
| `/echo` | 128.4K (126.6K–128.8K) | 0.353ms | 0.900ms | 53.7 MiB | 102% | 271% |

Startup median: **17.37ms** (16.34–30.17ms). Clean standalone build:
**0.152s**. Dev/debug rebuild: **0s** (no build step). Release executable:
**62,226,930 bytes / 59.34 MiB**, including the Bun runtime and Valibot.

All responses had the expected status. The four databases contained exactly
**135,294 / 133,795 / 132,148 / 133,193** committed posts, equal to the drained
201 responses, and passed integrity, foreign-key, timestamp, corpus-content and
AUTOINCREMENT checks. All processes exited cleanly and removed their sockets.

[measurements.json](measurements.json) retains aggregates, individual samples,
correctness results, runtime/SQLite details and source/artifact hashes. Raw logs,
databases and sampling records stay in the ignored directories
`results/bun-final-2026-09-22/`, `results/bun-startup-2026-09-22/` and
`results/bun-build-final-2026-09-22/`.
