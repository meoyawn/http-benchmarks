#!/usr/bin/env python3
"""Launch-to-bind of the standalone Bun executable; builds/migration excluded."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import statistics

PROJECT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("startup", PROJECT.parent / "measure-startup.py")
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = PROJECT / "dist/server"
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    runs = []
    for index in range(args.rounds):
        directory = output / f"run-{index + 1}"
        directory.mkdir()
        database = directory / "bench.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
        sock = Path(f"/tmp/bun-start-{os.getpid()}.sock")
        assert not os.path.lexists(sock)
        command = [str(binary), "-db", str(database), "-socket", str(sock)]
        result = startup.measure(command, f"Listening on {sock}".encode(), sock, directory, PROJECT)
        assert not sock.exists()
        runs.append(result)
        print(f"Run {index + 1}: {result['launch_to_listening_ms']:.3f} ms", flush=True)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
    result = {
        "method": "Fresh migrated databases/processes; no cache flush; first post-bind log; untimed echo readiness check",
        "sha256": digest, "runs": runs,
        "summary": {"median_ms": statistics.median(r["launch_to_listening_ms"] for r in runs),
                    "min_ms": min(r["launch_to_listening_ms"] for r in runs),
                    "max_ms": max(r["launch_to_listening_ms"] for r in runs)},
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
