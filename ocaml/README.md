# OCaml / Cohttp-Eio / native SQLite

`POST /posts` parses JSON, validates the two fields, inserts/reuses a user, inserts
one post, consumes all five `RETURNING` columns and responds after committing.
`POST /echo` parses and serializes the two fields. HTTP/1.1 uses a Unix domain
socket and the [shared migration](../db/migrations/001_init.up.sql).

The selected stack is **Cohttp/Eio, ATDgen/Yojson and direct sqlite3-ocaml**.
Local library-selection trials included parallel HTTP and JSON at 1/2/4/8 domains
and full HTTP-to-SQLite write tests. These are the fastest
configurations among the tested candidates for this machine and workload.

## Setup

```sh
cd ocaml
./setup.sh
./toolchain.sh exec -- dune build --profile release bin/bench.exe
python3 test.py

# Once for a fresh database; do not reapply to an already migrated database.
pkgx sqlite3 -bail ../db/db.sqlite < ../db/migrations/001_init.up.sql
./_build/default/bin/bench.exe
```

Setup uses **pkgx first** for OCaml 5.5.1, opam 2.6.0, SQLite, pkg-config and GMP;
Homebrew supplies unavailable tooling (`pkgconf` on this machine), or the
bootstrap toolchain if pkgx is absent. A C compiler, Python 3 and macOS Xcode
Command Line Tools are required. The pkgx compiler lacks Flambda, so opam builds
the pinned Flambda compiler in the local switch `.tools/flambda/_opam` with root
`.tools/opam`. Shell profiles and the user's default switch are untouched.

Setup also builds [the same optimized native SQLite engine as Kotlin and Go](../db/README.md)
in `.tools/sqlite`, then installs the binding against it. `toolchain.sh` gives this
engine precedence over pkgx's generic SQLite. The executable records its native
library search path and starts directly, without pkgx, opam or Dune wrappers.
Keep `.tools/sqlite` at that location, or rebuild after moving the checkout.

| Component | Version |
| --- | --- |
| OCaml / opam | 5.5.1 with Flambda / 2.6.0 |
| Dune | 3.24.2 |
| Cohttp/Eio / Eio | 6.3.0 / 1.5 |
| ATDgen / Yojson | 4.2.0 / 3.0.0 |
| sqlite3-ocaml / SQLite | 5.4.2 / 3.53.4 |
| Re | 1.14.0 |
| Randomized load driver | [Vegeta 12.13.0](../loadgen/README.md) |

[Production dependencies](http-benchmark.opam) and their [lockfile](http-benchmark.opam.locked)
pin the library versions. Release builds use `-O3`.

`task start` builds and runs the executable. Defaults are `../db/db.sqlite`,
`/tmp/benchmark.sock`, **1 HTTP domain + 1 dedicated SQLite writer domain**, and
8 MiB minor heaps. Override them with:

```sh
./_build/default/bin/bench.exe -db /absolute/path/migrated.sqlite -socket /tmp/ocaml.sock -domains 4
```

`-domains` counts HTTP domains; the writer adds one. Both run concurrently in one
process, including when `-domains 1`. Every domain explicitly sets its minor heap:
OCaml does not inherit `Gc.set` settings in spawned domains. `-minor-heap-words`
changes it for all workers (default 1,048,576 words). One connection belongs to
one writer; the bounded 1,024-request queue resolves each response after its own
commit. Shutdown stops HTTP producers, drains accepted writes and joins the writer.

## Randomized workload results

The one- and four-HTTP-domain configurations were remeasured on 2026-09-18
alongside both Rust and both Go settings. Six rotating rounds, fresh processes
and databases, 50 concurrent requests, ten seconds of `/posts` followed by ten
seconds of `/echo`, with no HTTP warm-up. The [shared workload](../loadgen/README.md)
randomly selects from 65,536 valid email/content pairs, including Unicode and
JSON escapes. RPS, p50 and CPU are medians; RSS is the maximum sample. The same
HTTP domain count is retained for both endpoints, plus one writer domain.
These are same-host, unpinned Mac measurements. All domain counters, statuses,
exact email/content pairs and drained commit counts are verified. Production
OCaml code and SQL are unchanged.

| HTTP domains + writer | Endpoint | RPS | p50 | Peak RSS | CPU |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 + 1 | `/posts` | 29.4K | 1.306ms | 37.0 MiB | 148% |
| 1 + 1 | `/echo` | 67.6K | 0.692ms | 39.2 MiB | 94% |
| 4 + 1 | `/posts` | 23.0K | 1.685ms | 95.4 MiB | 226% |
| 4 + 1 | `/echo` | 139.2K | 0.330ms | 98.2 MiB | 316% |

The `results/random-json-2026-09-18/summary.json` report (gitignored)
retains all individual samples. Startup and build measurements below retain
their prior values.

## Multicore results

The following **historical static-payload** sweep selected the domain counts;
it is not directly comparable to the new randomized results.

Apple M1 Pro (8 performance + 2 efficiency cores), 16 GiB, macOS 26.4, 2026-09-17.
Three rotating rounds with Go and Kotlin, fresh processes/databases, 50 connections,
10 seconds per endpoint, `/posts` then `/echo`, no HTTP warm-up. Rates, p50 and CPU
are medians; RSS is the largest sample across the three runs.

| HTTP domains + writer | Posts RPS | Echo RPS | Posts CPU | Echo CPU | Posts peak RSS | Echo peak RSS | Start + bind |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 + 1 | 45.1K | 89.5K | 157% | 88% | 35.6 MiB | 37.9 MiB | 8.92ms |
| 2 + 1 | 40.7K | 126.6K | 198% | 166% | 51.5 MiB | 54.8 MiB | 9.90ms |
| 4 + 1 | 35.1K | 160.4K | 261% | 297% | 89.6 MiB | 93.2 MiB | 10.00ms |
| 8 + 1 | 31.2K | 135.7K | 312% | 470% | 147.9 MiB | 156.0 MiB | 10.68ms |

The default maximizes **writes**. Four HTTP domains improve echo, while more HTTP
workers add coordination around SQLite's one writer. CPU is whole-process CPU
time divided by wall time; **100% is one core**. Every HTTP domain reported a
nonzero request count. These results replace the old single-event-loop Lwt rows.

Clean release build: **1.12s**; warm incremental debug rebuild:
**0.521s** (three-run medians). All domain counts share build timings.
Clean removes Dune's `_build`, disables shared caching, generates
codecs, compiles application OCaml/C modules and links. Compiler, opam dependencies
and the common native SQLite engine remain prebuilt. Toolchain setup, SQLite engine
compilation, tests and environment resolution are excluded.

Debug rebuilds were remeasured on 2026-09-18 with `dune build --profile dev`, using
the **pkgx** toolchain wrapper and the pinned Flambda switch. An initial build and
no-change control warm the disposable project before timing. Each sample renames
the public `Model.new_post` type, its ATD schema declaration and consumers in other
files. Dune regenerates codecs, recompiles affected modules with `-g`/`-opaque`
and links the executable, without release `-O3`. Binary hashes verify every rebuild.
Source edits and output checks are outside timing. Samples, patches and compiler
commands are in `results/debug-rebuild-2026-09-18/` at the repository root (gitignored).

Startup is the median of five fresh processes per domain count, from native
executable launch to its post-bind listening log. It includes SQLite setup and
UDS listen, excludes build/migration, and uses an untimed echo readiness check.
Filesystem caches are not flushed. RAM includes all domains and native SQLite,
sampled every 100 ms; echo retains write-workload allocations.

## Validation and persistence

The precompiled whole-string email rule is identical to Go and Kotlin:
`^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`; content must be nonempty.
The [27 shared cases](../testdata/email-validation.json) run in all three projects.
The regex automaton and JSON scratch buffers are domain local because they have
mutable state. ATDgen uses its whole-input runtime reader; an input check rejects
Yojson comments, non-finite numbers and OCaml syntax extensions. Unknown fields
are ignored; absent/null fields become empty strings and fail write validation.

The [shared SQLite configuration](../db/README.md) sets the native compiler options,
page pool, cache, checkpoint, foreign keys and WAL/NORMAL settings equally across
all three languages. Each request executes both inserts and commits independently;
no batching or user cache. The direct binding reuses prepared statements and typed
column access. Failed writes reset statements and roll back; rollback failure marks
the connection unusable. Responses use the persisted values, including timestamps.

Twelve HTTP integration tests cover concurrent writes, rollback/recovery, all
returned columns, case-insensitive users and AUTOINCREMENT behavior, validation,
Unicode/embedded NULs/escaping, chunked requests, large nonrepeating bodies,
oversize rejection, pipelining/half-close, client disconnects, startup failures
and shutdown during a blocked write. They pass with both one and four HTTP domains.
The Cohttp adapter uses 32 KiB body reads to work around its partial-chunk offset
bug; the large-body tests guard that behavior.

## Reproduce measurements

From the repository root, after building all applications and setting `JAVA_HOME`:

```sh
python3 ocaml/measure-comparison.py results/comparison --ocaml-domains 1 2 4 8
python3 ocaml/measure-scaling.py results/ocaml-scaling --domains 1 2 4 8
python3 ocaml/measure-startup.py results/ocaml-startup --domains 1
python3 ocaml/measure-build.py results/ocaml-build
python3 measure-debug-build.py results/ocaml-debug-build --language ocaml
```

The comparison and scaling commands now use the shared randomized workload;
they do not reproduce the historical static-payload numbers above.
`measure-http.py` runs three fresh default-configuration samples. Build the
load driver first (`task load:build` at the repository root), and choose fresh
output directories. For the full six-configuration sweep:

```sh
python3 loadgen/measure.py results/random-json --rounds 6
```

The `results/random-json-2026-09-18/summary.json` report (gitignored) retains
all samples and aggregates. Full raw metrics, histograms, RSS samples, logs and
databases remain locally in `results/random-json-2026-09-18/` (gitignored).
All responses must have the expected status, and drained HTTP 201 counts must
exactly equal committed posts. Integrity, foreign keys, email/content pairs,
timestamps and AUTOINCREMENT sequences are checked after every run.
