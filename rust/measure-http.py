#!/usr/bin/env python3
"""Measure Rust with the same sequential UDS load, RSS and CPU protocol as PR #4."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import sqlite3
import statistics
import subprocess
import time

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workload = load(ROOT / "go/measure-http.py", "workload")


def cpu_seconds(pid):
    raw = subprocess.check_output(["ps", "-o", "time=", "-p", str(pid)], text=True).strip()
    days, clock = raw.split("-", 1) if "-" in raw else ("0", raw)
    return int(days) * 86400 + sum(float(part) * 60 ** i for i, part in enumerate(reversed(clock.split(":"))))


def verify(database, completed):
    with sqlite3.connect(database) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
        users = db.execute("SELECT count(*) FROM users").fetchone()[0]
        invalid = db.execute("SELECT count(*) FROM posts WHERE content IS NOT 'oha benchmark' OR user_id IS NOT 1 OR created_at <= 0 OR updated_at IS NOT created_at").fetchone()[0]
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name IS 'users'").fetchone()[0]
        post_sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name IS 'posts'").fetchone()[0]
    if integrity != [("ok",)] or foreign_keys or invalid or users != 1:
        raise RuntimeError("database integrity/content check failed")
    if not completed <= posts <= completed + 50 or sequence != posts or post_sequence != posts:
        raise RuntimeError(f"commit/sequence mismatch: {completed=}, {posts=}, {sequence=}, {post_sequence=}")
    return {"integrity": integrity, "foreign_keys": foreign_keys, "posts": posts, "users": users,
            "user_sequence": sequence, "post_sequence": post_sequence, "completed_post_responses": completed}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--binary", type=Path, nargs="+", default=[PROJECT / "target/release/rust-benchmark"])
    parser.add_argument("--workers", type=int, nargs="+", default=[1])
    parser.add_argument("--go-binary", type=Path, help="include a freshly built Go control in the rotating runs")
    parser.add_argument("--gomaxprocs", type=int, nargs="+", default=[2])
    parser.add_argument("--variant", nargs=3, action="append", metavar=("NAME", "BINARY", "WORKERS"),
                        help="compare named variants with independent worker counts; repeat as needed")
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--oha", default="pkgx oha")
    args = parser.parse_args()
    try:
        variants = ({name: (Path(binary).resolve(), int(workers)) for name, binary, workers in args.variant}
                    if args.variant else
                    {f"{binary.name}-{workers}": (binary.resolve(), workers)
                     for binary in args.binary for workers in args.workers})
    except ValueError:
        parser.error("worker counts must be integers")
    if args.variant and len(variants) != len(args.variant):
        parser.error("variant names must be unique")
    go_names = set()
    if args.go_binary:
        for processors in args.gomaxprocs:
            name = f"go-{processors}"
            if name in variants:
                parser.error(f"duplicate variant: {name}")
            variants[name] = (args.go_binary.resolve(), processors)
            go_names.add(name)
    if args.rounds < 1 or any(workers < 1 or not binary.is_file() for binary, workers in variants.values()):
        parser.error("positive rounds/workers and already built binaries required")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in (".", "..") for name in variants):
        parser.error("variant names must be single safe directory names")
    hashes = {name: digest(binary) for name, (binary, _) in variants.items()}
    sqlite_library = PROJECT / ".tools/sqlite/lib" / ("libsqlite3.dylib" if platform.system() == "Darwin" else "libsqlite3.so")
    library_hash = digest(sqlite_library)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {name: {endpoint: [] for endpoint in workload.PAYLOADS} for name in variants}
    checks = {name: [] for name in variants}
    names = list(variants)
    for index in range(args.rounds):
        shift = index % len(names)
        for name in names[shift:] + names[:shift]:
            binary, workers = variants[name]
            directory = output / f"{name}-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            socket = Path(f"/tmp/rust-bench-{os.getpid()}.sock")
            if os.path.lexists(socket):
                raise RuntimeError(f"socket already exists: {socket}")
            command = [str(binary), "-db", str(database), "-socket", str(socket)]
            env = dict(os.environ)
            if name in go_names:
                env["GOMAXPROCS"] = str(workers)
            else:
                command.extend(["-workers", str(workers)])
            (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
            (directory / "configuration.json").write_text(json.dumps(
                {"GOMAXPROCS": workers} if name in go_names else {"http_workers": workers}, indent=2) + "\n")
            print(f"{name} round {index + 1}", flush=True)
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    workload.wait_ready(server, socket)
                    for endpoint in workload.PAYLOADS:
                        start_cpu = cpu_seconds(server.pid)
                        start = time.monotonic()
                        result = workload.measure(server, endpoint, socket, directory / endpoint, shlex.split(args.oha), args.duration)
                        wall = time.monotonic() - start
                        used = cpu_seconds(server.pid) - start_cpu
                        result.update(server_cpu_seconds=used, cpu_observation_wall_seconds=wall,
                                      server_cpu_percent=used / wall * 100)
                        (directory / f"{endpoint}.metrics.json").write_text(json.dumps(result, indent=2) + "\n")
                        result.pop("rss_samples")
                        results[name][endpoint].append(result)
                finally:
                    workload.stop(server)
            if server.returncode != 0:
                raise RuntimeError(f"{name} did not stop cleanly: {server.returncode}")
            check = verify(database, results[name]["posts"][-1]["status_codes"]["201"])
            checks[name].append(check)
            (directory / "verification.json").write_text(json.dumps(check, indent=2) + "\n")
    if hashes != {name: digest(binary) for name, (binary, _) in variants.items()} or library_hash != digest(sqlite_library):
        raise RuntimeError("a measured artifact changed during the benchmark")
    summary = {name: {endpoint: {
        "rps_median": statistics.median(r["requests_per_second"] for r in runs),
        "rps_min": min(r["requests_per_second"] for r in runs),
        "rps_max": max(r["requests_per_second"] for r in runs),
        "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in runs),
        "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in runs),
        "server_cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in runs),
    } for endpoint, runs in endpoints.items()} for name, endpoints in results.items()}
    record = {"method": "Rotating-order rounds, fresh processes/databases, 50 connections, posts then echo, no HTTP warm-up; medians except maximum sampled RSS; 100% CPU = one core",
              "platform": platform.platform(), "rounds": args.rounds, "duration": args.duration,
              "configurations": {name: ({"GOMAXPROCS": workers} if name in go_names else {"http_workers": workers})
                                 for name, (_, workers) in variants.items()},
              "artifacts_sha256": hashes, "sqlite_library_sha256": library_hash,
              "executable_bytes": {name: binary.stat().st_size for name, (binary, _) in variants.items()},
              "sqlite_library_bytes": sqlite_library.stat().st_size,
              "sqlite_configuration": json.loads((ROOT / "db/sqlite-config.json").read_text()),
              "sqlite_build": json.loads(sqlite_library.with_suffix(sqlite_library.suffix + ".build.json").read_text()),
              "sqlite_reported": json.loads(subprocess.check_output([str(next(iter(variants.values()))[0]), "-check-config"], text=True)),
              "rustc": subprocess.check_output(["rustc", "--version"], cwd=PROJECT, text=True).strip(),
              "cargo_configuration": (PROJECT / ".cargo/config.toml").read_text(),
              "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in sorted(list((PROJECT / "src").rglob("*.rs")) + [PROJECT / "Cargo.toml", PROJECT / "Cargo.lock", PROJECT / "build.rs", PROJECT / ".cargo/config.toml"])},
              "oha_version": subprocess.check_output(shlex.split(args.oha) + ["--version"], text=True).strip(),
              "runs": results, "verification": checks, "summary": summary}
    if args.go_binary:
        record["go_build_info"] = subprocess.check_output(
            ["go", "version", "-m", str(args.go_binary.resolve())], text=True)
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
