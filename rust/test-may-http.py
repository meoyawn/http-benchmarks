#!/usr/bin/env python3
"""MAY framing, backpressure, socket ownership and accepted-write shutdown checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("http_checks", PROJECT / "test-http.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
BODY = b'{"content":"hello","email":"test@example.com"}'


def connect(path):
    stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stream.settimeout(10)
    stream.connect(str(path))
    return stream


def wire(body=BODY, endpoint="echo", extra=b""):
    return (f"POST /{endpoint} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n".encode()
            + extra + b"\r\n" + body)


def response(stream):
    result = http.client.HTTPResponse(stream)
    result.begin()
    return result.status, result.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=PROJECT / "target/may-release/may-benchmark")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="may-http-") as temporary:
        root = Path(temporary)
        database = root / "db.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
        path = Path(f"/tmp/may-http-{os.getpid()}.sock")
        assert not os.path.lexists(path)
        command = [str(args.binary.resolve()), "-db", str(database), "-socket", str(path), "-workers", str(args.workers)]
        # A failed bind must preserve another process's socket and ordinary files.
        for existing in ("file", "socket"):
            listener = None
            if existing == "file":
                path.write_text("owned by someone else")
            else:
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(str(path))
                listener.listen()
            inode = path.lstat().st_ino
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
            assert result.returncode != 0 and path.lstat().st_ino == inode, result.stdout
            if listener:
                listener.close()
            path.unlink()
        with (root / "server.log").open("w") as log:
            server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while not path.exists():
                    assert server.poll() is None and time.monotonic() < deadline
                    time.sleep(0.005)
                # Reuse one connection across malformed JSON, chunked trailers,
                # and fixed-length requests. Then test a pipelined final close.
                with connect(path) as stream:
                    stream.sendall(wire(b"{"))
                    assert response(stream)[0] == 400
                    stream.sendall(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n")
                    chunks = (f"{len(BODY):X};foo=bar\r\n".encode() + BODY + b"\r\n0\r\nX-Trailer: yes\r\n\r\n")
                    for byte in chunks:
                        stream.sendall(bytes([byte]))
                    assert json.loads(response(stream)[1]) == json.loads(BODY)
                    stream.sendall(wire() + wire(extra=b"Connection: close\r\n"))
                    # HTTPResponse may read ahead; use one persistent file for
                    # the pipelined responses so no buffered bytes are lost.
                    data = b""
                    while part := stream.recv(65536):
                        data += part
                    assert data.count(b"HTTP/1.1 200") == 2, data
                malformed = [
                    b"Content-Length: -1", b"Content-Length: nope", b"Content-Length: +1",
                    b"Content-Length: 99999999999999999999999999999999999999999",
                    b"Content-Length: 1\r\nContent-Length: 2", b"Content-Length: 1, 1",
                    b"Content-Length: 1\r\nTransfer-Encoding: chunked",
                    b"Transfer-Encoding: gzip", b"Transfer-Encoding: chunked, chunked",
                    b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked",
                ]
                for framing in malformed:
                    with connect(path) as stream:
                        stream.sendall(b"POST /echo HTTP/1.1\r\nHost: localhost\r\n" + framing + b"\r\n\r\n")
                        assert response(stream)[0] == 400, framing
                        assert stream.recv(1) == b""
                for body in [b"Z\r\n", b"\r\n", b"1\r\naXX", b"0\r\nbad trailer\r\n\r\n", b"0\r\nContent-Length: 1\r\n\r\n"]:
                    with connect(path) as stream:
                        stream.sendall(b"POST /echo HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n" + body)
                        assert response(stream)[0] == 400, body
                for framing, body in [(b"Content-Length: 2097153", b""), (b"Transfer-Encoding: chunked", b"200001\r\n")]:
                    with connect(path) as stream:
                        stream.sendall(b"POST /echo HTTP/1.1\r\n" + framing + b"\r\n\r\n" + body)
                        assert response(stream)[0] == 413
                with connect(path) as stream:
                    stream.sendall(b"POST /echo HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n180000\r\n" + b"x" * 0x180000 + b"\r\n90000\r\n")
                    assert response(stream)[0] == 413, "decoded chunk sizes must accumulate"
                limit = 2 * 1024 * 1024
                empty = json.dumps({"content": "", "email": "limit@example.com"}).encode()
                exact = json.dumps({"content": "x" * (limit - len(empty)), "email": "limit@example.com"}).encode()
                assert len(exact) == limit
                for chunked in (False, True):
                    status, data, _ = checks.request(path, "/echo", exact, chunked=chunked)
                    assert status == 200 and json.loads(data) == json.loads(exact)
                # Exercise partial socket writes and coroutine-aware backpressure.
                large = json.dumps({"content": "x" * 1_000_000, "email": "big@example.com"}).encode()
                with connect(path) as slow:
                    slow.sendall(wire(large))
                    time.sleep(0.05)
                    assert checks.request(path, "/echo", BODY)[0] == 200
                    status, data = response(slow)
                    assert status == 200 and json.loads(data) == json.loads(large)
                # A half-closed request still gets its response.
                with connect(path) as stream:
                    stream.sendall(wire())
                    stream.shutdown(socket.SHUT_WR)
                    assert response(stream)[0] == 200
                # During shutdown, accepted jobs retain their per-commit replies.
                # Idle sockets and incomplete headers must not delay draining.
                idle = connect(path)
                partial = connect(path)
                partial.sendall(b"POST /echo HTTP/1.1\r\nHost:")
                writes = [connect(path) for _ in range(50)]
                with sqlite3.connect(database) as blocker:
                    blocker.execute("BEGIN IMMEDIATE")
                    for stream in writes:
                        stream.sendall(wire(endpoint="posts"))
                    time.sleep(0.2)
                    assert checks.request(path, "/echo", BODY)[0] == 200
                    server.terminate()
                    time.sleep(0.1)
                    assert server.poll() is None, "shutdown abandoned an accepted write"
                    blocker.rollback()
                with ThreadPoolExecutor(max_workers=50) as pool:
                    returned = list(pool.map(response, writes))
                assert all(status == 201 for status, _ in returned), returned
                assert len({json.loads(body)["id"] for _, body in returned}) == 50
                for stream in writes + [idle, partial]:
                    stream.close()
                server.wait(timeout=15)
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT count(*) FROM posts").fetchone() == (50,)
                    assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                assert server.returncode == 0 and not path.exists()
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
                if server.returncode != 0:
                    print((root / "server.log").read_text())
        # Removing/replacing the pathname while running must not make shutdown
        # unlink the replacement or prevent the listener coroutine from stopping.
        server = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            assert b"Listening on" in server.stdout.readline()
            path.unlink()
            path.write_text("replacement")
            server.terminate()
            server.communicate(timeout=15)
            assert server.returncode == 0 and path.read_text() == "replacement"
        finally:
            if server.poll() is None:
                server.kill()
                server.wait()
            path.unlink(missing_ok=True)
    print("MAY checks passed: framing, keep-alive, limits, backpressure, socket ownership, 50 accepted writes drained on shutdown")


if __name__ == "__main__":
    main()
