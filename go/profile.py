#!/usr/bin/env python3
"""Profile the Go write workload in a disposable source copy, without shipping fgprof."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent
spec = importlib.util.spec_from_file_location("benchmark", ROOT / "rust/measure-http.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)

PROFILER = '''package main

import (
    "errors"
    "os"
    "runtime/pprof"
    "github.com/felixge/fgprof"
)

func startBenchmarkProfile() func() {
    file, err := os.Create(os.Getenv("BENCH_PROFILE_FILE"))
    if err != nil { panic(err) }
    var stop func() error
    if os.Getenv("BENCH_PROFILE_CPU") == "1" {
        if err := pprof.StartCPUProfile(file); err != nil {
            _ = file.Close()
            panic(err)
        }
        stop = func() error { pprof.StopCPUProfile(); return nil }
    } else {
        stop = fgprof.Start(file, fgprof.FormatPprof)
    }
    return func() {
        if err := errors.Join(stop(), file.Close()); err != nil { panic(err) }
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profiler", choices=("fgprof", "cpu"), default="fgprof")
    parser.add_argument("--gomaxprocs", type=int, default=2)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--loadgen", default=str(Path(__file__).resolve().parent.parent / "loadgen/bombard"))
    args = parser.parse_args()
    if args.gomaxprocs < 1 or args.seconds < 1:
        parser.error("positive gomaxprocs and seconds required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    sources = [path for path in PROJECT.glob("*.go") if not path.name.endswith("_test.go")]
    sources += [PROJECT / "go.mod", PROJECT / "go.sum"]
    hashes = {path.name: benchmark.digest(path) for path in sources}
    for path in sources:
        shutil.copy2(path, source / path.name)
    main_source = source / "main.go"
    original = main_source.read_text()
    marker = "func main() {"
    if original.count(marker) != 1:
        raise RuntimeError("cannot find unique main function for profiling")
    main_source.write_text(original.replace(marker, marker + "\n\tdefer startBenchmarkProfile()()"))
    (source / "profile.go").write_text(PROFILER)
    binary = source / "bench-profile"
    env = dict(os.environ, CGO_CFLAGS="-O3 -DNDEBUG", GOMAXPROCS=str(args.gomaxprocs),
               BENCH_PROFILE_FILE=str(output / "profile.pprof"), BENCH_PROFILE_CPU=str(int(args.profiler == "cpu")))
    builds = [["go", "get", "github.com/felixge/fgprof@v0.9.5"],
              ["go", "build", "-trimpath", "-o", str(binary), "."]]
    with (output / "build.log").open("w") as log:
        for command in builds:
            subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    database = output / "bench.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
    socket = Path(f"/tmp/go-profile-{os.getpid()}.sock")
    if os.path.lexists(socket):
        raise RuntimeError(f"socket exists: {socket}")
    command = [str(binary), "-db", str(database), "-socket", str(socket)]
    record = {"build_commands": builds, "server_command": command, "profiler": args.profiler,
              "GOMAXPROCS": args.gomaxprocs, "source_sha256": hashes, "binary_sha256": benchmark.digest(binary),
              "purpose": "Diagnostic only: profiling adds overhead; exclude throughput from ranking."}
    (output / "commands.json").write_text(json.dumps(record, indent=2) + "\n")
    with (output / "server.log").open("w") as log:
        server = subprocess.Popen(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            benchmark.workload.wait_ready(server, socket)
            result = benchmark.workload.measure(server, "posts", socket, output / "posts",
                                                shlex.split(args.loadgen), f"{args.seconds}s")
        finally:
            benchmark.workload.stop(server)
    if server.returncode:
        raise RuntimeError(f"server failed; see {output / 'server.log'}")
    verification = benchmark.verify(database, result["status_codes"]["201"])
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    with (output / "top.txt").open("w") as log:
        subprocess.run(["go", "tool", "pprof", "-top", str(binary), str(output / "profile.pprof")],
                       cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f"Profile, stack summary and verified database saved in {output}")


if __name__ == "__main__":
    main()
