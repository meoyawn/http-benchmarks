"""Shared randomized valid JSON workload, process sampling and exact DB checks."""
import functools
import hashlib
import json
import os
from pathlib import Path
import random
import re
import socket
import sqlite3
import string
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "loadgen/bombard"
DEFAULT_COMMAND = str(BINARY)
PAYLOADS = ("posts", "echo")
SEED = 20260918
CORPUS_SIZE = 65536
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.ASCII)


@functools.lru_cache(maxsize=1)
def corpus():
    rng = random.Random(SEED)
    alphabet = string.ascii_letters + string.digits + ' .,!?:;"\\\n\t' + "éλ雪🙂"
    rows = []
    for index in range(CORPUS_SIZE):
        email = f"{index:04x}.{rng.getrandbits(48):012x}@{rng.getrandbits(32):08x}.example.test"
        content = "".join(rng.choices(alphabet, k=rng.randint(32, 256)))
        if not EMAIL.fullmatch(email) or not content:
            raise RuntimeError("invalid generated corpus entry")
        rows.append({"email": email, "content": content})
    return rows


@functools.lru_cache(maxsize=1)
def prepare():
    if not BINARY.is_file():
        raise RuntimeError("Build the load generator first: cd loadgen && go build -trimpath -o bombard .")
    path = ROOT / ".tools/random-json-v1.jsonl"
    data = b"".join((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in corpus())
    if not path.is_file() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return path


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
                return
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.02)
    raise TimeoutError("server did not become ready within 30 seconds")


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("server failed to stop gracefully")


def measure(process, endpoint, socket_path, output, loadgen=None, duration="10s", *, cpus=5, shards=1, processes=5, seed=SEED):
    if endpoint not in PAYLOADS:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    path = prepare()
    expected = "201" if endpoint == "posts" else "200"
    command = (loadgen or [str(BINARY)]) + [
        "-url", f"http://localhost/{endpoint}", "-unix-socket", str(socket_path),
        "-duration", duration, "-status", expected, "-corpus", str(path),
        "-cpus", str(cpus), "-shards", str(shards), "-processes", str(processes), "-seed", str(seed),
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
                    # One sampler subprocess observes server and client together.
                    raw = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
                    observations = [(int(pid), int(ppid), int(size)) for pid, ppid, size in (line.split() for line in raw.splitlines())]
                    rss = {pid: size for pid, _, size in observations}
                    samples.append({"seconds": time.monotonic() - start, "rss_kib": rss[process.pid],
                                    "client_rss_kib": sum(size for pid, ppid, size in observations if pid == load.pid or ppid == load.pid)})
                    time.sleep(0.1)
            except BaseException:
                stop(load)
                raise
            if load.returncode:
                raise RuntimeError(f"load generator exited with {load.returncode}; see {output}.stderr and .json")
    wall = time.monotonic() - start
    used_cpu = cpu_seconds(process.pid) - start_cpu
    result = json.loads(output.with_suffix(".json").read_text())
    if result["requests"] <= 0 or result["status_codes"] != {expected: result["requests"]} or result["errors"]:
        raise RuntimeError(f"unexpected responses: {result['status_codes']}, errors: {result['errors']}")
    result.pop("histogram")  # Retained in the raw JSON; do not duplicate in aggregates.
    result.update(command=command, peak_sampled_rss_mib=max(s["rss_kib"] for s in samples) / 1024,
                  client_peak_sampled_rss_mib=max(s["client_rss_kib"] for s in samples) / 1024,
                  rss_sampling_interval_seconds=0.1, rss_samples=samples,
                  server_cpu_seconds=used_cpu, cpu_observation_wall_seconds=wall,
                  server_cpu_percent=used_cpu / result["wall_seconds"] * 100,
                  corpus_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    output.with_suffix(".metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{output.name}: {result['requests_per_second']:.0f} RPS, "
          f"{result['p50_seconds'] * 1000:.3f} ms p50, "
          f"server/client CPU {result['server_cpu_percent']:.0f}/{result['client_cpu_percent']:.0f}%", flush=True)
    return result


def verify(database, completed):
    with sqlite3.connect(database) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
        users = db.execute("SELECT count(*) FROM users").fetchone()[0]
        sequences = dict(db.execute("SELECT name, seq FROM sqlite_sequence"))
        db.execute("CREATE TEMP TABLE expected(email TEXT PRIMARY KEY COLLATE NOCASE, content TEXT NOT NULL)")
        db.executemany("INSERT INTO expected VALUES (?, ?)", ((r["email"], r["content"]) for r in corpus()))
        invalid_users = db.execute("SELECT count(*) FROM users u LEFT JOIN expected e ON u.email=e.email WHERE e.email IS NULL OR u.created_at <= 0 OR u.updated_at IS NOT u.created_at").fetchone()[0]
        invalid = db.execute("SELECT count(*) FROM posts p LEFT JOIN users u ON p.user_id=u.id LEFT JOIN expected e ON u.email=e.email WHERE e.content IS NULL OR p.content IS NOT e.content OR p.created_at <= 0 OR p.updated_at IS NOT p.created_at").fetchone()[0]
    if integrity != [("ok",)] or foreign_keys or invalid or invalid_users:
        raise RuntimeError(f"database verification failed: {integrity=}, {foreign_keys=}, {invalid=}, {invalid_users=}")
    if posts != completed or sequences.get("users", 0) != posts or sequences.get("posts", 0) != posts:
        raise RuntimeError(f"exact drained commit/sequence mismatch: {completed=}, {posts=}, {sequences=}")
    if not 0 <= users <= min(CORPUS_SIZE, posts) or (posts and not users):
        raise RuntimeError(f"unexpected user count: {users=}, {posts=}")
    return {"integrity": integrity, "foreign_keys": foreign_keys, "posts": posts, "users": users,
            "user_sequence": sequences.get("users", 0), "post_sequence": sequences.get("posts", 0),
            "completed_post_responses": completed, "invalid_content_or_users": invalid + invalid_users}
