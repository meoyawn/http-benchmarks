#!/usr/bin/env python3
"""Exercise the built server through HTTP/1.1 over real Unix sockets."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import socket
import shlex
import sqlite3
import subprocess
import tempfile
import time
import unittest
import uuid

PROJECT = Path(__file__).resolve().parent
BINARY = Path(os.environ.get("OCAML_BENCH_BINARY", PROJECT / "_build/default/bin/bench.exe"))
SERVER_ARGS = shlex.split(os.environ.get("OCAML_BENCH_TEST_ARGS", ""))
MIGRATION = (PROJECT.parent / "db/migrations/001_init.up.sql").read_text()


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ocaml-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / "db.sqlite"
        self.socket = Path(f"/tmp/ocaml-test-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock")
        self.assertFalse(os.path.lexists(self.socket))
        with sqlite3.connect(self.database) as db:
            db.executescript(MIGRATION)
        self.log = (self.directory / "server.log").open("w")
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen([str(BINARY), "-db", str(self.database), "-socket", str(self.socket)] + SERVER_ARGS,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail((self.directory / "server.log").read_text())
            with socket.socket(socket.AF_UNIX) as client:
                try:
                    client.connect(str(self.socket))
                    return
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(0.01)
        self.fail("server did not listen")

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                self.fail("server did not stop")
        self.assertEqual(self.process.returncode, 0, (self.directory / "server.log").read_text())
        self.assertFalse(os.path.lexists(self.socket))

    def request(self, path, body, method="POST", chunks=False):
        connection = http.client.HTTPConnection("localhost", timeout=15)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(15)
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        headers = {"Content-Type": "application/json"}
        if chunks:
            headers["Transfer-Encoding"] = "chunked"
            body = [body[i:i + 3] for i in range(0, len(body), 3)]
        try:
            connection.sock.connect(str(self.socket))
            connection.request(method, path, body, headers, encode_chunked=chunks)
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(int(response.getheader("Content-Length")), len(raw))
            return response.status, raw, dict(response.getheaders())
        finally:
            connection.close()

    def test_echo_parses_and_serializes(self):
        payload = {"content": 'quote " \\ newline\n\x00 café 🐫', "email": "foo@gmail.com"}
        for chunks in (False, True):
            status, raw, _ = self.request("/echo?check=1", payload, chunks=chunks)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw), payload)
        status, raw, _ = self.request("/echo", {**payload, "ignored": [1, 2]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), payload)

    def test_pipelining_half_close_and_large_headers(self):
        payloads = [{"content": str(index) + "x" * 5000, "email": "foo@gmail.com"} for index in range(20)]
        requests = []
        for payload in payloads:
            body = json.dumps(payload).encode()
            requests.append(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nX-Large: " + b"a" * 5000 +
                            b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        with socket.socket(socket.AF_UNIX) as client, ThreadPoolExecutor(max_workers=1) as sender:
            client.settimeout(5)
            client.connect(str(self.socket))
            def send():
                client.sendall(b"".join(requests))
                client.shutdown(socket.SHUT_WR)
            sending = sender.submit(send)
            stream = client.makefile("rb")
            for payload in payloads:
                self.assertIn(b"200", stream.readline())
                headers = {}
                while (line := stream.readline()) not in (b"\r\n", b""):
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                self.assertEqual(json.loads(stream.read(int(headers["content-length"]))), payload)
            self.assertEqual(stream.read(), b"")
            stream.close()
            sending.result(timeout=5)

    def test_shutdown_with_an_inflight_write(self):
        with sqlite3.connect(self.database, timeout=5) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.request, "/posts", {"email": "closing@example.com", "content": "queued"})
                time.sleep(0.05)
                self.process.terminate()
                time.sleep(0.05)
                blocker.rollback()
                try:
                    status, _, _ = pending.result(timeout=10)
                    self.assertIn(status, (201, 500))
                except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                    pass
        self.process.wait(timeout=10)
        self.assertEqual(self.process.returncode, 0)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])

    def test_insert_persists_returned_columns_and_reuses_user(self):
        content = 'quote " \\ newline\n\x00 café 🐫'
        for email in ("foo@gmail.com", "FOO@gmail.com"):
            status, raw, _ = self.request("/posts", {"email": email, "content": content})
            self.assertEqual(status, 201, raw)
            row = json.loads(raw)
            self.assertEqual(set(row), {"id", "user_id", "content", "created_at", "updated_at"})
            with sqlite3.connect(self.database) as db:
                stored = db.execute("SELECT id,user_id,content,created_at,updated_at FROM posts WHERE id=?", (row["id"],)).fetchone()
            self.assertEqual(stored, tuple(row[k] for k in ("id", "user_id", "content", "created_at", "updated_at")))
            self.assertEqual(row["content"], content)
            self.assertEqual(row["user_id"], 1)
            self.assertIsInstance(row["created_at"], int)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()[0], 2)

    def test_invalid_requests_do_not_write(self):
        for body in ('', '{', '[]', 'null', '{} trailing', '{"content":1,"email":"a@b"}',
                     '{"email":"a@b","content":"ok", "ignored":NaN}',
                     '{"email":"a@b","content":"ok", "ignored":Infinity}',
                     '{/* comment */"email":"a@b","content":"ok"}',
                     '{"email":"a@b","content":"ok", "ignored":(1,2)}',
                     '{"email":"a@b","content":"ok", "ignored":<"tag">}',
                     '\x0b{"email":"a@b","content":"ok"}',
                     {"content": "", "email": "foo@gmail.com"},
                     {"content": "ok", "email": "bad"},
                     {"content": "ok", "email": "a@b trailing"},
                     {"content": "ok", "email": "Alice <a@b>"},
                     {"email": "foo@gmail.com"}):
            with self.subTest(body=body):
                self.assertEqual(self.request("/posts", body)[0], 400)
        self.assertEqual(self.request("/missing", {})[0], 404)
        status, _, headers = self.request("/posts", {}, method="GET")
        self.assertEqual(status, 405)
        self.assertEqual(headers["allow"], "POST")
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 0)

    def test_failed_post_rolls_back_user_and_recovers(self):
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TRIGGER reject_post BEFORE INSERT ON posts WHEN NEW.content='reject' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.request("/posts", {"email": "rollback@example.com", "content": "reject"})[0], 500)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM posts").fetchone()[0], 0)
        self.assertEqual(self.request("/posts", {"email": "good@example.com", "content": "accepted"})[0], 201)

    def test_shared_email_validation(self):
        cases = json.loads((PROJECT.parent / "testdata/email-validation.json").read_text())
        for case in cases:
            with self.subTest(email=case["email"]):
                status, _, _ = self.request("/posts", {"email": case["email"], "content": "valid"})
                self.assertEqual(status, 201 if case["valid"] else 400)

    def test_concurrent_posts(self):
        def send(index):
            status, raw, _ = self.request("/posts", {"email": "shared@example.com", "content": f"post {index}"})
            self.assertEqual(status, 201, raw)
            return json.loads(raw)["id"]
        with ThreadPoolExecutor(max_workers=20) as pool:
            ids = list(pool.map(send, range(100)))
        self.assertEqual(len(set(ids)), 100)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM posts").fetchone()[0], 100)

    def test_oversized_body(self):
        self.assertEqual(self.request("/echo", {"content": "x" * (1024 * 1024), "email": "a@b"})[0], 413)

    def test_large_nonrepeating_body(self):
        payload = {"email": "long@example.com", "content": " ".join(str(i) for i in range(15000))}
        status, raw, _ = self.request("/echo", payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), payload)

    def test_disconnected_clients_do_not_kill_server(self):
        body = b'{"content":"ok","email":"a@b"}'
        request = b"POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        for _ in range(100):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.socket))
                client.sendall(request)
        self.assertEqual(self.request("/echo", {"content": "ok", "email": "a@b"})[0], 200)

    def test_startup_rejects_existing_path_and_unmigrated_db(self):
        def rejected(database, socket_path):
            result = subprocess.run([str(BINARY), "-db", str(database), "-socket", str(socket_path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
        path = self.directory / "existing"
        path.write_text("preserve me")
        rejected(self.database, path)
        self.assertEqual(path.read_text(), "preserve me")
        rejected(self.database, self.socket)
        empty = self.directory / "empty.sqlite"
        empty.touch()
        missing_socket = self.directory / "empty.sock"
        rejected(empty, missing_socket)
        self.assertFalse(missing_socket.exists())
        rejected(self.directory / "missing.sqlite", missing_socket)


if __name__ == "__main__":
    unittest.main(verbosity=2)
