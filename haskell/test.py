#!/usr/bin/env python3
"""HTTP contract, shared email fixtures, SQLite options and rollback recovery."""
import http.client
import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT.parent / 'loadgen'))
import workload


def request(sock, path, body, method='POST'):
    client = http.client.HTTPConnection('localhost', timeout=10)
    client.sock = socket.socket(socket.AF_UNIX)
    client.sock.settimeout(10)
    client.sock.connect(str(sock))
    try:
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        client.request(method, path, body, {'Content-Type': 'application/json'})
        response = client.getresponse()
        result = (response.status, response.read(), response.getheaders())
        return result
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparisons', action='store_true')
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--capabilities', type=int, default=4)
    args = parser.parse_args()
    binary = (args.binary or PROJECT / 'bin' / ('haskell-comparison' if args.comparisons else 'haskell-benchmark')).resolve()
    with tempfile.TemporaryDirectory(prefix='haskell-test-') as temp:
        directory = Path(temp)
        database = directory / 'test.sqlite'
        with sqlite3.connect(database) as db:
            db.executescript((PROJECT.parent / 'db/migrations/001_init.up.sql').read_text())
        settings = json.loads(subprocess.check_output([str(binary), '-db', str(database), '-inspect']))
        config = json.loads((PROJECT.parent / 'db/sqlite-config.json').read_text())
        assert settings['version'] == [config['version']], settings
        for key, value in config['pragmas'].items():
            assert dict(settings['pragmas'])[key] == [str(value)], (key, settings)
        # SQLite reports only applicable public compile options, not feature
        # macros made redundant by newer releases or platform feature defines.
        import ctypes
        native = ctypes.CDLL(str(PROJECT / '.tools/sqlite/lib' / ('libsqlite3.dylib' if sys.platform == 'darwin' else 'libsqlite3.so')))
        native.sqlite3_compileoption_get.restype = ctypes.c_char_p
        expected = []
        index = 0
        while True:
            option = native.sqlite3_compileoption_get(index)
            if option is None:
                break
            expected.append(option.decode())
            index += 1
        assert sorted(settings['compile_options']) == sorted(expected)
        for http in (('warp', 'snap') if args.comparisons else ('warp',)):
            for codec in ('aeson', 'jsonifier'):
                sock = directory / 'http.sock'
                with (directory / 'server.log').open('w') as log:
                    server = subprocess.Popen([str(binary), '-db', str(database), '-socket', str(sock), '-http', http, '-codec', codec,
                                               '+RTS', f'-N{args.capabilities}', '-RTS'], stdout=log, stderr=log)
                    try:
                        workload.wait_ready(server, sock)
                        for fixture in json.loads((PROJECT.parent / 'testdata/email-validation.json').read_text()):
                            status, _, _ = request(sock, '/posts', {'email': fixture['email'], 'content': 'fixture'})
                            assert status == (201 if fixture['valid'] else 400), (fixture, status)
                        for body in (b'', b'{', b'[]', b'null', b'{}', b'{"email":2,"content":"x"}', b'{"email":"a@b.co","content":null}',
                                     b'{"email":"a@b.co","content":"x"} {}', b'{"email":"a@b.co","content":"x", "ignored":NaN}',
                                     b'{"email":"a@b.co","content":"\xff"}', b'{"email":"a@b.co","content":"\x01"}'):
                            assert request(sock, '/echo', body)[0] == 400, body
                        assert request(sock, '/posts', {'email': 'a@b.co', 'content': ''})[0] == 400
                        assert request(sock, '/missing', b'{}')[0] == 404
                        status, _, headers = request(sock, '/echo', b'{}', 'GET')
                        assert status == 405 and dict((k.lower(), v) for k, v in headers).get('allow') == 'POST', (status, headers)
                        special = {'email': 'special@example.test', 'content': 'quote " slash \\ newline\nNUL\0 café λ 雪 🙂'}
                        assert json.loads(request(sock, '/echo', special)[1]) == special
                        assert json.loads(request(sock, '/echo?query=yes', special)[1]) == special
                        # An abort after the new user insert must roll back both
                        # tables and leave all prepared statements reusable.
                        with sqlite3.connect(database) as db:
                            before = db.execute('SELECT count(*) FROM posts').fetchone()[0]
                            db.execute("CREATE TRIGGER fail_post BEFORE INSERT ON posts WHEN NEW.content IS 'force rollback' BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
                        assert request(sock, '/posts', {'email': 'rollback@example.test', 'content': 'force rollback'})[0] == 500
                        with sqlite3.connect(database) as db:
                            assert db.execute("SELECT count(*) FROM users WHERE email IS 'rollback@example.test'").fetchone()[0] == 0
                            assert db.execute('SELECT count(*) FROM posts').fetchone()[0] == before
                            db.execute('DROP TRIGGER fail_post')
                        status, body, _ = request(sock, '/posts', special)
                        assert status == 201, body
                        row = json.loads(body)
                        with sqlite3.connect(database) as db:
                            stored = db.execute('SELECT id, user_id, content, created_at, updated_at FROM posts WHERE id IS ?', (row['id'],)).fetchone()
                            assert stored == tuple(row[k] for k in ('id', 'user_id', 'content', 'created_at', 'updated_at'))
                        if http == 'warp' and codec == 'jsonifier':
                            # The optimized timer must still expire idle connections.
                            with socket.socket(socket.AF_UNIX) as idle:
                                idle.settimeout(13)
                                idle.connect(str(sock))
                                started = time.monotonic()
                                assert idle.recv(1) == b''
                                assert 8 <= time.monotonic() - started <= 13
                            assert request(sock, '/echo', special)[0] == 200
                    finally:
                        workload.stop(server)
                    assert server.returncode == 0, (directory / 'server.log').read_text()
                    assert not sock.exists()
                print(f'{http}/{codec}: contract, fixtures, rollback and commit visibility passed', flush=True)
        occupied = directory / 'occupied.sock'
        occupied.write_text('owned by someone else')
        result = subprocess.run([str(binary), '-db', str(database), '-socket', str(occupied)], capture_output=True)
        assert result.returncode != 0 and occupied.read_text() == 'owned by someone else'
        print('SQLite engine/options/pragmas and socket ownership passed')


if __name__ == '__main__':
    main()
