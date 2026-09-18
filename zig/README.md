# Zig HTTP benchmark

Uses Zig **0.15.2 through pkgx**, pinned `http.zig` and `zig-sqlite` revisions for
that compiler, and mvzr **0.3.12**. On 2026-09-18, refreshing pkgx still could not
resolve Zig 0.16.0; 0.15.2 was its latest available release. Dependency URLs and
content hashes are in `build.zig.zon`; the unused Zap dependency is removed.

SQLite uses the repository's **3.53.4** build and compiler flags, instead of the
binding's bundled older engine. The insert SQL and per-request transactions are
unchanged. The connection retains WAL, NORMAL synchronization, foreign keys and
a 10-second busy timeout.

The server uses one socket I/O worker and four request threads, each with a
16 KiB scratch buffer, plus Zig's concurrent allocator. SQLite operations share
one connection and remain serialized by the existing transaction mutex.
Both measured endpoints use this same configuration.

## Build and test

From this directory:

```sh
task build
task test
# Equivalent without Task:
python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite
python3 zig.py build -Doptimize=ReleaseFast
python3 zig.py build test --summary all
```

`zig.py` invokes `pkgx zig@0.15.2`. On affected Apple Silicon installations, it
selects an installed SDK containing `arm64-macos` linker stubs using a local
`.tools/` shim. This machine uses macOS SDK 15.4 because its SDK 26.5 stubs omit
that target. No system SDK or developer-directory setting is changed.

The pinned HTTP dependency incorrectly enables `TCP_NODELAY` for Unix sockets.
`build.zig` fixes that one condition in a generated copy under `.zig-cache/`;
the downloaded dependency stays intact. The randomized UDS preflight exercises it.

## Measure

After building the [load generator](../loadgen/README.md), from the repository root:

```sh
python3 loadgen/test-servers.py --config zig
python3 loadgen/measure.py results/zig-random-json --config zig --rounds 4
```

The runner supplies a fresh database and socket with `-db` and `-socket`.
It drains all client requests before stopping Zig with SIGTERM, then verifies
integrity, foreign keys, corpus contents and exact response/commit counts.
Zig currently uses the default SIGTERM termination; it does not implement a
server-side graceful shutdown handler. Logs, databases and reports are ignored.
Current HTTP results are in the [root README](../README.md).
