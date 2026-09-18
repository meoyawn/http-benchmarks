#!/usr/bin/env python3
"""Measure launch-to-bind for the same Go executable at two GOMAXPROCS values."""
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
    parser.add_argument("--binary", type=Path, default=PROJECT / "bench")
    parser.add_argument("--gomaxprocs", nargs="+", type=int, default=[2, 4])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--marker", default="Listening on ", help="post-bind log prefix, immediately before socket path")
    args = parser.parse_args()
    if args.rounds < 1 or any(p < 1 for p in args.gomaxprocs):
        parser.error("rounds and GOMAXPROCS values must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    artifact_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    results = {str(p): [] for p in args.gomaxprocs}
    for index in range(args.rounds):
        order = args.gomaxprocs if index % 2 == 0 else list(reversed(args.gomaxprocs))
        for procs in order:
            directory = output / f"p{procs}-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
            sock = Path(f"/tmp/go-startup-{os.getpid()}-{procs}-{index}.sock")
            if os.path.lexists(sock):
                raise RuntimeError(f"socket already exists: {sock}")
            command = [str(binary), "-db", str(database), "-socket", str(sock)]
            result = startup.measure(command, (args.marker + str(sock)).encode(), sock,
                                     directory, PROJECT, dict(os.environ, GOMAXPROCS=str(procs)))
            result["GOMAXPROCS"] = procs
            results[str(procs)].append(result)
            print(f"GOMAXPROCS={procs} run {index + 1}: {result['launch_to_listening_ms']:.3f} ms", flush=True)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == artifact_hash
    report = {"method": "Fresh migrated database/process per run, alternating GOMAXPROCS; launch to receipt of post-bind log; untimed echo readiness check; no cache flush",
              "sha256": artifact_hash, "rounds": args.rounds, "runs": results,
              "summary": {p: {"median_ms": statistics.median(r["launch_to_listening_ms"] for r in runs),
                               "min_ms": min(r["launch_to_listening_ms"] for r in runs),
                               "max_ms": max(r["launch_to_listening_ms"] for r in runs)}
                          for p, runs in results.items()}}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
