#!/usr/bin/env python3
"""Measure an already built native executable with the root README protocol."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shlex
import sqlite3
import statistics
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
spec = importlib.util.spec_from_file_location("startup", ROOT / "measure-startup.py")
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--native", type=Path, default=PROJECT / "build/native/nativeCompile/kotlin-bench")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--startup-rounds", type=int, default=5)
    parser.add_argument("--loadgen", default=str(Path(__file__).resolve().parent.parent / "loadgen/bombard"))
    args = parser.parse_args()
    binary = args.native.resolve()
    if not binary.is_file() or min(args.rounds, args.startup_rounds) < 1:
        parser.error("a built executable and positive round counts are required")
    original_hash = digest(binary)
    sidecars = sorted([*binary.parent.glob("*.dylib"), *binary.parent.glob("*.so")])
    sidecar_hashes = {p.name: digest(p) for p in sidecars}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runs = {endpoint: [] for endpoint in ("posts", "echo")}
    checks = []
    for index in range(args.rounds):
        directory = output / f"http-{index + 1}"
        subprocess.run([sys.executable, str(PROJECT / "measure-http.py"), str(directory),
                        "--native", str(binary), "--socket", f"/tmp/kotlin-native-{os.getpid()}.sock",
                        "--loadgen", args.loadgen], check=True)
        for endpoint in runs:
            result = json.loads((directory / f"{endpoint}.metrics.json").read_text())
            result.pop("rss_samples")
            runs[endpoint].append(result)
        checks.append(json.loads((directory / "verification.json").read_text()))
    starts = []
    for index in range(args.startup_rounds):
        directory = output / f"startup-{index + 1}"
        directory.mkdir()
        database = directory / "bench.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
        uds = Path(f"/tmp/kotlin-native-start-{os.getpid()}-{index}.sock")
        if os.path.lexists(uds):
            raise RuntimeError(f"socket already exists: {uds}")
        command = [str(binary), f"-Ddb.path={database}", f"-Dhttp.socket={uds}"]
        result = startup.measure(command, f"Listening on {uds}".encode(), uds, directory, PROJECT)
        starts.append(result)
    assert digest(binary) == original_hash, "artifact changed during measurement"
    assert sidecar_hashes == {p.name: digest(p) for p in sidecars}, "runtime libraries changed"
    summary = {endpoint: {
        "rps_median": statistics.median(r["requests_per_second"] for r in samples),
        "rps_min": min(r["requests_per_second"] for r in samples),
        "rps_max": max(r["requests_per_second"] for r in samples),
        "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in samples),
        "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in samples),
        "server_cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in samples),
    } for endpoint, samples in runs.items()}
    summary["startup_ms_median"] = statistics.median(r["launch_to_listening_ms"] for r in starts)
    summary["executable_mib"] = binary.stat().st_size / 2**20
    summary["distribution_mib"] = (binary.stat().st_size + sum(p.stat().st_size for p in sidecars)) / 2**20
    record = {"platform": platform.platform(), "artifact_sha256": original_hash,
              "method": "Three fresh processes/databases by default; 50 connections; posts then echo, 10s each; no HTTP warm-up; medians except maximum sampled RSS; 100% CPU equals one core",
              "artifact_bytes": binary.stat().st_size, "runs": runs, "verification": checks,
              "runtime_libraries": {p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in sidecars},
              "load_generator": subprocess.check_output(shlex.split(args.loadgen) + ["--version"], text=True).strip(),
              "startup_runs": starts, "summary": summary,
              "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sorted(
                  list((PROJECT / "src/main").rglob("*")) + [PROJECT / "build.gradle", PROJECT / "gradle.properties"])
                  if p.is_file()},
              "sqlite_configuration": json.loads((ROOT / "db/sqlite-config.json").read_text())}
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
