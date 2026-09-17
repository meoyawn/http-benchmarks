#!/usr/bin/env python3
"""Measure built Go/Kotlin processes from launch to their first listening log line."""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import selectors
import socket
import sqlite3
import statistics
import subprocess
import time


ROOT = Path(__file__).resolve().parent


def verify_echo(socket_path):
    client = http.client.HTTPConnection("localhost", timeout=5)
    client.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.sock.settimeout(5)
    try:
        client.sock.connect(str(socket_path))
        payload = {"content": "startup check", "email": "foo@gmail.com"}
        client.request("POST", "/echo", json.dumps(payload), {"Content-Type": "application/json"})
        response = client.getresponse()
        if response.status != 200 or json.loads(response.read()) != payload:
            raise RuntimeError("listening server failed the echo check")
    finally:
        client.close()


def measure(command, marker, socket_path, directory, cwd):
    (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (directory / "server.log").open("wb") as log, selectors.DefaultSelector() as selector:
        start = time.perf_counter_ns()
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = b""
            deadline = time.monotonic() + 30
            elapsed = None
            ready_line = None
            while elapsed is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("no listening log within 30 seconds; see server.log")
                chunk = os.read(process.stdout.fileno(), 65536)
                received = time.perf_counter_ns()
                if not chunk:
                    raise RuntimeError("server exited before logging that it is listening; see server.log")
                log.write(chunk)
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()
                for line in lines:
                    if marker in line:
                        elapsed = (received - start) / 1_000_000
                        ready_line = line.decode(errors="replace")
                        break
            # Outside the timed interval: confirm this log represents a usable listener.
            verify_echo(socket_path)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                remaining, _ = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                remaining, _ = process.communicate()
                log.write(remaining)
                raise RuntimeError("server did not stop gracefully")
            log.write(remaining)
        if process.returncode not in (0, 143):
            raise RuntimeError(f"server exited with {process.returncode}; see server.log")
    result = {"launch_to_listening_ms": elapsed, "ready_line": ready_line, "echo_check": "passed"}
    (directory / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--java", default=str(Path(os.environ["JAVA_HOME"]) / "bin/java") if "JAVA_HOME" in os.environ else "java")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    artifacts = {
        "go": ROOT / "go/bench",
        "kotlin": ROOT / "kotlin-vertx-panama/build/libs/kotlin-vertx-panama-1.0-all.jar",
    }
    for artifact in artifacts.values():
        if not artifact.is_file():
            parser.error(f"build the application first: {artifact}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {language: [] for language in artifacts}
    for index in range(args.rounds):
        for language in (("go", "kotlin") if index % 2 == 0 else ("kotlin", "go")):
            directory = output / f"{language}-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            socket_path = Path(f"/tmp/http-startup-{os.getpid()}-{language}-{index}.sock")
            if os.path.lexists(socket_path):
                raise RuntimeError(f"socket path already exists: {socket_path}")
            if language == "go":
                command = [str(artifacts[language]), "-db", str(database), "-socket", str(socket_path)]
                # The application's earlier 'Listening on' line precedes bind.
                # Hertz emits this transport log after creating the listener.
                marker = f"HTTP server listening on address={socket_path}".encode()
                cwd = ROOT / "go"
            else:
                command = [args.java, "-server", "-XX:+PerfDisableSharedMem", "--enable-native-access=ALL-UNNAMED",
                           f"-Ddb.path={database}", f"-Dhttp.socket={socket_path}", "-jar", str(artifacts[language])]
                marker = f"Listening on {socket_path}".encode()
                cwd = ROOT / "kotlin-vertx-panama"
            result = measure(command, marker, socket_path, directory, cwd)
            results[language].append(result)
            print(f"{language} {index + 1}: {result['launch_to_listening_ms']:.3f} ms", flush=True)
    summary = {
        "method": "Wall time immediately before Popen to receipt of the first post-bind listening log; five alternating fresh processes by default; migration/builds excluded; echo check after timing; no cache flushing or HTTP warm-up",
        "runs": results,
        "artifacts_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in artifacts.items()},
        "summary": {},
    }
    for language, runs in results.items():
        values = [run["launch_to_listening_ms"] for run in runs]
        summary["summary"][language] = {"median_ms": statistics.median(values), "min_ms": min(values), "max_ms": max(values)}
        print(f"{language}: median {statistics.median(values):.3f} ms", flush=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
