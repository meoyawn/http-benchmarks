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
PAYLOADS = {
    "posts": '{ "content": "oha benchmark", "email": "oha@gmail.com" }',
    "echo": '{ "content": "oha benchmark", "email": "foo@gmail.com" }',
}


def cpu_seconds(pid):
    raw = subprocess.check_output(["ps", "-o", "time=", "-p", str(pid)], text=True).strip()
    days, clock = raw.split("-", 1) if "-" in raw else ("0", raw)
    return int(days) * 86400 + sum(float(part) * 60 ** i for i, part in enumerate(reversed(clock.split(":"))))


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
    start_cpu = cpu_seconds(process.pid)
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
    wall = time.monotonic() - start
    used_cpu = cpu_seconds(process.pid) - start_cpu
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
        "server_cpu_seconds": used_cpu,
        "cpu_observation_wall_seconds": wall,
        "server_cpu_percent": used_cpu / wall * 100,
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
    parser.add_argument("--java", default=os.environ.get("JAVA_HOME", "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home") + "/bin/java")
    parser.add_argument("--jar", type=Path, default=PROJECT / "build/libs/kotlin-1.0-all.jar")
    parser.add_argument("--native", type=Path, help="measure this executable instead of the JVM")
    parser.add_argument("--jvm-arg", action="append", default=[])
    parser.add_argument("--socket", type=Path, default=Path("/tmp/kotlin-benchmark.sock"))
    parser.add_argument("--oha", default="pkgx oha", help="load-generator command")
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
                    results.append((endpoint, measure(server, endpoint, args.socket, output / (endpoint + "-warmup"), shlex.split(args.oha), args.warmup)))
                for i in range(args.runs):
                    label = endpoint if args.runs == 1 else f"{endpoint}-{i+1}"
                    results.append((endpoint, measure(server, endpoint, args.socket, output / label, shlex.split(args.oha), args.duration)))
        finally:
            stop(server)
    if server.returncode not in (0, 143):
        raise RuntimeError(f"server exited with {server.returncode}; see server.log")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != artifact_hash:
        raise RuntimeError("measured artifact changed during the benchmark")
    with sqlite3.connect(database) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
        users = db.execute("SELECT count(*) FROM users").fetchone()[0]
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name IS 'users'").fetchone()
        post_sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name IS 'posts'").fetchone()
        invalid = db.execute("SELECT count(*) FROM posts WHERE content IS NOT 'oha benchmark' OR user_id IS NOT 1 OR created_at <= 0 OR updated_at IS NOT created_at").fetchone()[0]
    completed = sum(r["status_codes"]["201"] for e, r in results if e == "posts")
    post_runs = sum(e == "posts" for e, r in results)
    assert integrity == [("ok",)] and not foreign_keys and not invalid and users == (1 if completed else 0)
    assert completed <= posts <= completed + 50 * post_runs, (completed, posts)
    assert (sequence[0] if sequence else 0) == posts, "user ID allocation differs from INSERT OR IGNORE"
    assert (post_sequence[0] if post_sequence else 0) == posts, "post ID allocation differs from committed rows"
    verification = {"integrity": integrity, "foreign_keys": foreign_keys, "posts": posts, "users": users,
                    "user_sequence": sequence, "post_sequence": post_sequence,
                    "completed_post_responses": completed, "server_exit_code": server.returncode}
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
