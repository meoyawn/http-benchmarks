#!/usr/bin/env python3
"""Measure three builds with empty Go caches and three real source-edit rebuilds."""

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent
    # Resolve dependencies once outside the timed interval.
    subprocess.run(["go", "mod", "download"], cwd=project, check=True)
    env = dict(os.environ, GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local", CGO_CFLAGS="-O3 -DNDEBUG")
    command = ["go", "build", "-trimpath", "-ldflags=-s -w", "-o", "bench", "."]

    def build(label, directory, cache):
        with (output / f"{label}.log").open("w") as log:
            start = time.perf_counter()
            subprocess.run(command, cwd=directory, env=dict(env, GOCACHE=str(cache)),
                           stdout=log, stderr=subprocess.STDOUT, check=True)
            elapsed = time.perf_counter() - start
        print(f"{label}: {elapsed:.3f}s", flush=True)
        return elapsed

    # Work on a disposable copy so edits and cache clearing cannot interfere with
    # a running server, the developer's cache, or concurrent source edits.
    with tempfile.TemporaryDirectory(prefix="go-build-") as temporary:
        root = Path(temporary)
        copy = root / "source"
        copy.mkdir()
        for path in project.iterdir():
            if path.suffix == ".go" or path.name in {"go.mod", "go.sum"}:
                shutil.copy2(path, copy / path.name)
        clean = [build(f"clean-{i}", copy, root / f"cache-{i}") for i in range(1, 4)]
        cache = root / "warm-cache"
        build("warmup", copy, cache)
        source = copy / "main.go"
        original = source.read_text()
        marker = '"Listening on %s (SQLite %s)"'
        if original.count(marker) != 1:
            raise RuntimeError("cannot find startup log for incremental source edit")
        incremental = []
        for i in range(1, 4):
            source.write_text(original.replace(marker, f'"Listening on %s (SQLite %s), build timing {i}"'))
            incremental.append(build(f"incremental-{i}", copy, cache))
    result = {
        "unit": "seconds", "command": command, "CGO_CFLAGS": env["CGO_CFLAGS"],
        "go_version": subprocess.check_output(["go", "version"], text=True).strip(),
        "clean": clean, "incremental": incremental,
        "clean_median": statistics.median(clean), "incremental_median": statistics.median(incremental),
        "clean_definition": "Fresh GOCACHE per build: compile standard library, dependencies, bundled SQLite C source, application, and link",
        "incremental_definition": "Warm GOCACHE; change startup log string in a disposable source copy, recompile application and relink",
        "excludes": "Dependency/toolchain downloads, tests, and copying source; original source and default GOCACHE are untouched",
    }
    (output / "build-times.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
