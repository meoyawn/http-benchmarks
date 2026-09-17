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
PAYLOADS = {
    "posts": '{ "content": "oha benchmark", "email": "oha@gmail.com" }',
    "echo": '{ "content": "oha benchmark", "email": "foo@gmail.com" }',
}


def wait_ready(process, socket_path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("server exited before becoming ready; see server log")
        with socket.socket(socket.AF_UNIX) as connection:
            try:
                connection.connect(str(socket_path))
                return  # No HTTP warm-up request.
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.02)
    raise TimeoutError("server did not become ready within 30 seconds")


def measure(process, endpoint, socket_path, output, oha, duration="10s"):
    command = oha + [
        f"http://localhost/{endpoint}", "--no-tui", "--unix-socket", str(socket_path),
        "-z", duration, "-m", "POST", "-T", "application/json", "-d", PAYLOADS[endpoint],
        "--output-format", "json",
    ]
    samples = []
    start = time.monotonic()
    with output.with_suffix(".json").open("w") as log, output.with_suffix(".stderr").open("w") as stderr:
        with subprocess.Popen(command, stdout=log, stderr=stderr) as load:
            try:
                while load.poll() is None:
                    if process.poll() is not None:
                        raise RuntimeError("server exited during benchmark")
                    rss = subprocess.check_output(["ps", "-o", "rss=", "-p", str(process.pid)], text=True)
                    samples.append({"seconds": time.monotonic() - start, "rss_kib": int(rss.strip())})
                    time.sleep(0.1)
            except BaseException:
                load.terminate()
                load.wait(timeout=10)
                raise
            if load.returncode:
                raise RuntimeError(f"oha exited with {load.returncode}; see {output}.stderr")
    result = json.loads(output.with_suffix(".json").read_text())
    expected = "201" if endpoint == "posts" else "200"
    if set(result["statusCodeDistribution"]) != {expected}:
        raise RuntimeError(f"unexpected responses: {result['statusCodeDistribution']}")
    # Timed oha runs cancel up to 50 in-flight requests at the deadline.
    errors = result.get("errorDistribution", {})
    if any("deadline" not in error.lower() for error in errors):
        raise RuntimeError(f"unexpected transport errors: {errors}")
    summary = {
        "command": command,
        "requests_per_second": result["summary"]["requestsPerSec"],
        "p50_seconds": result["latencyPercentiles"]["p50"],
        "status_codes": result["statusCodeDistribution"],
        "errors": errors,
        "peak_sampled_rss_mib": max(s["rss_kib"] for s in samples) / 1024,
        "rss_sampling_interval_seconds": 0.1,
        "rss_samples": samples,
    }
    output.with_suffix(".metrics.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{output.name}: {summary['requests_per_second']:.0f} RPS, "
          f"{summary['p50_seconds'] * 1000:.3f} ms p50, "
          f"{summary['peak_sampled_rss_mib']:.1f} MiB peak RSS", flush=True)
    return summary


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("server failed to stop gracefully")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--binary", type=Path, default=PROJECT / "bench")
    parser.add_argument("--socket", type=Path, default=Path("/tmp/benchmark.sock"))
    parser.add_argument("--oha", default="pkgx oha", help="load-generator command")
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
            results = {endpoint: measure(server, endpoint, args.socket, output / endpoint, shlex.split(args.oha))
                       for endpoint in PAYLOADS}
        finally:
            stop(server)
    if server.returncode:
        raise RuntimeError(f"server exited with {server.returncode}; see server.log")
    with sqlite3.connect(database) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
        users = db.execute("SELECT count(*) FROM users").fetchone()[0]
        invalid = db.execute("SELECT count(*) FROM posts WHERE content IS NOT 'oha benchmark' OR user_id IS NOT 1").fetchone()[0]
    completed = results["posts"]["status_codes"]["201"]
    assert integrity == [("ok",)] and not foreign_keys and not invalid and users == 1
    assert completed <= posts <= completed + 50, (completed, posts)
    verification = {"integrity": integrity, "foreign_keys": foreign_keys, "posts": posts, "users": users,
                    "completed_post_responses": completed, "server_exit_code": server.returncode}
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
