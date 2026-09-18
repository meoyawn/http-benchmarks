#!/usr/bin/env python3
"""Build first, then rotate local library or HTTP comparisons on shared data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
sys.path.insert(0, str(ROOT / "loadgen"))
import workload
import measure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("libs", "http"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", default="3s")
    parser.add_argument("--json", default="yyjson", choices=("std", "serde", "yyjson"))
    parser.add_argument("--sqlite", default="zqlite", choices=("zqlite", "ndsqlite"))
    parser.add_argument("--http", nargs="+", choices=("zap", "httpz", "dusty", "std"), default=("zap", "httpz", "dusty", "std"))
    parser.add_argument("--workers", nargs="+", type=int, default=(1, 2, 4, 8))
    parser.add_argument("--config", nargs="+", choices=[f"{h}-{w}" for h in ("zap", "httpz", "dusty", "std") for w in (1, 2, 3, 4, 8)])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    corpus = workload.prepare()
    if args.mode == "libs":
        candidates = {**{f"json-{j}": [f"-Djson={j}"] for j in ("std", "serde", "yyjson")},
                      **{f"sqlite-{s}": [f"-Dsqlite={s}"] for s in ("zqlite", "ndsqlite")}}
    else:
        candidates = {f"{http}-{workers}": [f"-Dhttp={http}", f"-Djson={args.json}", f"-Dsqlite={args.sqlite}"]
                      for http in args.http for workers in args.workers}
    if args.mode == "http" and args.config:
        candidates = {name: [f"-Dhttp={name.split('-')[0]}", f"-Djson={args.json}", f"-Dsqlite={args.sqlite}"] for name in args.config}
    report = {"rounds": args.rounds, "duration": args.duration, "corpus_sha256": measure.digest(corpus),
              "compiler": subprocess.check_output(["zig", "version"], text=True).strip(),
              "dependencies": (PROJECT / "build.zig.zon").read_text(), "excluded": {}, "runs": {}, "order": []}
    binaries = {}
    for name, flags in candidates.items():
        prefix = output / "build" / name
        prefix.mkdir(parents=True)
        commands = [["python3", "zig.py", "build", "test", "-Doptimize=ReleaseFast", *flags],
                    ["python3", "zig.py", "build", *(["bench"] if args.mode == "libs" else []), "-Doptimize=ReleaseFast", *flags, "--prefix", str(prefix)]]
        print("build/test", name, flush=True)
        with (prefix / "build.log").open("w") as log:
            success = all(subprocess.run(cmd, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT).returncode == 0 for cmd in commands)
        if not success:
            report["excluded"][name] = (prefix / "build.log").read_text()
            print("excluded:", name, flush=True)
            continue
        binaries[name] = prefix / "bin" / ("bench-libs" if args.mode == "libs" else "zig")
        report["runs"][name] = [] if args.mode == "libs" else {"posts": [], "echo": []}
    report["binary_sha256"] = {name: measure.digest(path) for name, path in binaries.items()}
    order = list(binaries)
    for index in range(args.rounds):
        rotated = order[index % len(order):] + order[:index % len(order)]
        report["order"].append(rotated)
        for name in rotated:
            directory = output / f"{name}-{index+1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            print(name, index + 1, flush=True)
            if args.mode == "libs":
                mode = name.split("-")[0]
                command = [str(binaries[name]), mode, str(corpus), str(database)]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                (directory / "stderr.txt").write_text(result.stderr)
                metrics = json.loads(result.stderr)
                if mode == "sqlite": workload.verify(database, metrics["operations"])
                report["runs"][name].append(metrics)
            else:
                sock = Path(f"/tmp/zig-compare-{os.getpid()}.sock")
                if os.path.lexists(sock): raise RuntimeError(f"socket already exists: {sock}")
                command = [str(binaries[name]), "-db", str(database), "-socket", str(sock), "-workers", name.split("-")[1]]
                with (directory / "server.log").open("w") as log:
                    server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    try:
                        workload.wait_ready(server, sock)
                        for endpoint in ("posts", "echo"):
                            metrics = workload.measure(server, endpoint, sock, directory / endpoint, duration=args.duration, seed=workload.SEED + index)
                            metrics.pop("rss_samples")
                            report["runs"][name][endpoint].append(metrics)
                    finally:
                        workload.stop(server)
                        sock.unlink(missing_ok=True)
                if server.returncode not in (0, -15, 143): raise RuntimeError((directory / "server.log").read_text())
                workload.verify(database, report["runs"][name]["posts"][-1]["requests"])
            (directory / "command.json").write_text(json.dumps(command, indent=2))
            report["summary"] = ({n: statistics.median(r["ns_per_op"] for r in samples) for n, samples in report["runs"].items() if samples}
                                 if args.mode == "libs" else measure.aggregate(report["runs"]))
            (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    report["complete"] = True
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__": main()
