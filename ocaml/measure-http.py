#!/usr/bin/env python3
"""Run fresh OCaml processes with the existing Go workload/RSS sampler."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sqlite3
import statistics
import subprocess
import re

import workload

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(database, completed):
    with sqlite3.connect(database) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
        users = db.execute("SELECT count(*) FROM users").fetchone()[0]
        invalid = db.execute("SELECT count(*) FROM posts WHERE content IS NOT 'oha benchmark' OR user_id IS NOT 1").fetchone()[0]
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()[0]
    if integrity != [("ok",)] or foreign_keys or invalid or users != 1:
        raise RuntimeError("database integrity/content check failed")
    if not completed <= posts <= completed + 50 or sequence != posts:
        raise RuntimeError(f"commit/sequence mismatch: {completed=}, {posts=}, {sequence=}")
    return {"integrity": integrity, "foreign_keys": foreign_keys, "posts": posts,
            "users": users, "user_sequence": sequence, "completed_post_responses": completed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--binary", type=Path, default=PROJECT / "_build/default/bin/bench.exe")
    parser.add_argument("--oha", default="pkgx oha")
    parser.add_argument("--domains", type=int, default=1)
    args = parser.parse_args()
    if args.rounds < 1 or not args.binary.is_file():
        parser.error("positive --rounds and an already built --binary are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {endpoint: [] for endpoint in workload.PAYLOADS}
    checks = []
    for index in range(args.rounds):
        directory = output / f"run-{index + 1}"
        directory.mkdir()
        database = directory / "bench.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
        socket_path = Path(f"/tmp/ocaml-bench-{os.getpid()}-{index}.sock")
        if os.path.lexists(socket_path):
            raise RuntimeError(f"socket path already exists: {socket_path}")
        command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(socket_path), "-domains", str(args.domains)]
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (directory / "server.log").open("w") as log:
            server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
            try:
                workload.wait_ready(server, socket_path)
                for endpoint in workload.PAYLOADS:
                    result = workload.measure(server, endpoint, socket_path, directory / endpoint, shlex.split(args.oha))
                    results[endpoint].append(result)
            finally:
                workload.stop(server)
        if server.returncode != 0:
            raise RuntimeError(f"server exited with {server.returncode}; see {directory}/server.log")
        check = verify(database, results["posts"][-1]["status_codes"]["201"])
        check["server_exit_code"] = server.returncode
        check["http_domain_requests"] = {int(domain): int(count) for domain, count in
            re.findall(r"HTTP domain (\d+): (\d+) requests", (directory / "server.log").read_text())}
        if len(check["http_domain_requests"]) != args.domains or any(count == 0 for count in check["http_domain_requests"].values()):
            raise RuntimeError("not all configured HTTP domains served requests")
        checks.append(check)
        (directory / "verification.json").write_text(json.dumps(check, indent=2) + "\n")
    summary = {
        "method": "Three fresh processes/databases by default; 50 connections, 10 seconds per endpoint; posts then echo; no HTTP warm-up; median RPS/p50, maximum RSS sampled every 100 ms",
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "http_domains": args.domains,
        "writer_domains": 1,
        "oha_version": subprocess.check_output(shlex.split(args.oha) + ["--version"], text=True).strip(),
        "verification": checks,
        "runs": {endpoint: [{k: v for k, v in run.items() if k != "rss_samples"} for run in runs]
                 for endpoint, runs in results.items()},
        "summary": {},
    }
    for endpoint, runs in results.items():
        summary["summary"][endpoint] = {
            "rps_median": statistics.median(r["requests_per_second"] for r in runs),
            "rps_min": min(r["requests_per_second"] for r in runs),
            "rps_max": max(r["requests_per_second"] for r in runs),
            "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in runs),
            "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in runs),
            "server_cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in runs),
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["summary"], indent=2))


if __name__ == "__main__":
    main()
