# Shared SQLite benchmark configuration

Go, Kotlin Panama, OCaml, Rust and C# use the same SQLite **3.53.4** amalgamation and the
configuration in [sqlite-config.json](sqlite-config.json). The selected settings
optimize this unbatched WAL workload while retaining its transaction semantics.

| Setting | All five implementations |
| --- | --- |
| C compilation | `-O3 -DNDEBUG`, same SQLite feature/optimization defines |
| Thread safety | `SQLITE_THREADSAFE=2`; one owner and `NOMUTEX` per connection |
| Memory accounting | `SQLITE_CONFIG_MEMSTATUS=0` |
| Page allocation | 1,024 preallocated slots, each 4,096 bytes + SQLite's header, aligned to 8 bytes; normal malloc fallback |
| Journal / synchronization | WAL / `NORMAL` |
| Database pages / connection cache | 4,096 bytes / `cache_size=-2000` (2,000 KiB) |
| WAL automatic checkpoint | Every 1,000 pages |
| Temporary storage / mmap | `MEMORY` / disabled |
| Foreign keys / busy timeout | On / 10,000 ms |
| Optimization | `optimize=0x10002` at startup; `optimize` at shutdown |
| Transaction | `BEGIN IMMEDIATE`, user insert, post insert with five `RETURNING` columns, `COMMIT` for each request |

No batching, user cache, deferred commits, or disabled foreign keys. WAL with
`NORMAL` retains the existing benchmark's durability policy: a power failure can
lose recent committed transactions. See SQLite's [compiler options](https://sqlite.org/compile.html)
and [runtime configuration](https://sqlite.org/c3ref/c_config_covering_index_scan.html).

[prepare-sqlite.py](prepare-sqlite.py) verifies the archive checksum and builds
Kotlin's bundled library and the OCaml/Rust/C# local native libraries from the same options.
Go's pinned Tailscale driver contains exactly the same upstream amalgamation
inside its `SQLITE_TRUNK` conditional wrapper, with the same defines already in
its cgo directives. Go builds set `CGO_CFLAGS='-O3 -DNDEBUG'`; its tests compare
the engine's reported options and connection pragmas against this configuration.
Go, Kotlin and OCaml reported identical `sqlite3_compileoption_get` lists.
Rust links this engine through rusqlite's safe API with the `bundled` feature
disabled. Its tests check every connection pragma and shared compile define.

Memory configuration happens before opening connections. Go briefly shuts down
the engine initialized by its imported driver, configures it, and initializes it
again during package initialization. OCaml, Kotlin, Rust and C# configure before their
first initialization. Page pools live for the process lifetime.

C# calls the same engine through .NET's generated `LibraryImport` interop.
Its three-function [C shim](../csharp/sqlite-config.c) only adapts the variadic
`sqlite3_config` ABI on Apple ARM64; it contains no query or transaction logic.
The engine source, compiler defines and connection settings are unchanged.
Its HTTP tests check every reported compile define and connection pragma.
