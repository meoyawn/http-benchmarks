#!/usr/bin/env python3
"""Untimed randomized response/persistence preflight for selected configurations."""
import argparse
import http.client
import signal
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile

import workload
import measure

ROOT = workload.ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", nargs="+", choices=measure.CONFIG_NAMES, default=measure.DEFAULT_CONFIGS)
    args = parser.parse_args()
    for name, config in measure.configurations(args.config).items():
        with tempfile.TemporaryDirectory(prefix="random-preflight-", dir="/tmp") as temporary:
            directory = Path(temporary)
            command, env, cwd, database, sock = measure.launch_configuration(name, config, directory)
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, cwd=cwd, env=dict(os.environ, **env), stdout=log, stderr=subprocess.STDOUT)
                try:
                    workload.wait_ready(server, sock)
                    for index, payload in enumerate(workload.corpus()[:64]):
                        for endpoint, status in [("echo", 200), ("posts", 201)]:
                            connection = http.client.HTTPConnection("localhost", timeout=10)
                            connection.sock = socket.socket(socket.AF_UNIX)
                            connection.sock.settimeout(10)
                            connection.sock.connect(str(sock))
                            try:
                                connection.request("POST", f"/{endpoint}", json.dumps(payload, ensure_ascii=False).encode(), {"Content-Type": "application/json"})
                                response = connection.getresponse()
                                body = json.loads(response.read())
                                if response.status != status:
                                    raise RuntimeError(f"{name} {endpoint}: {response.status}, {body}")
                                if endpoint == "echo":
                                    assert body == payload, (body, payload)
                                else:
                                    assert body["content"] == payload["content"]
                                    with sqlite3.connect(database) as db:
                                        row = db.execute("SELECT p.id, p.user_id, p.content, p.created_at, p.updated_at, u.email FROM posts p JOIN users u ON p.user_id=u.id WHERE p.id=?", (body["id"],)).fetchone()
                                    assert row[:5] == tuple(body[key] for key in ("id", "user_id", "content", "created_at", "updated_at")), (row, body)
                                    assert row[5] == payload["email"]
                            finally:
                                connection.close()
                except Exception as error:
                    raise RuntimeError(f"{name}: {error}\n{(directory / 'server.log').read_text()}") from error
                finally:
                    workload.stop(server)
                    if name.startswith(("zig", "haskell-")) and sock.exists():
                        raise RuntimeError(f"{name} left its socket behind")
            accepted = (0, 128 + signal.SIGTERM, -signal.SIGTERM) if name == "kotlin-jvm" else (0,)
            if server.returncode not in accepted:
                raise RuntimeError((directory / "server.log").read_text())
            workload.verify(database, 64)
            print(f"{name}: 64 randomized echo round trips and 64 committed post responses verified", flush=True)


if __name__ == "__main__":
    main()
