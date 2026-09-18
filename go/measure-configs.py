#!/usr/bin/env python3
"""Measure the Go release at multiple GOMAXPROCS settings with fresh processes/databases."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sqlite3
import statistics
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
sys.path.insert(0, str(ROOT / "ocaml"))
import workload

spec = importlib.util.spec_from_file_location("validation", ROOT / "ocaml/measure-http.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--binary", type=Path, default=PROJECT / "bench")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--gomaxprocs", nargs="+", type=int, default=[2, 4])
    args = parser.parse_args()
    if args.rounds < 1 or any(procs < 1 for procs in args.gomaxprocs):
        parser.error("rounds and GOMAXPROCS values must be positive")
    if len(set(args.gomaxprocs)) != len(args.gomaxprocs):
        parser.error("GOMAXPROCS values must be unique")
    binary = args.binary.resolve()
    artifacts = {"release": {"bytes": binary.stat().st_size,
                              "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}}
    variants = [("release", procs) for procs in args.gomaxprocs]
    random.Random(20260918).shuffle(variants)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {f"{name}-p{procs}": {endpoint: [] for endpoint in workload.PAYLOADS}
               for name, procs in variants}
    for index in range(args.rounds):
        shift = index * max(1, len(variants) // args.rounds) % len(variants)
        order = variants[shift:] + variants[:shift]
        for name, procs in order:
            label = f"{name}-p{procs}"
            directory = output / f"{label}-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            sock = Path(f"/tmp/go-stack-{os.getpid()}.sock")
            if os.path.lexists(sock):
                raise RuntimeError(f"socket already exists: {sock}")
            assert hashlib.sha256(binary.read_bytes()).hexdigest() == artifacts[name]["sha256"]
            print(f"{label} round {index + 1}", flush=True)
            # Keep a record of external machine activity for the run's validity review.
            (directory / "processes.txt").write_text(subprocess.check_output(
                ["ps", "-Ao", "pid,ppid,pcpu,comm"], text=True))
            env = dict(os.environ, GOMAXPROCS=str(procs))
            command = [str(binary), "-db", str(database), "-socket", str(sock)]
            (directory / "command.json").write_text(json.dumps({"command": command, "GOMAXPROCS": procs}))
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    workload.wait_ready(server, sock)
                    for endpoint in workload.PAYLOADS:
                        result = workload.measure(server, endpoint, sock, directory / endpoint,
                                                  ["pkgx", "oha"], args.duration)
                        result["cpu_method"] = "Whole-process ps CPU-time delta / wall time around the load, across every server thread; 100% = one core"
                        (directory / endpoint).with_suffix(".metrics.json").write_text(json.dumps(result, indent=2) + "\n")
                        result.pop("rss_samples")
                        results[label][endpoint].append(result)
                finally:
                    workload.stop(server)
            if server.returncode:
                raise RuntimeError(f"{label} exited with {server.returncode}")
            check = validation.verify(database, results[label]["posts"][-1]["status_codes"]["201"])
            (directory / "verification.json").write_text(json.dumps(check, indent=2))
            (output / "runs.json").write_text(json.dumps(results, indent=2))
    summary = {name: {endpoint: {
        "rps": statistics.median(r["requests_per_second"] for r in runs),
        "min_rps": min(r["requests_per_second"] for r in runs),
        "max_rps": max(r["requests_per_second"] for r in runs),
        "p50_ms": statistics.median(r["p50_seconds"] * 1000 for r in runs),
        "rss_mib": max(r["peak_sampled_rss_mib"] for r in runs),
        "cpu_percent": statistics.median(r["server_cpu_percent"] for r in runs),
    } for endpoint, runs in endpoints.items()} for name, endpoints in results.items()}
    record = {"rounds": args.rounds, "duration": args.duration, "cases": variants,
              "artifacts": artifacts, "summary": summary}
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
