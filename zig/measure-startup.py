#!/usr/bin/env python3
"""Measure fresh Zig process launches through SQLite initialization and UDS bind."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import statistics

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
spec = importlib.util.spec_from_file_location("startup", ROOT / "measure-startup.py")
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--binary", type=Path, default=PROJECT / "zig-out/bin/zig")
    args = parser.parse_args()
    if args.rounds < 1 or args.workers < 1 or not args.binary.is_file():
        parser.error("positive rounds/workers and an already built binary required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary_hash = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    results = []
    for index in range(args.rounds):
        directory = output / f"run-{index + 1}"
        directory.mkdir()
        database = directory / "bench.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
        socket_path = Path(f"/tmp/zig-start-{os.getpid()}-{index}.sock")
        if os.path.lexists(socket_path):
            raise RuntimeError(f"socket exists: {socket_path}")
        command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(socket_path), "-workers", str(args.workers)]
        result = startup.measure(command, f"Listening on {socket_path}".encode(), socket_path, directory, PROJECT)
        results.append(result)
        print(f"run {index + 1}: {result['launch_to_listening_ms']:.3f} ms", flush=True)
    if binary_hash != hashlib.sha256(args.binary.read_bytes()).hexdigest():
        raise RuntimeError("binary changed during measurement")
    values = [r["launch_to_listening_ms"] for r in results]
    summary = {"method": "Wall time before Popen through receipt of the post-bind listening log; five fresh processes and migrated databases; no filesystem cache flushing; echo verification after timing",
               "binary_sha256": binary_hash, "http_workers": args.workers, "runs": results,
               "median_ms": statistics.median(values), "min_ms": min(values), "max_ms": max(values)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"median: {summary['median_ms']:.3f} ms")


if __name__ == "__main__":
    main()
