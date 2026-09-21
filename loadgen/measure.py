#!/usr/bin/env python3
"""Rotate server configurations through randomized valid JSON."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import sqlite3
import statistics
import subprocess

import workload
import host

ROOT = workload.ROOT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(runs):
    return {name: {endpoint: {
        "rps_median": statistics.median(r["requests_per_second"] for r in samples),
        "rps_min": min(r["requests_per_second"] for r in samples),
        "rps_max": max(r["requests_per_second"] for r in samples),
        "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in samples),
        "p95_ms_median": statistics.median(r["p95_seconds"] * 1000 for r in samples),
        "p99_ms_median": statistics.median(r["p99_seconds"] * 1000 for r in samples),
        "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in samples),
        "server_cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in samples),
        "client_cpu_percent_median": statistics.median(r["client_cpu_percent"] for r in samples),
        "client_peak_rss_mib_max": max(r["client_peak_sampled_rss_mib"] for r in samples),
    } for endpoint, samples in endpoints.items() if samples} for name, endpoints in runs.items()}


DEFAULT_CONFIGS = ("rust-1", "go-2", "ocaml-1", "rust-3", "go-4", "ocaml-4")
CONFIG_NAMES = (*DEFAULT_CONFIGS, "csharp-jit", "csharp-aot", "kotlin-jvm", "zig", "zig-4",
                "haskell-1", "haskell-2", "haskell-4", "haskell-8", "haskell-8-hybrid", "bun")


def configurations(names):
    configs = {
        "bun": (ROOT / "bun/dist/server", [], {}),
        "rust-1": (ROOT / "rust/target/release/rust-benchmark", ["-workers", "1"], {}),
        "go-2": (ROOT / "go/bench", [], {"GOMAXPROCS": "2"}),
        "ocaml-1": (ROOT / "ocaml/_build/default/bin/bench.exe", ["-domains", "1"], {}),
        "rust-3": (ROOT / "rust/target/release/rust-benchmark", ["-workers", "3"], {}),
        "go-4": (ROOT / "go/bench", [], {"GOMAXPROCS": "4"}),
        "ocaml-4": (ROOT / "ocaml/_build/default/bin/bench.exe", ["-domains", "4"], {}),
        "csharp-jit": (ROOT / "csharp/bin/jit/csharp", [], {}),
        "csharp-aot": (ROOT / "csharp/bin/aot/csharp", [], {}),
        "zig": (ROOT / "zig/zig-out/bin/zig", ["-workers", "1"], {}),
        "zig-4": (ROOT / "zig/zig-out/bin/zig", ["-workers", "4"], {}),
    }
    for capabilities in (1, 2, 4, 8):
        configs[f"haskell-{capabilities}"] = (ROOT / "haskell/bin/haskell-benchmark",
            ["+RTS", f"-N{capabilities}", "-A8m", "-RTS"], {})
    configs["haskell-8-hybrid"] = (ROOT / "haskell/bin/haskell-benchmark",
        ["-fork", "pinned", "-sqlite-step", "hybrid", "+RTS", "-N8", "-A8m", "-RTS"], {})
    if "csharp-jit" in names:
        runtimes = subprocess.check_output(["pkgx", "dotnet", "--list-runtimes"], text=True)
        runtime_path = re.search(r"Microsoft.NETCore.App .* \[(.*)\]", runtimes)[1]
        configs["csharp-jit"][2]["DOTNET_ROOT"] = str(Path(runtime_path).parent.parent)
    if "kotlin-jvm" in names:
        java = Path(os.environ["JAVA_HOME"]) / "bin/java" if "JAVA_HOME" in os.environ else Path(shutil.which("java") or "java")
        native = ROOT / "kotlin/build/jit/native"
        configs["kotlin-jvm"] = (java, [
            "-server", "-XX:+PerfDisableSharedMem", "--enable-native-access=ALL-UNNAMED",
            f"-Djava.library.path={native}", f"-Dsqlite.library={native}/libsqlite3.{'dylib' if platform.system() == 'Darwin' else 'so'}",
            "-XX:AOTMode=on", f"-XX:AOTCache={ROOT}/kotlin/build/jit/kotlin-bench.aot",
        ], {})
    return {name: configs[name] for name in names}


def launch_configuration(name, config, directory):
    binary, flags, env = config
    database = directory / "bench.sqlite"
    socket = Path(f"/tmp/random-json-{os.getpid()}.sock")
    cwd = ROOT
    if name == "kotlin-jvm":
        command = [str(binary), *flags, f"-Ddb.path={database}", f"-Dhttp.socket={socket}",
                   "-jar", str(ROOT / "kotlin/build/libs/kotlin-1.0-all.jar")]
    else:
        command = [str(binary), "-db", str(database), "-socket", str(socket), *flags]
    if os.path.lexists(socket):
        raise RuntimeError(f"socket already exists: {socket}")
    with sqlite3.connect(database) as db:
        db.executescript((ROOT / "db/migrations/001_init.up.sql").read_text())
    return command, env, cwd, database, socket


def artifact_key(path):
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--client-cpus", type=int, default=5)
    parser.add_argument("--client-shards", type=int, default=1)
    parser.add_argument("--client-processes", type=int, default=5)
    parser.add_argument("--config", nargs="+", choices=CONFIG_NAMES, default=DEFAULT_CONFIGS)
    parser.add_argument("--endpoints", nargs="+", choices=workload.PAYLOADS, default=list(workload.PAYLOADS))
    args = parser.parse_args()
    configs = configurations(args.config)
    if args.rounds < 1 or any(not binary.is_file() for binary, _, _ in configs.values()):
        parser.error("positive rounds and built binaries required; see loadgen/README.md")
    path = workload.prepare()  # Corpus creation is outside every measured interval.
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tracked = sorted(set(binary for binary, _, _ in configs.values()) | {workload.BINARY, path})
    for name in configs:
        if name.startswith("csharp-"):
            tracked += [p for p in configs[name][0].parent.iterdir() if p.is_file() and p.suffix != ".pdb"]
        elif name.startswith("zig"):
            tracked += list((ROOT / "zig/.tools/sqlite/lib").glob("libsqlite3.*"))
        elif name.startswith("haskell-"):
            tracked += list((ROOT / "haskell/.tools/sqlite/lib").glob("libsqlite3.*"))
        elif name == "kotlin-jvm":
            tracked += [ROOT / "kotlin/build/libs/kotlin-1.0-all.jar", ROOT / "kotlin/build/jit/kotlin-bench.aot"]
            tracked += list((ROOT / "kotlin/build/jit/native").glob("*"))
    hashes = {artifact_key(p): digest(p) for p in tracked}
    runs = {name: {endpoint: [] for endpoint in args.endpoints} for name in configs}
    checks = {name: [] for name in configs}
    order = list(configs)
    record = {"method": "Rotating order; fresh processes/databases; 50 concurrent requests; posts then echo; no HTTP warm-up; outstanding requests drain; medians except maximum RSS; 100% CPU = one core",
              "platform": platform.platform(), "host": host.inspect(), "rounds": args.rounds, "duration": args.duration,
              "client_cpus": args.client_cpus, "client_shards": args.client_shards, "client_processes": args.client_processes,
              "corpus_seed": workload.SEED, "corpus_entries": workload.CORPUS_SIZE,
              "load_generator": subprocess.check_output([str(workload.BINARY), "-version"], text=True).strip(),
              "artifacts_sha256": hashes, "configurations": {k: {"binary": artifact_key(b), "args": flags, "env": env} for k, (b, flags, env) in configs.items()},
              "sqlite_configuration": json.loads((ROOT / "db/sqlite-config.json").read_text()),
              "sqlite_configuration_exceptions": {"bun": "bun:sqlite default engine (system SQLite on macOS); matching connection pragmas, no custom engine or global allocator configuration"} if "bun" in configs else {},
              "bun_source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sorted([*(ROOT / "bun/src").glob("*.ts"), ROOT / "bun/package.json", ROOT / "bun/bun.lock"])} if "bun" in configs else {},
              "haskell_source_sha256": {str(p.relative_to(ROOT)): digest(p) for pattern in
                  ("src/*.hs", "app/*.hs", "cbits/*.c", "*.cabal", "cabal.project", "cabal.project.freeze", "toolchain.sh", "build.py")
                  for p in sorted((ROOT / "haskell").glob(pattern))} if any(name.startswith("haskell-") for name in configs) else {},
              "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sorted(ROOT.glob("loadgen/*")) if p.suffix in (".go", ".py", ".mod", ".sum")},
              "zig_source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sorted([*(ROOT / "zig/src").glob("*.zig"), *(ROOT / "zig").glob("build.zig*"), ROOT / "zig/zig.py"])}
                                     if any(name.startswith("zig") for name in configs) else {},
              "runs": runs, "verification": checks, "order": []}
    if "bun" in configs:
        record["bun_runtime"] = json.loads(subprocess.check_output([
            "bun", "-e", 'import { Database } from "bun:sqlite"; const db = new Database(":memory:"); '
            'console.log(JSON.stringify({bun: Bun.version, sqlite: db.query("SELECT sqlite_version() AS version").get().version, '
            'sqlite_compile_options: db.query("PRAGMA compile_options").values().flat()})); db.close();'
        ], text=True, cwd=ROOT / "bun"))
    for index in range(args.rounds):
        shift = index % len(order)
        round_order = order[shift:] + order[:shift]
        record["order"].append(round_order)
        for name in round_order:
            binary, flags, env = configs[name]
            directory = output / f"{name}-{index+1}"
            directory.mkdir()
            command, env, cwd, database, socket = launch_configuration(name, configs[name], directory)
            (directory / "command.json").write_text(json.dumps({"command": command, "env": env, "cwd": str(cwd)}, indent=2) + "\n")
            (directory / "processes.txt").write_text(subprocess.check_output(["ps", "-Ao", "pid,ppid,pcpu,comm"], text=True))
            print(f"{name} round {index+1}/{args.rounds}", flush=True)
            with (directory / "server.log").open("w") as log:
                server = subprocess.Popen(command, cwd=cwd, env=dict(os.environ, **env), stdout=log, stderr=subprocess.STDOUT)
                try:
                    workload.wait_ready(server, socket)
                    for endpoint in args.endpoints:
                        result = workload.measure(server, endpoint, socket, directory / endpoint, duration=args.duration,
                                                  cpus=args.client_cpus, shards=args.client_shards, processes=args.client_processes, seed=workload.SEED+index)
                        result.pop("rss_samples")
                        runs[name][endpoint].append(result)
                finally:
                    workload.stop(server)
                    if name.startswith(("zig", "haskell-", "bun")) and socket.exists():
                        raise RuntimeError(f"{name} left its socket behind")
            accepted_exits = (0, 128 + signal.SIGTERM, -signal.SIGTERM) if name == "kotlin-jvm" else (0,)
            if server.returncode not in accepted_exits:
                raise RuntimeError(f"{name} failed to stop cleanly: {server.returncode}")
            completed = runs[name].get("posts", [])
            check = workload.verify(database, completed[-1]["requests"] if completed else 0)
            if name.startswith("ocaml"):
                domains = dict(re.findall(r"HTTP domain (\d+): (\d+) requests", (directory / "server.log").read_text()))
                if len(domains) != int(name.split("-")[1]) or any(int(v) == 0 for v in domains.values()):
                    raise RuntimeError(f"not all OCaml HTTP domains served requests: {domains}")
                check["http_domain_requests"] = domains
            checks[name].append(check)
            (directory / "verification.json").write_text(json.dumps(check, indent=2) + "\n")
            record["summary"] = aggregate(runs)
            (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    if hashes != {artifact_key(p): digest(p) for p in tracked}:
        raise RuntimeError("measured artifact or corpus changed during sweep")
    record["complete"] = True
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record["summary"], indent=2))


if __name__ == "__main__":
    main()
