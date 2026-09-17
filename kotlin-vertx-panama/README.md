# Kotlin Vert.x / SQLite FFM benchmark

This benchmark uses the JDK's stable Foreign Function & Memory API (Project Panama)
to call SQLite through generated jextract bindings. The server listens on a Unix
domain socket. `/posts` validates JSON and inserts a user and post in a SQLite
transaction; `/echo` parses and serializes JSON without a database operation.

## Versions

Updated on 2026-09-17 to the latest stable dependencies, using the existing local
Java 26 installation as requested. jextract is distributed only as an early-access
tool; the version below is its latest published binary. Its bundled Java 25 runs
the generator only. Compilation, tests, and the benchmark server use Java 26.

| Component | Version |
| --- | --- |
| Homebrew OpenJDK | 26.0.2.1 |
| [Gradle](https://gradle.org/releases/) | 9.7.1 |
| [Kotlin](https://kotlinlang.org/docs/releases.html) | 2.4.20 |
| [Vert.x](https://vertx.io/) | 5.1.8 |
| Netty, aligned with the Vert.x BOM | 4.2.18.Final |
| Kotlin coroutines | 1.11.0 |
| Jackson (Vert.x's compatible 2.x line) | 2.22.2 |
| [SQLite](https://sqlite.org/download.html) | 3.53.4 |
| [jextract](https://jdk.java.net/jextract/) | 25-jextract+2-4 |
| [Shadow](https://plugins.gradle.org/plugin/com.gradleup.shadow) | 9.6.1 |
| [Dependency updates plugin](https://plugins.gradle.org/plugin/io.github.ben-manes.versions) | 0.64.0 |
| JUnit / AssertJ | 6.1.3 / 3.27.7 |
| oha, through pkgx | 1.16.0 |

## Setup on macOS Apple Silicon

The following commands use fish and assume Homebrew OpenJDK 26 and SQLite are
already installed. Set `JAVA_HOME` to a Java 26 installation if the `openjdk`
Homebrew formula points to another version. `SQLITE3_HOME` supplies both the
header used for generation and the native library loaded at runtime.

```fish
cd kotlin-vertx-panama
set -gx JAVA_HOME (brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home
set -gx SQLITE3_HOME (brew --prefix sqlite)
set -gx JEXTRACT_HOME "$PWD/.tools/jextract-25"

mkdir -p .tools
curl -fL 'https://download.java.net/java/early_access/jextract/25/2/openjdk-25-jextract+2-4_macos-aarch64_bin.tar.gz' -o .tools/jextract.tar.gz
echo '3dd1dd1bde059d271739e2cc2290c64f93f85488c86c01e566c0e374eece798f  .tools/jextract.tar.gz' | shasum -a 256 -c -
tar -xzf .tools/jextract.tar.gz -C .tools

./gradlew test shadowJar
```

Gradle automatically runs jextract when its inputs change, including after
`clean`. Generated sources live in `build/generated/sources/jextract`. The Gradle
distribution is pinned with a SHA-256 checksum. `--enable-native-access=ALL-UNNAMED`
is enabled for tests and application execution; preview flags are unnecessary.

The tests create their own temporary SQLite database and Unix socket. They cover
the two endpoints, request validation, persisted content, FFM text binding, and
transaction rollback. The SQLite benchmark no longer requires a running PostgreSQL
server. The optional PostgreSQL implementation remains selectable with
`-Ddb.backend=postgres`; it was not benchmarked in this update.

## Run the server

Create the benchmark database once on a fresh checkout, using the shared migration:

```fish
"$SQLITE3_HOME/bin/sqlite3" ../db/db.sqlite < ../db/migrations/001_init.up.sql
task start
```

`task start` builds the fat JAR and uses `JAVA_HOME`. Without Task:

```fish
"$JAVA_HOME/bin/java" -server -XX:+PerfDisableSharedMem --enable-native-access=ALL-UNNAMED \
  -Djava.library.path="$SQLITE3_HOME/lib" -Dhttp.socket=/tmp/benchmark.sock \
  -jar build/libs/kotlin-vertx-panama-1.0-all.jar
```

Run from this directory so the default database path `../db/db.sqlite` resolves.
Override it with `-Ddb.path=/absolute/path.sqlite` before `-jar` if needed. SQLite
uses WAL, `synchronous=NORMAL`, foreign keys, a 10-second busy timeout, and one
writer thread with `BEGIN IMMEDIATE` transactions.

In another terminal, execute the two commands in the [root README](../README.md),
prefixing each `oha` invocation with `pkgx` if needed. The recorded run used the
exact 10-second commands, `/posts` followed by `/echo`, with default concurrency
and no separate HTTP warm-up. Stop the server with Ctrl-C after benchmarking.

## Measure build time

With the setup environment above and the benchmark server stopped:

```fish
./gradlew shadowJar
python3 measure-build.py ../results/2026-09-17-kotlin-vertx-panama
```

The script records three clean `clean shadowJar` builds and three incremental
`shadowJar` builds. Each incremental sample changes a startup log string in
`App.kt`, so Kotlin actually recompiles; the script then restores the source and
rebuilds the original artifact. Run it while no other process is editing `App.kt`.

Measurements use a warm Gradle/Kotlin daemon, offline dependencies, and
`--no-build-cache`. Wall times include Gradle configuration, compilation and fat-JAR
packaging; clean builds also include binding generation. They exclude tests and
dependency downloads. The root tables report medians: **3.88s clean** and **1.98s
incremental**. Logs and `build-times.json` are written to the gitignored `results/`
directory; they are not needed to build or run the benchmark.
