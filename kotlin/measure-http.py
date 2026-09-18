#!/usr/bin/env python3
"""Run the repository's HTTP workloads and sample server RSS every 100 ms."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import sqlite3
import subprocess
import time


PROJECT = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(PROJECT.parent / "loadgen"))
from workload import (PAYLOADS, DEFAULT_COMMAND, wait_ready, stop, measure, verify, cpu_seconds, prepare)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--java", default=os.environ.get("JAVA_HOME", "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home") + "/bin/java")
    parser.add_argument("--jar", type=Path, default=PROJECT / "build/libs/kotlin-1.0-all.jar")
    parser.add_argument("--native", type=Path, help="measure this executable instead of the JVM")
    parser.add_argument("--jvm-arg", action="append", default=[])
    parser.add_argument("--socket", type=Path, default=Path("/tmp/kotlin-benchmark.sock"))
    parser.add_argument("--loadgen", default=str(Path(__file__).resolve().parent.parent / "loadgen/bombard"), help="load-generator command")
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--warmup", default="0s", help="optional warm-up per endpoint, saved separately")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--endpoints", nargs="+", choices=list(PAYLOADS), default=list(PAYLOADS))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "bench.sqlite"
    if database.exists() or args.socket.exists():
        parser.error("output database and socket must not already exist; choose a fresh output/socket")
    with sqlite3.connect(database) as db:
        db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
    command = [args.java, "-server", "-XX:+PerfDisableSharedMem", "--enable-native-access=ALL-UNNAMED",
               "-Ddb.path=" + str(database),
               "-Dhttp.socket=" + str(args.socket), *args.jvm_arg, "-jar", str(args.jar.resolve())]
    if args.native:
        command = [str(args.native.resolve()), "-Ddb.path=" + str(database),
                   "-Dhttp.socket=" + str(args.socket), *args.jvm_arg]
    artifact = (args.native or args.jar).resolve()
    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output / "artifact.json").write_text(json.dumps({"path": str(artifact), "bytes": artifact.stat().st_size,
                                                     "sha256": artifact_hash}, indent=2) + "\n")
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (output / "server.log").open("w") as log:
        server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
        (output / "server.pid").write_text(str(server.pid) + "\n")
        try:
            wait_ready(server, args.socket)
            results = []
            for endpoint in args.endpoints:
                if args.warmup != "0s":
                    results.append((endpoint, measure(server, endpoint, args.socket, output / (endpoint + "-warmup"), shlex.split(args.loadgen), args.warmup)))
                for i in range(args.runs):
                    label = endpoint if args.runs == 1 else f"{endpoint}-{i+1}"
                    results.append((endpoint, measure(server, endpoint, args.socket, output / label, shlex.split(args.loadgen), args.duration)))
        finally:
            stop(server)
    if server.returncode not in (0, 143):
        raise RuntimeError(f"server exited with {server.returncode}; see server.log")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != artifact_hash:
        raise RuntimeError("measured artifact changed during the benchmark")
    verification = verify(database, sum(r["status_codes"]["201"] for e, r in results if e == "posts"))
    verification["server_exit_code"] = server.returncode
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
