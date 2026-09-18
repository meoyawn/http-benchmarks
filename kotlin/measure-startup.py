#!/usr/bin/env python3
"""Measure JVM/native launch, listening, first echo and first committed post."""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import selectors
import socket
import sqlite3
import statistics
import subprocess
import time

PROJECT = Path(__file__).resolve().parent


def request(uds, endpoint):
    payload = {"email": "startup@example.com", "content": "startup check café 🐈"}
    client = http.client.HTTPConnection("localhost", timeout=10)
    client.sock = socket.socket(socket.AF_UNIX)
    client.sock.settimeout(10)
    try:
        client.sock.connect(str(uds))
        client.request("POST", endpoint, json.dumps(payload), {"Content-Type": "application/json"})
        response = client.getresponse()
        body = json.loads(response.read())
        assert response.status == (200 if endpoint == "/echo" else 201), (response.status, body)
        if endpoint == "/echo":
            assert body == payload
        else:
            assert body["content"] == payload["content"] and body["user_id"] == 1
        return body
    finally:
        client.close()


def measure(command, uds, database, directory):
    (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (directory / "server.log").open("wb") as log, selectors.DefaultSelector() as selector:
        start = time.perf_counter_ns()
        process = subprocess.Popen(command, cwd=PROJECT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=0)
        elapsed = lambda: (time.perf_counter_ns() - start) / 1_000_000
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = b""
            deadline = time.monotonic() + 30
            marker = f"Listening on {uds}".encode()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError(f"no listening log; see {directory}/server.log")
                chunk = os.read(process.stdout.fileno(), 65536)
                received = elapsed()
                if not chunk:
                    raise RuntimeError(f"server exited before listening; see {directory}/server.log")
                log.write(chunk)
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()
                if any(marker in line for line in lines):
                    listening = received
                    break
            request(uds, "/echo")
            echo = elapsed()
            post = request(uds, "/posts")
            committed_post = elapsed()
            # Check visibility while the server is still running, after its response.
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT content FROM posts WHERE id=?", (post["id"],)).fetchone() == (post["content"],)
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
        assert process.returncode in (0, 143), process.returncode
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    result = {"listening_ms": listening, "first_echo_ms": echo, "first_post_ms": committed_post,
              "verification": "echo, committed post, integrity, foreign keys, graceful shutdown"}
    (directory / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--java", default=str(Path(os.environ["JAVA_HOME"]) / "bin/java") if "JAVA_HOME" in os.environ else "java")
    parser.add_argument("--jar", type=Path, default=PROJECT / "build/libs/kotlin-1.0-all.jar")
    parser.add_argument("--native", type=Path, help="measure a GraalVM executable instead of the JVM")
    parser.add_argument("--native-dir", type=Path)
    parser.add_argument("--cache", type=Path, default=PROJECT / "build/jit/kotlin-bench.aot")
    parser.add_argument("--modes", nargs="+", choices=["bundled", "libraries", "cached"])
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--profile", action="store_true", help="record JFR and class initialization; timings include profiler overhead")
    args = parser.parse_args()
    args.modes = args.modes or (["libraries"] if args.native else ["cached"])
    args.native_dir = args.native_dir or (args.native.resolve().parent / "native" if args.native else PROJECT / "build/jit/native")
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.native and (args.profile or "cached" in args.modes):
        parser.error("--profile and cached mode require the JVM")
    artifacts = [(args.native or args.jar).resolve()]
    if args.native:
        artifacts += [*args.native.resolve().parent.glob("*.dylib"), *args.native.resolve().parent.glob("*.so")]
    if any(mode != "bundled" for mode in args.modes):
        artifacts += list(args.native_dir.resolve().iterdir())
    if "cached" in args.modes:
        artifacts.append(args.cache.resolve())
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    hashes = {str(p): {"sha256": digest(p), "bytes": p.stat().st_size} for p in artifacts}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runs = {mode: [] for mode in args.modes}
    for index in range(args.rounds):
        for mode in (args.modes if index % 2 == 0 else list(reversed(args.modes))):
            directory = output / f"{mode}-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
            uds = Path(f"/tmp/kotlin-start-{os.getpid()}-{index}-{mode}.sock")
            if os.path.lexists(uds):
                raise RuntimeError(f"socket already exists: {uds}")
            command = ([str(args.native.resolve())] if args.native else
                       [args.java, "-server", "-XX:+PerfDisableSharedMem", "--enable-native-access=ALL-UNNAMED"])
            command += [f"-Ddb.path={database}", f"-Dhttp.socket={uds}"]
            if mode != "bundled":
                name = "libsqlite3.dylib" if platform.system() == "Darwin" else "libsqlite3.so"
                command += [f"-Djava.library.path={args.native_dir.resolve()}",
                            f"-Dsqlite.library={args.native_dir.resolve() / name}"]
            if mode == "cached":
                command += ["-XX:AOTMode=on", f"-XX:AOTCache={args.cache.resolve()}"]
            if args.profile:
                command += [f"-XX:StartFlightRecording=filename={directory}/startup.jfr,settings=profile,dumponexit=true",
                            f"-Xlog:class+init=info:file={directory}/classes.log:uptime"]
            if not args.native:
                command += ["-jar", str(args.jar.resolve())]
            result = measure(command, uds, database, directory)
            runs[mode].append(result)
            print(f"{mode} {index + 1}: listen {result['listening_ms']:.2f} ms, "
                  f"echo {result['first_echo_ms']:.2f} ms, post {result['first_post_ms']:.2f} ms", flush=True)
    assert all(digest(Path(p)) == record["sha256"] for p, record in hashes.items()), "artifacts changed during measurement"
    summary = {mode: {key: {"median": statistics.median(r[key] for r in values),
                           "min": min(r[key] for r in values), "max": max(r[key] for r in values)}
                      for key in ("listening_ms", "first_echo_ms", "first_post_ms")}
               for mode, values in runs.items()}
    report = {"method": "Fresh processes and migrated databases; alternating mode order; all times from Popen. "
                        "Echo then post after post-bind listening log; no HTTP warm-up or cache flushing. "
                        "Cache training/native staging are build steps excluded from startup.",
              "profiled": args.profile, "platform": platform.platform(), "artifacts": hashes,
              "java_version": None if args.native else subprocess.check_output([args.java, "-version"], stderr=subprocess.STDOUT, text=True).strip(),
              "runs": runs, "summary": summary}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
