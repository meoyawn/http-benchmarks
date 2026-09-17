#!/usr/bin/env python3
"""Measure process launch through SQLite initialization and UDS bind/listen."""

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
    parser.add_argument("--domains", type=int, default=1)
    parser.add_argument("--binary", type=Path, default=PROJECT / "_build/default/bin/bench.exe")
    args = parser.parse_args()
    if args.rounds < 1 or args.domains < 1 or not args.binary.is_file():
        parser.error("positive --rounds and an already built --binary are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for index in range(args.rounds):
        directory = output / f"run-{index + 1}"
        directory.mkdir()
        database = directory / "bench.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
        socket = Path(f"/tmp/ocaml-start-{os.getpid()}-{index}.sock")
        if os.path.lexists(socket):
            raise RuntimeError(f"socket path already exists: {socket}")
        command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(socket), "-domains", str(args.domains)]
        result = startup.measure(command, f"Listening on {socket}".encode(), socket, directory, PROJECT)
        results.append(result)
        print(f"run {index + 1}: {result['launch_to_listening_ms']:.3f} ms", flush=True)
    values = [r["launch_to_listening_ms"] for r in results]
    summary = {
        "method": "Wall time immediately before Popen to receipt of post-bind listening log; fresh processes and migrated databases; migration/builds excluded; echo check after timing; no filesystem cache flushing",
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "http_domains": args.domains,
        "runs": results,
        "median_ms": statistics.median(values), "min_ms": min(values), "max_ms": max(values),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"median: {summary['median_ms']:.3f} ms")


if __name__ == "__main__":
    main()
