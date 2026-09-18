#!/usr/bin/env python3
"""Compare HTTP domain counts with one dedicated SQLite writer in each process."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess

import workload

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
spec = importlib.util.spec_from_file_location("ocaml_measure", PROJECT / "measure-http.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--binary", type=Path, default=PROJECT / "_build/default/bin/bench.exe")
    parser.add_argument("--domains", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--minor-heap-words", type=int, default=1048576)
    args = parser.parse_args()
    if args.rounds < 1 or min(args.domains) < 1 or len(set(args.domains)) != len(args.domains):
        parser.error("positive --rounds and unique positive --domains are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {domains: {endpoint: [] for endpoint in workload.PAYLOADS} for domains in args.domains}
    for index in range(args.rounds):
        shift = index % len(args.domains)
        for domains in args.domains[shift:] + args.domains[:shift]:
            directory = output / f"domains-{domains}-run-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            socket = Path(f"/tmp/ocaml-scale-{os.getpid()}.sock")
            if os.path.lexists(socket):
                raise RuntimeError(f"socket exists: {socket}")
            command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(socket),
                       "-domains", str(domains), "-minor-heap-words", str(args.minor_heap_words)]
            (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                try:
                    workload.wait_ready(server, socket)
                    for endpoint in workload.PAYLOADS:
                        row = workload.measure(server, endpoint, socket, directory / endpoint, [str(ROOT / "loadgen/bombard")], args.duration)
                        row.pop("rss_samples")
                        results[domains][endpoint].append(row)
                finally:
                    workload.stop(server)
            if server.returncode:
                raise RuntimeError(f"server did not stop cleanly: {server.returncode}")
            check = validation.verify(database, results[domains]["posts"][-1]["status_codes"]["201"])
            check["http_domain_requests"] = {int(domain): int(count) for domain, count in
                re.findall(r"HTTP domain (\d+): (\d+) requests", (directory / "server.log").read_text())}
            if len(check["http_domain_requests"]) != domains or min(check["http_domain_requests"].values()) == 0:
                raise RuntimeError("not all HTTP domains served requests")
            (directory / "verification.json").write_text(json.dumps(check, indent=2) + "\n")
    summary = {domains: {endpoint: {
        "rps_median": statistics.median(r["requests_per_second"] for r in runs),
        "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in runs),
        "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in runs),
        "server_cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in runs),
    } for endpoint, runs in endpoints.items()} for domains, endpoints in results.items()}
    record = {"method": "Rotating-order fresh processes and databases; one SQLite writer domain plus the indicated HTTP domains; same payloads and 50 connections; posts then echo; no warm-up",
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "rounds": args.rounds, "duration": args.duration, "minor_heap_words": args.minor_heap_words,
              "runs": results, "summary": summary}
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
