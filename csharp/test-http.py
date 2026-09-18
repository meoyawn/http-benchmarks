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
import shlex
import shutil
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def request(socket_path, endpoint, body, method="POST", chunked=False, content_type="application/json"):
    client = http.client.HTTPConnection("localhost", timeout=10)
    client.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.sock.settimeout(10)
    try:
        client.sock.connect(str(socket_path))
        client.request(method, endpoint, iter([body[:10], body[10:]]) if chunked else body,
                       {"Content-Type": content_type}, encode_chunked=chunked)
        response = client.getresponse()
        data = response.read()
        if response.status in (200, 201):
            assert response.getheader("Content-Length") == str(len(data)), "incorrect serialized byte length"
            assert response.getheader("Transfer-Encoding") is None, "JSON unexpectedly used chunked framing"
        return response.status, data, response.getheader("Content-Type")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    launch = parser.add_mutually_exclusive_group()
    launch.add_argument("--command", default="pkgx dotnet bin/Release/net10.0/csharp.dll")
    launch.add_argument("--bundle", type=Path, help="Test a copy of only this executable, with a fresh native extraction cache")
    parser.add_argument("--args", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="csharp-http-test-") as temporary:
        directory = Path(temporary)
        environment = os.environ.copy()
        command = shlex.split(args.command)
        if args.bundle:
            release = directory / "release"
            release.mkdir()
            executable = release / "csharp"
            shutil.copy2(args.bundle.resolve(), executable)
            environment['DOTNET_BUNDLE_EXTRACT_BASE_DIR'] = str(directory / "extracted")
            command = ['pkgx', '+dotnet', str(executable)]
        working_directory = directory if args.bundle else PROJECT
        database = directory / "test.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
        reported = json.loads(subprocess.check_output(command + ['-db', str(database), '-check-config'],
                                                     cwd=working_directory, env=environment, text=True))
        config = json.loads((ROOT / 'db/sqlite-config.json').read_text())
        assert reported['version'] == config['version']
        for name, expected in config['pragmas'].items():
            assert reported['pragmas'][name] == str(expected), (name, reported['pragmas'][name], expected)
        assert reported['pragmas']['page_size'] == str(config['runtime']['page_bytes'])
        for define in config['defines']:
            if define.startswith('SQLITE_') and define != 'SQLITE_ENABLE_JSON1':
                option = define.removeprefix('SQLITE_')
                assert option in reported['compile_options'] or option.removesuffix('=1') in reported['compile_options'], option
        socket_path = Path(f"/tmp/csharp-test-{os.getpid()}.sock")
        if os.path.lexists(socket_path):
            raise RuntimeError(f"socket exists: {socket_path}")
        command = command + ["-db", str(database), "-socket", str(socket_path)] + shlex.split(args.args)
        with (directory / "server.log").open("w") as log:
            server = subprocess.Popen(command, cwd=working_directory, env=environment, stdout=log, stderr=subprocess.STDOUT)
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
                # Preserve the normal ASP.NET JSON defaults and charset handling.
                uppercase = json.dumps({key.upper(): value for key, value in post.items()}).encode()
                assert json.loads(request(socket_path, "/echo", uppercase)[1]) == post
                utf16 = json.dumps(post, ensure_ascii=False).encode("utf-16-le")
                assert json.loads(request(socket_path, "/echo", utf16, content_type="application/json; charset=utf-16")[1]) == post
                assert request(socket_path, "/echo", body, content_type="text/plain")[0] == 415
                assert request(socket_path, "/missing", body)[0] == 404
                assert request(socket_path, "/posts", body, method="GET")[0] in (404, 405)
                for invalid in [b"", b"{", b"null", b"[]", b'{"content":1,"email":"a@b.com"}', b'{"content":"x"}', body + b" trailing", b'{"content":"\xff","email":"a@b.com"}']:
                    for endpoint in ("/posts", "/echo"):
                        assert request(socket_path, endpoint, invalid)[0] == 400, (endpoint, invalid)
                assert request(socket_path, "/posts", json.dumps(dict(post, content="")).encode())[0] == 400
                # Failure after inserting the user must roll back both statements.
                # A deferred foreign key fails at COMMIT rather than sqlite3_step.
                with sqlite3.connect(database) as observer:
                    observer.executescript("""
                        CREATE TABLE guard (user_id INTEGER REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED);
                        CREATE TRIGGER reject_statement BEFORE INSERT ON posts WHEN NEW.content = 'statement-failure'
                        BEGIN SELECT RAISE(ABORT, 'injected statement failure'); END;
                        CREATE TRIGGER reject_commit AFTER INSERT ON posts WHEN NEW.content = 'commit-failure'
                        BEGIN INSERT INTO guard VALUES (-1); END;
                    """)
                for value in ('statement-failure', 'commit-failure'):
                    failed = {"content": value, "email": value + '@example.com'}
                    assert request(socket_path, '/posts', json.dumps(failed).encode())[0] == 500
                    with sqlite3.connect(database) as observer:
                        assert observer.execute('SELECT count(*) FROM users WHERE email = ?', [failed['email']]).fetchone()[0] == 0
                        assert observer.execute('SELECT count(*) FROM posts').fetchone()[0] == 0
                with sqlite3.connect(database) as observer:
                    observer.executescript('DROP TRIGGER reject_statement; DROP TRIGGER reject_commit; DROP TABLE guard;')
                committed = 0
                for fixture in json.loads((ROOT / "testdata/email-validation.json").read_text()):
                    status, _, _ = request(socket_path, "/posts", json.dumps(dict(post, email=fixture["email"])).encode())
                    assert status == (201 if fixture["valid"] else 400), fixture
                    committed += int(fixture["valid"])

                large = {"content": "multi-buffer 雪\u0000 " * 4096, "email": "large@example.com"}
                for chunked in (False, True):
                    status, data, _ = request(socket_path, "/posts", json.dumps(large).encode(), chunked=chunked)
                    assert status == 201 and json.loads(data)["content"] == large["content"], (status, data[:300], len(data), len(large["content"]))
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
                with sqlite3.connect(database) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
                    blocker.execute("BEGIN IMMEDIATE")
                    pending = pool.submit(write, 200)
                    try:
                        time.sleep(0.05)
                        assert not pending.done(), "write bypassed SQLite's transaction lock"
                        assert request(socket_path, "/echo", body)[0] == 200
                    finally:
                        blocker.rollback()
                    assert pending.result() not in ids
                    committed += 1
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == committed
                    assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
                print(f"HTTP checks passed: {committed} verified commits, 50 concurrent clients, shared validation, malformed JSON, chunked echo, all RETURNING columns, delayed writer wake-up", flush=True)
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
