#!/usr/bin/env python3
"""Exercise the real UDS server, including concurrent commits and malformed requests."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def request(socket_path, endpoint, body, method="POST", chunked=False):
    client = http.client.HTTPConnection("localhost", timeout=10)
    client.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.sock.settimeout(10)
    try:
        client.sock.connect(str(socket_path))
        client.request(method, endpoint, iter([body[:10], body[10:]]) if chunked else body,
                       {"Content-Type": "application/json"}, encode_chunked=chunked)
        response = client.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=PROJECT / "zig-out/bin/zig")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="zig-http-test-") as temporary:
        directory = Path(temporary)
        database = directory / "test.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
        socket_path = Path(f"/tmp/zig-test-{os.getpid()}.sock")
        if os.path.lexists(socket_path):
            raise RuntimeError(f"socket exists: {socket_path}")
        command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(socket_path), "-workers", str(args.workers)]
        with (directory / "server.log").open("w") as log:
            server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while not socket_path.exists():
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError((directory / "server.log").read_text())
                    time.sleep(0.01)
                post = {"content": "quotes \" slash \\ newline\n雪\u0000", "email": "foo@gmail.com"}
                body = json.dumps(post).encode()
                for chunked in (False, True):
                    status, data, content_type = request(socket_path, "/echo", body, chunked=chunked)
                    assert status == 200 and json.loads(data) == post and content_type.startswith("application/json")
                assert request(socket_path, "/echo?ignored=1", body)[0] == 200
                assert request(socket_path, "/posts", b"x" * (2 * 1024 * 1024 + 1))[0] == 413
                assert request(socket_path, "/missing", body)[0] == 404
                assert request(socket_path, "/posts", body, method="GET")[0] in (404, 405)
                for invalid in [b"", b"{", b"null", b"[]", b'{"content":1,"email":"a@b.com"}', b'{"content":"x"}', body + b" trailing", b'{"content":"\xff","email":"a@b.com"}']:
                    for endpoint in ("/posts", "/echo"):
                        assert request(socket_path, endpoint, invalid)[0] == 400, (endpoint, invalid)
                assert request(socket_path, "/posts", json.dumps(dict(post, content="")).encode())[0] == 400
                committed = 0
                for fixture in json.loads((ROOT / "testdata/email-validation.json").read_text()):
                    status, _, _ = request(socket_path, "/posts", json.dumps(dict(post, email=fixture["email"])).encode())
                    assert status == (201 if fixture["valid"] else 400), fixture
                    committed += int(fixture["valid"])

                large = {"content": "multi-buffer 雪\u0000 " * 4096, "email": "large@example.com"}
                for chunked in (False, True):
                    status, data, _ = request(socket_path, "/posts", json.dumps(large).encode(), chunked=chunked)
                    assert status == 201 and json.loads(data)["content"] == large["content"]
                    committed += 1

                def write(index):
                    payload = {"content": f"parallel {index} 雪", "email": "parallel@example.com"}
                    status, data, content_type = request(socket_path, "/posts", json.dumps(payload).encode())
                    assert status == 201 and content_type.startswith("application/json")
                    result = json.loads(data)
                    assert set(result) == {"id", "user_id", "content", "created_at", "updated_at"}
                    assert result["content"] == payload["content"]
                    assert result["created_at"] == result["updated_at"] and result["created_at"] > 1_700_000_000_000
                    # An independent connection must observe the transaction before we
                    # count the HTTP response as successful.
                    with sqlite3.connect(database) as observer:
                        row = observer.execute("SELECT content, user_id, created_at, updated_at FROM posts WHERE id IS ?", [result["id"]]).fetchone()
                    assert row == (result["content"], result["user_id"], result["created_at"], result["updated_at"])
                    return result["id"]

                with ThreadPoolExecutor(max_workers=50) as pool:
                    ids = list(pool.map(write, range(200)))
                assert len(set(ids)) == 200
                committed += len(ids)

                # The HTTP worker must remain responsive while SQLite waits for a
                # write lock, then return the committed row after it is released.
                with sqlite3.connect(database) as blocker, ThreadPoolExecutor(max_workers=50) as pool:
                    blocker.execute("BEGIN IMMEDIATE")
                    pending = [pool.submit(write, index) for index in range(200, 250)]
                    try:
                        time.sleep(0.1)
                        assert all(not future.done() for future in pending), "write bypassed SQLite's transaction lock"
                        start = time.monotonic()
                        assert request(socket_path, "/echo", body)[0] == 200
                        assert time.monotonic() - start < 1, "waiting writes occupied the HTTP executors"
                    finally:
                        blocker.rollback()
                    assert all(future.result() not in ids for future in pending)
                    committed += len(pending)
                with sqlite3.connect(database) as db:
                    db.execute("CREATE TRIGGER fail_post BEFORE INSERT ON posts WHEN NEW.content IS 'forced failure' BEGIN SELECT RAISE(ABORT, 'test'); END")
                failed = {"content": "forced failure", "email": "rolledback@example.com"}
                assert request(socket_path, "/posts", json.dumps(failed).encode())[0] == 500
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT count(*) FROM users WHERE email IS ?", [failed["email"]]).fetchone()[0] == 0
                    db.execute("DROP TRIGGER fail_post")
                write(250)
                committed += 1
                # Refusing a second bind must leave the first server reachable.
                collision = subprocess.run(command, cwd=PROJECT, capture_output=True, timeout=10)
                assert collision.returncode != 0
                assert request(socket_path, "/echo", body)[0] == 200
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == committed
                    assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
                print(f"HTTP checks passed: {committed} verified commits, 50 concurrent clients, shared validation, malformed JSON, chunked echo, all RETURNING columns, 50 suspended writes with responsive echo, rollback recovery, occupied socket", flush=True)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                    raise RuntimeError("server did not stop gracefully")
                if server.returncode != 0:
                    raise RuntimeError((directory / "server.log").read_text())
                assert not socket_path.exists(), "server left its socket behind"


if __name__ == "__main__":
    main()
