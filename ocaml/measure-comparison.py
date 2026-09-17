#!/usr/bin/env python3
"""Rotate Go, Kotlin and multicore OCaml through the same UDS workload."""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess

import workload

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
spec = importlib.util.spec_from_file_location("ocaml_measure", PROJECT / "measure-http.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--ocaml-domains", type=int, nargs="+", default=[1])
    parser.add_argument("--java", default=str(Path(os.environ.get("JAVA_HOME", "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home")) / "bin/java"))
    args = parser.parse_args()
    if args.rounds < 1 or min(args.ocaml_domains) < 1 or len(set(args.ocaml_domains)) != len(args.ocaml_domains):
        parser.error("positive rounds and unique positive OCaml domain counts are required")
    artifacts = {"go": ROOT / "go/bench",
                 "kotlin": ROOT / "kotlin-vertx-panama/build/libs/kotlin-vertx-panama-1.0-all.jar"}
    artifacts.update({f"ocaml-{domains}": PROJECT / "_build/default/bin/bench.exe" for domains in args.ocaml_domains})
    sqlite_library = PROJECT / ".tools/sqlite/lib" / ("libsqlite3.dylib" if platform.system() == "Darwin" else "libsqlite3.so")
    sqlite_hash = hashlib.sha256(sqlite_library.read_bytes()).hexdigest()
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in artifacts.items()}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {name: {endpoint: [] for endpoint in workload.PAYLOADS} for name in artifacts}
    verification = {name: [] for name in artifacts}
    names = list(artifacts)
    for index in range(args.rounds):
        shift = index % len(names)
        for name in names[shift:] + names[:shift]:
            directory = output / f"{name}-{index + 1}"
            directory.mkdir()
            database = directory / "bench.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
            socket = Path(f"/tmp/http-compare-{os.getpid()}-{name}.sock")
            if os.path.lexists(socket):
                raise RuntimeError(f"socket exists: {socket}")
            if name == "kotlin":
                command = [args.java, "-server", "-XX:+PerfDisableSharedMem", "--enable-native-access=ALL-UNNAMED",
                           f"-Ddb.path={database}", f"-Dhttp.socket={socket}", "-jar", str(artifacts[name])]
            else:
                command = [str(artifacts[name]), "-db", str(database), "-socket", str(socket)]
                if name.startswith("ocaml-"):
                    command += ["-domains", name.removeprefix("ocaml-")]
            (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                try:
                    workload.wait_ready(server, socket)
                    for endpoint in workload.PAYLOADS:
                        result = workload.measure(server, endpoint, socket, directory / endpoint, ["pkgx", "oha"])
                        result.pop("rss_samples")
                        results[name][endpoint].append(result)
                finally:
                    workload.stop(server)
            if server.returncode not in ((0, 143) if name == "kotlin" else (0,)):
                raise RuntimeError(f"{name} did not stop cleanly: {server.returncode}")
            check = validation.verify(database, results[name]["posts"][-1]["status_codes"]["201"])
            check["server_exit_code"] = server.returncode
            if name.startswith("ocaml-"):
                log = (directory / "server.log").read_text()
                domains = re.search(r", (\d+) HTTP domains \+ 1 writer domain", log)
                counts = {int(domain): int(count) for domain, count in re.findall(r"HTTP domain (\d+): (\d+) requests", log)}
                if not domains or len(counts) != int(domains[1]) or min(counts.values()) == 0:
                    raise RuntimeError("not all OCaml HTTP domains served requests")
                check["http_domain_requests"] = counts
            verification[name].append(check)
            (directory / "verification.json").write_text(json.dumps(check, indent=2) + "\n")
    if hashes != {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in artifacts.items()}:
        raise RuntimeError("an artifact changed during the benchmark")
    if sqlite_hash != hashlib.sha256(sqlite_library.read_bytes()).hexdigest():
        raise RuntimeError("OCaml native SQLite changed during the benchmark")
    summary = {name: {endpoint: {
        "rps_median": statistics.median(r["requests_per_second"] for r in runs),
        "rps_min": min(r["requests_per_second"] for r in runs),
        "rps_max": max(r["requests_per_second"] for r in runs),
        "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in runs),
        "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in runs),
        "server_cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in runs),
    } for endpoint, runs in endpoints.items()} for name, endpoints in results.items()}
    record = {"method": "Three rotating-order rounds by default; fresh processes and migrated databases; 50 connections for 10s per endpoint; posts then echo, no warm-up; identical simple email regex; median RPS/p50/CPU and maximum sampled RSS",
              "rounds": args.rounds, "ocaml_http_domains": args.ocaml_domains,
              "artifacts_sha256": hashes, "ocaml_native_sqlite_sha256": sqlite_hash,
              "sqlite_configuration": json.loads((ROOT / "db/sqlite-config.json").read_text()), "oha_version": subprocess.check_output(["pkgx", "oha", "--version"], text=True).strip(),
              "runs": results, "verification": verification, "summary": summary}
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
