#!/usr/bin/env python3
"""Measure three clean builds and three real source-edit rebuilds, in seconds."""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory for timings and build logs")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent
    source = project / "src/main/kotlin/bench/App.kt"
    original = source.read_bytes()
    marker = b"logger.log(System.Logger.Level.INFO, uds)"
    assert original.count(marker) == 1, "Cannot locate startup log to change"
    command = ["./gradlew", "--offline", "--no-build-cache", "--console=plain"]

    def build(label, *tasks):
        with (output / (label + ".log")).open("w") as log:
            start = time.perf_counter()
            subprocess.run(command + list(tasks), cwd=project, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
            elapsed = time.perf_counter() - start
        print(f"{label}: {elapsed:.3f}s", flush=True)
        return elapsed

    # Resolve dependencies and warm the Gradle/Kotlin daemons before measuring.
    # Run ./gradlew shadowJar once online before invoking this offline script.
    build("warmup", "shadowJar")
    clean = [build(f"clean-{i}", "clean", "shadowJar") for i in range(1, 4)]
    incremental = []
    try:
        for i in range(1, 4):
            replacement = f'logger.log(System.Logger.Level.INFO, "build timing {i}: $uds")'
            source.write_bytes(original.replace(marker, replacement.encode()))
            incremental.append(build(f"incremental-{i}", "shadowJar"))
    finally:
        source.write_bytes(original)
        # The runnable artifact must match the restored source, even after failure.
        build("restore", "shadowJar")

    result = {
        "unit": "seconds",
        "command": command,
        "clean_tasks": ["clean", "shadowJar"],
        "incremental_tasks": ["shadowJar"],
        "clean": clean,
        "incremental": incremental,
        "clean_median": statistics.median(clean),
        "incremental_median": statistics.median(incremental),
        "includes": "Gradle startup/configuration, compilation and fat JAR packaging; clean also regenerates FFM bindings",
        "excludes": "Dependency downloads and tests; Gradle/Kotlin daemons are warm; build cache is disabled",
        "source_edit": "Change the startup log string in App.kt, then restore it",
    }
    (output / "build-times.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
