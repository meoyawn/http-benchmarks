#!/usr/bin/env python3
"""Check valid corpus generation, strict status gates and draining over real UDS."""
import http.server
import json
from pathlib import Path
import socketserver
import subprocess
import tempfile
import threading
import unittest

import workload


class DriverTests(unittest.TestCase):
    def test_corpus(self):
        rows = workload.corpus()
        self.assertEqual(len({r["email"] for r in rows}), workload.CORPUS_SIZE)
        self.assertTrue(all(workload.EMAIL.fullmatch(r["email"]) and 32 <= len(r["content"]) <= 256 for r in rows))
        self.assertTrue(any('"' in r["content"] and "\n" in r["content"] for r in rows))
        self.assertTrue(any(not r["content"].isascii() for r in rows))

    def check_status(self, status, expected, succeeds):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append(json.loads(body))
                if status == 0:
                    self.close_connection = True
                    return
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                if status == 302:
                    self.send_header("Location", "/echo")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        with tempfile.TemporaryDirectory(prefix="bombard-", dir="/tmp") as temporary:
            directory = Path(temporary)
            path = directory / "corpus.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in workload.corpus()[:32]))
            with Server(str(directory / "http.sock"), Handler) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    result = subprocess.run([str(workload.BINARY), "-corpus", str(path), "-unix-socket", str(directory / "http.sock"),
                                             "-duration", "100ms", "-connections", "2", "-shards", "2", "-cpus", "2", "-processes", "2", "-status", str(expected)],
                                            capture_output=True, text=True, timeout=15)
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(result.returncode == 0, succeeds, result.stderr)
            if not succeeds:
                self.assertIn("expected only HTTP", result.stderr)
                return
            report = json.loads(result.stdout)
            self.assertEqual(sum(report["histogram"]["Counts"]), report["requests"])
            self.assertEqual(sum(child["requests"] for child in report["children"]), report["requests"])
            self.assertGreater(report["requests"], 0)
            if succeeds:
                self.assertEqual(report["requests"], len(received))
                self.assertEqual(report["status_codes"], {str(expected): len(received)})
                self.assertFalse(report["errors"])
                self.assertGreater(len({r["email"] for r in received}), 1)

    def test_echo(self):
        self.check_status(200, 200, True)

    def test_posts(self):
        self.check_status(201, 201, True)

    def test_wrong_success_status(self):
        self.check_status(200, 201, False)

    def test_server_error(self):
        self.check_status(500, 201, False)

    def test_redirect_not_followed(self):
        self.check_status(302, 200, False)

    def test_disconnect(self):
        self.check_status(0, 200, False)


if __name__ == "__main__":
    unittest.main()
