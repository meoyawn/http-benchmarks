#!/usr/bin/env python3
"""Exercise the packaged JVM or native application over its real Unix socket."""

import argparse
import concurrent.futures
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("workload", PROJECT / "measure-http.py")
workload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path)
    parser.add_argument("--java", default=str(Path(os.environ.get("JAVA_HOME", "/usr")) / "bin/java"))
    parser.add_argument("--jar", type=Path, default=PROJECT / "build/libs/kotlin-1.0-all.jar")
    parser.add_argument("--jvm-arg", "--runtime-arg", action="append", default=[])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kotlin-verify-") as temporary:
        database = Path(temporary) / "bench.sqlite"
        uds = Path(f"/tmp/kotlin-verify-{os.getpid()}.sock")
        if os.path.lexists(uds):
            raise RuntimeError(f"socket already exists: {uds}")
        with sqlite3.connect(database) as db:
            db.executescript((PROJECT.parent / "db/migrations/001_init.up.sql").read_text())
        properties = [f"-Ddb.path={database}", f"-Dhttp.socket={uds}"]
        command = ([str(args.native.resolve()), *args.jvm_arg, *properties] if args.native else
                   [args.java, "--enable-native-access=ALL-UNNAMED", *args.jvm_arg,
                    *properties, "-jar", str(args.jar.resolve())])

        def request(endpoint, payload):
            client = http.client.HTTPConnection("localhost", timeout=10)
            client.sock = socket.socket(socket.AF_UNIX)
            client.sock.settimeout(10)
            try:
                client.sock.connect(str(uds))
                client.request("POST", endpoint, payload if isinstance(payload, str) else json.dumps(payload),
                               {"Content-Type": "application/json"})
                response = client.getresponse()
                body = response.read()
                return response.status, json.loads(body) if response.status < 400 else body
            finally:
                client.close()

        expected = []
        with tempfile.TemporaryFile(mode="w+") as log:
            server = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
            try:
                workload.wait_ready(server, uds)
                for content in ['quotes " slash \\ newline\n NUL\0 café 🐈', 'x' * 20000]:
                    payload = {"email": "test@example.com", "content": content}
                    assert request("/echo", payload) == (200, payload)
                    status, post = request("/posts", payload)
                    assert status == 201 and post["content"] == content and post["user_id"] == 1
                    assert set(post) == {"id", "user_id", "content", "created_at", "updated_at"}
                    assert post["created_at"] > 0 and post["created_at"] == post["updated_at"]
                    # The row must already be committed when its response arrives.
                    with sqlite3.connect(database) as db:
                        assert db.execute("SELECT content FROM posts WHERE id=?", (post["id"],)).fetchone() == (content,)
                    expected.append(post)
                status, post = request("/posts", {"email": "TEST@example.com", "content": "same user"})
                assert status == 201 and post["user_id"] == 1
                expected.append(post)
                for case in json.loads((PROJECT.parent / "testdata/email-validation.json").read_text()):
                    status, post = request("/posts", {"email": case["email"], "content": "validation"})
                    assert status == (201 if case["valid"] else 400), case
                    if status == 201:
                        expected.append(post)
                assert request("/posts", {"email": "test@example.com", "content": ""})[0] == 400
                for invalid in ['{}', 'null', '[]', '{', '{"email":5,"content":"x"}',
                                '{"email":"x","content":"y",}', '{"email":"x","content":"y"} true']:
                    for endpoint in ["/posts", "/echo"]:
                        assert request(endpoint, invalid)[0] == 400, invalid
                with sqlite3.connect(database) as db:
                    db.execute("CREATE TRIGGER reject_test BEFORE INSERT ON posts WHEN NEW.content='reject' "
                               "BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
                assert request("/posts", {"email": "test@example.com", "content": "reject"})[0] == 500
                with sqlite3.connect(database) as db:
                    db.execute("DROP TRIGGER reject_test")
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    for status, post in pool.map(lambda i: request("/posts", {
                        "email": "test@example.com", "content": f"concurrent {i}"}), range(100)):
                        assert status == 201
                        expected.append(post)
            except BaseException:
                log.seek(0)
                print(log.read())
                raise
            finally:
                workload.stop(server)
            assert server.returncode in (0, 143), server.returncode
        with sqlite3.connect(database) as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == len(expected)
            assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()[0] == len(expected)
        print(f"Passed: echo, committed writes, Unicode/NUL/large strings, validation, rollback/recovery, concurrency, shutdown, integrity ({len(expected)} posts)")


if __name__ == "__main__":
    main()
