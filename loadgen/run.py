#!/usr/bin/env python3
"""Send randomized valid JSON to an already-running benchmark server."""
import argparse
import json
from pathlib import Path
import subprocess

import workload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint", choices=workload.PAYLOADS)
    parser.add_argument("--socket", default="/tmp/benchmark.sock", help="empty string selects TCP")
    parser.add_argument("--url", help="override URL, e.g. http://localhost:3000/posts")
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--cpus", type=int, default=5)
    parser.add_argument("--processes", type=int, default=5)
    parser.add_argument("--output", type=Path, help="save raw metrics and merged histogram")
    args = parser.parse_args()
    corpus = workload.prepare()
    command = [str(workload.BINARY), "-corpus", str(corpus), "-url", args.url or f"http://localhost/{args.endpoint}",
               "-unix-socket", args.socket, "-duration", args.duration, "-cpus", str(args.cpus),
               "-processes", str(args.processes), "-status", "201" if args.endpoint == "posts" else "200"]
    result = subprocess.run(command, stdout=subprocess.PIPE, text=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.stdout)
    if result.stdout:
        report = json.loads(result.stdout)
        report.pop("histogram", None)
        report.pop("children", None)
        print(json.dumps(report, indent=2))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
