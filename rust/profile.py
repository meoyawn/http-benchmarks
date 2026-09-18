#!/usr/bin/env python3
"""Profile an optimized Rust server under the real SQLite write workload on macOS."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shlex
import sqlite3
import subprocess

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
spec = importlib.util.spec_from_file_location("benchmark", PROJECT / "measure-http.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--binary", type=Path, default=PROJECT / "target/release/rust-benchmark")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--profiler", choices=("sample", "instruments"), default="sample")
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--oha", default="pkgx oha")
    args = parser.parse_args()
    if platform.system() != "Darwin" or args.workers < 1 or args.seconds < 1 or not args.binary.is_file():
        parser.error("requires macOS, a built binary and positive workers/seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    database = output / "bench.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
    socket = Path(f"/tmp/rust-profile-{os.getpid()}.sock")
    if os.path.lexists(socket):
        raise RuntimeError(f"socket exists: {socket}")
    command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(socket), "-workers", str(args.workers)]
    env = dict(os.environ)
    if args.profiler == "instruments" and "DEVELOPER_DIR" not in env:
        xcode = Path("/Applications/Xcode.app/Contents/Developer")
        if xcode.is_dir():
            env["DEVELOPER_DIR"] = str(xcode)
    with (output / "server.log").open("w") as log, (output / "profiler.log").open("w") as profiler_log:
        server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
        profiler = None
        try:
            benchmark.workload.wait_ready(server, socket)
            if args.profiler == "sample":
                profile_command = ["sample", str(server.pid), str(args.seconds), "1", "-file", str(output / "stacks.txt")]
            else:
                profile_command = ["xctrace", "record", "--template", "Time Profiler", "--attach", str(server.pid),
                                   "--time-limit", f"{args.seconds}s", "--output", str(output / "cpu.trace")]
            record = {"server_command": command, "profiler_command": profile_command,
                      "binary_sha256": benchmark.digest(args.binary),
                      "purpose": "Diagnostic only: profiling adds overhead; exclude throughput from ranking."}
            (output / "commands.json").write_text(json.dumps(record, indent=2) + "\n")
            profiler = subprocess.Popen(profile_command, env=env, stdout=profiler_log, stderr=subprocess.STDOUT)
            # Allow recorder startup time while continuing to exercise the server.
            result = benchmark.workload.measure(server, "posts", socket, output / "posts",
                                                shlex.split(args.oha), f"{args.seconds + 10}s")
            if profiler.wait(timeout=30):
                raise RuntimeError(f"profiler failed; see {output / 'profiler.log'}")
        finally:
            if profiler is not None and profiler.poll() is None:
                profiler.terminate()
                try:
                    profiler.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    profiler.kill()
                    profiler.wait()
            benchmark.workload.stop(server)
    if server.returncode:
        raise RuntimeError(f"server failed; see {output / 'server.log'}")
    verification = benchmark.verify(database, result["status_codes"]["201"])
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(f"Profile and verified database saved in {output}")


if __name__ == "__main__":
    main()
