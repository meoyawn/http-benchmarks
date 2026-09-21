#!/usr/bin/env python3
"""Validate native Bun HTTP, Valibot and committed SQLite transactions over UDS."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def request(sock, endpoint, body, method="POST", chunked=False, content_length=None):
    client = http.client.HTTPConnection("localhost", timeout=10)
    client.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.sock.settimeout(10)
    try:
        client.sock.connect(str(sock))
        headers = {"Content-Type": "application/json"}
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        client.request(method, endpoint, iter([body[:10], body[10:]]) if chunked else body,
                       headers, encode_chunked=chunked)
        response = client.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=PROJECT / "dist/server")
    args = parser.parse_args()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        with tempfile.TemporaryDirectory(prefix="bun-http-test-") as temporary:
            directory = Path(temporary)
            database = directory / "test.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            sock = Path(f"/tmp/bun-test-{os.getpid()}.sock")
            assert not os.path.lexists(sock)
            command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(sock)]
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 15
                    while not sock.exists():
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError((directory / "server.log").read_text())
                        time.sleep(0.01)
                    post = {"content": "quotes \" slash \\ newline\n雪\u0000", "email": "foo@gmail.com"}
                    body = json.dumps(post).encode()
                    for chunked in (False, True):
                        status, data, content_type = request(sock, "/echo", body, chunked=chunked)
                        assert status == 200 and json.loads(data) == post and content_type.startswith("application/json")
                    assert request(sock, "/echo?ignored=1", body)[0] == 200
                    assert request(sock, "/posts", b"", content_length=2 * 1024 * 1024 + 1)[0] == 413
                    assert request(sock, "/missing", body)[0] == 404
                    assert request(sock, "/posts", body, method="GET")[0] in (404, 405)
                    for invalid in [b"", b"{", b"null", b"[]", b'{"content":1,"email":"a@b.com"}',
                                    b'{"content":"x"}', b'{"content":"x","email":null}', body + b" trailing"]:
                        for endpoint in ("/posts", "/echo"):
                            assert request(sock, endpoint, invalid)[0] == 400, (endpoint, invalid)
                    assert request(sock, "/posts", json.dumps(dict(post, content="")).encode())[0] == 400
                    committed = 0
                    for fixture in json.loads((ROOT / "testdata/email-validation.json").read_text()):
                        status, _, _ = request(sock, "/posts", json.dumps(dict(post, email=fixture["email"])).encode())
                        assert status == (201 if fixture["valid"] else 400), fixture
                        committed += int(fixture["valid"])

                    large = {"content": "multi-buffer 雪\u0000 " * 4096, "email": "large@example.com"}
                    for chunked in (False, True):
                        status, data, _ = request(sock, "/posts", json.dumps(large).encode(), chunked=chunked)
                        assert status == 201 and json.loads(data)["content"] == large["content"]
                        committed += 1

                    def write(index):
                        payload = {"content": f"parallel {index} 雪", "email": "parallel@example.com"}
                        status, data, content_type = request(sock, "/posts", json.dumps(payload).encode())
                        assert status == 201 and content_type.startswith("application/json")
                        result = json.loads(data)
                        assert set(result) == {"id", "user_id", "content", "created_at", "updated_at"}
                        assert result["content"] == payload["content"]
                        assert result["created_at"] == result["updated_at"] and result["created_at"] > 1_700_000_000_000
                        with sqlite3.connect(database) as observer:
                            row = observer.execute("SELECT content, user_id, created_at, updated_at FROM posts WHERE id IS ?", [result["id"]]).fetchone()
                        assert row == (result["content"], result["user_id"], result["created_at"], result["updated_at"])
                        return result["id"]

                    with ThreadPoolExecutor(max_workers=50) as pool:
                        ids = list(pool.map(write, range(200)))
                    assert len(set(ids)) == 200
                    committed += len(ids)
                    with sqlite3.connect(database) as db:
                        assert db.execute("SELECT count(*) FROM users WHERE email IS 'parallel@example.com'").fetchone()[0] == 1
                        db.execute("CREATE TRIGGER fail_post BEFORE INSERT ON posts WHEN NEW.content IS 'forced failure' BEGIN SELECT RAISE(ABORT, 'test'); END")
                    failed = {"content": "forced failure", "email": "rolledback@example.com"}
                    assert request(sock, "/posts", json.dumps(failed).encode())[0] == 500
                    with sqlite3.connect(database) as db:
                        assert db.execute("SELECT count(*) FROM users WHERE email IS ?", [failed["email"]]).fetchone()[0] == 0
                        db.execute("DROP TRIGGER fail_post")
                    write(200)
                    committed += 1
                    collision = subprocess.run(command, cwd=PROJECT, capture_output=True, timeout=10)
                    assert collision.returncode != 0
                    assert request(sock, "/echo", body)[0] == 200
                    with sqlite3.connect(database) as db:
                        assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == committed
                        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
                    print(f"{stop_signal.name}: {committed} commits, 50 clients, validation, malformed JSON, chunked bodies, rollback recovery and occupied socket passed", flush=True)
                finally:
                    server.send_signal(stop_signal)
                    try:
                        server.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
                        raise RuntimeError("server did not stop gracefully")
                    if server.returncode != 0:
                        raise RuntimeError((directory / "server.log").read_text())
                    assert not sock.exists(), "server left its socket behind"


if __name__ == "__main__":
    main()
