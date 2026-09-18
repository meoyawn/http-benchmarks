#!/usr/bin/env python3
"""Run the repository's HTTP workloads and sample server RSS every 100 ms."""

import argparse
import json
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
    parser.add_argument("--binary", type=Path, default=PROJECT / "bench")
    parser.add_argument("--socket", type=Path, default=Path("/tmp/benchmark.sock"))
    parser.add_argument("--loadgen", default=str(Path(__file__).resolve().parent.parent / "loadgen/bombard"), help="load-generator command")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "bench.sqlite"
    if database.exists() or args.socket.exists():
        parser.error("output database and socket must not already exist; choose a fresh output/socket")
    with sqlite3.connect(database) as db:
        db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
    command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(args.socket)]
    with (output / "server.log").open("w") as log:
        server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_ready(server, args.socket)
            results = {endpoint: measure(server, endpoint, args.socket, output / endpoint, shlex.split(args.loadgen))
                       for endpoint in PAYLOADS}
        finally:
            stop(server)
    if server.returncode:
        raise RuntimeError(f"server exited with {server.returncode}; see server.log")
    verification = verify(database, results["posts"]["status_codes"]["201"])
    verification["server_exit_code"] = server.returncode
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
