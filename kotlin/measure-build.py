#!/usr/bin/env python3
"""Measure three clean builds and three real source-edit rebuilds, in seconds."""

import argparse
import json
from pathlib import Path
import statistics
import shutil
import tempfile
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory for timings and build logs")
    parser.add_argument("--native", action="store_true", help="measure clean optimized native builds on GraalVM/JDK 25")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    original_project = Path(__file__).resolve().parent
    if args.native:
        # No source edits in this mode. Leave the last measured optimized binary
        # in the normal build directory for verification and throughput tests.
        measure(original_project, output, native=True)
        return
    with tempfile.TemporaryDirectory(prefix="kotlin-build-") as temporary:
        project = Path(temporary) / "kotlin"
        project.mkdir()
        shutil.copytree(original_project.parent / "db", project.parent / "db")
        for name in ("src", "gradle"):
            shutil.copytree(original_project / name, project / name)
        for name in ("build.gradle", "settings.gradle", "gradle.properties", "gradlew", "prepare-sqlite.py"):
            shutil.copy2(original_project / name, project / name)
        cache = ".tools/sqlite-amalgamation-3530400"
        shutil.copytree(original_project / cache, project / cache)
        shutil.copy2(original_project / (cache + ".zip"), project / (cache + ".zip"))
        measure(project, output, args.native)


def measure(project, output, native=False):
    source = project / "src/main/kotlin/bench/App.kt"
    original = source.read_bytes()
    marker = b'logger.log(System.Logger.Level.INFO, "Listening on $uds")'
    assert original.count(marker) == 1, "Cannot locate startup log to change"
    command = ["./gradlew", "--offline", "--no-build-cache", "--console=plain"]
    task = "nativeCompile" if native else "shadowJar"
    if native:
        command.append("-PjavaVersion=25")

    def build(label, *tasks):
        with (output / (label + ".log")).open("w") as log:
            start = time.perf_counter()
            subprocess.run(command + list(tasks), cwd=project, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
            elapsed = time.perf_counter() - start
        print(f"{label}: {elapsed:.3f}s", flush=True)
        return elapsed

    # Resolve dependencies and warm the Gradle/Kotlin daemons before measuring.
    # Run the selected release task once online before this offline script.
    build("warmup", "classes" if native else task)
    clean = [build(f"clean-{i}", "clean", task) for i in range(1, 4)]
    incremental = []
    try:
        for i in ([] if native else range(1, 4)):
            replacement = f'logger.log(System.Logger.Level.INFO, "build timing {i}: $uds")'
            source.write_bytes(original.replace(marker, replacement.encode()))
            incremental.append(build(f"incremental-{i}", task))
    finally:
        if not native:
            source.write_bytes(original)

    result = {
        "unit": "seconds",
        "command": command,
        "clean_tasks": ["clean", task],
        "incremental_tasks": [] if native else [task],
        "clean": clean,
        "incremental": incremental,
        "clean_median": statistics.median(clean),
        "incremental_median": statistics.median(incremental) if incremental else None,
        "includes": "Gradle startup/configuration, SQLite C, jextract, Kotlin/Java compilation, " +
                    ("GraalVM -O3 native compilation/linking, stripping and macOS signing" if native else "fat JAR packaging"),
        "excludes": "Dependency downloads, tests and source copying; Gradle/Kotlin daemons are warm; build cache is disabled",
        "source_edit": "None; native clean builds replace build/ with the last measured artifact" if native else
                       "Change the startup log string in App.kt in a disposable source copy; original source and runnable artifact are untouched",
    }
    (output / "build-times.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
