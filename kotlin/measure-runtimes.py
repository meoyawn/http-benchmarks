#!/usr/bin/env python3
"""Remeasure the complete JVM/GraalVM rows using alternating fresh processes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--java", default=str(Path(os.environ["JAVA_HOME"]) / "bin/java") if "JAVA_HOME" in os.environ else "java")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--startup-rounds", type=int, default=7)
    parser.add_argument("--build-results", type=Path, required=True,
                        help="parent of jvm-build, native-build and debug-build measurement directories")
    args = parser.parse_args()
    if min(args.rounds, args.startup_rounds) < 1:
        parser.error("round counts must be positive")
    jar = PROJECT / "build/libs/kotlin-1.0-all.jar"
    cache = PROJECT / "build/jit/kotlin-bench.aot"
    binary = PROJECT / "build/native/nativeCompile/kotlin-bench"
    libraries = {"jvm": PROJECT / "build/jit/native", "graalvm": binary.parent / "native"}
    artifacts = {"jvm": [jar, cache, *sorted(libraries["jvm"].iterdir())],
                 "graalvm": [binary, *sorted(binary.parent.glob("*.dylib")),
                             *sorted(binary.parent.glob("*.so")), *sorted(libraries["graalvm"].iterdir())]}
    hashes = {mode: {str(p): {"sha256": digest(p), "bytes": p.stat().st_size} for p in paths}
              for mode, paths in artifacts.items()}
    build = args.build_results.resolve()
    builds = {"jvm": json.loads((build / "jvm-build/build-times.json").read_text()),
              "graalvm": json.loads((build / "native-build/build-times.json").read_text())}
    debug = json.loads((build / "debug-build/kotlin/summary.json").read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runs = {mode: {endpoint: [] for endpoint in ("posts", "echo")} for mode in artifacts}
    checks = {mode: [] for mode in artifacts}
    sqlite_name = "libsqlite3.dylib" if platform.system() == "Darwin" else "libsqlite3.so"
    for mode in artifacts:
        command = [sys.executable, str(PROJECT / "verify-executable.py"),
                   f"--runtime-arg=-Djava.library.path={libraries[mode]}",
                   f"--runtime-arg=-Dsqlite.library={libraries[mode] / sqlite_name}"]
        command += (["--java", args.java, "--jar", str(jar), "--runtime-arg=-XX:AOTMode=on",
                     f"--runtime-arg=-XX:AOTCache={cache}"] if mode == "jvm" else ["--native", str(binary)])
        with (output / f"{mode}-verification.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    for index in range(args.rounds):
        for mode in (["jvm", "graalvm"] if index % 2 == 0 else ["graalvm", "jvm"]):
            directory = output / f"{mode}-{index + 1}"
            directory.mkdir()
            (directory / "processes-before.txt").write_text(subprocess.check_output(
                ["ps", "-A", "-o", "pid,pcpu,comm", "-r"], text=True))
            command = [sys.executable, str(PROJECT / "measure-http.py"), str(directory),
                       "--socket", f"/tmp/kotlin-{mode}-{os.getpid()}.sock",
                       f"--jvm-arg=-Djava.library.path={libraries[mode]}",
                       f"--jvm-arg=-Dsqlite.library={libraries[mode] / sqlite_name}"]
            if mode == "jvm":
                command += ["--java", args.java, "--jar", str(jar), "--jvm-arg=-XX:AOTMode=on",
                            f"--jvm-arg=-XX:AOTCache={cache}"]
            else:
                command += ["--native", str(binary)]
            print(f"{mode}, HTTP round {index + 1}", flush=True)
            subprocess.run(command, check=True)
            for endpoint in runs[mode]:
                metric = json.loads((directory / f"{endpoint}.metrics.json").read_text())
                metric.pop("rss_samples")
                runs[mode][endpoint].append(metric)
            checks[mode].append(json.loads((directory / "verification.json").read_text()))
    starts = {}
    for mode in artifacts:
        command = [sys.executable, str(PROJECT / "measure-startup.py"), str(output / f"{mode}-startup"),
                   "--rounds", str(args.startup_rounds)]
        command += (["--java", args.java] if mode == "jvm" else ["--native", str(binary)])
        subprocess.run(command, check=True)
        starts[mode] = json.loads((output / f"{mode}-startup/summary.json").read_text())
    for mode, paths in artifacts.items():
        assert all(digest(p) == hashes[mode][str(p)]["sha256"] for p in paths), "artifact changed"
    summary = {}
    for mode in artifacts:
        summary[mode] = {endpoint: {
            "rps_median": statistics.median(r["requests_per_second"] for r in values),
            "rps_min": min(r["requests_per_second"] for r in values),
            "rps_max": max(r["requests_per_second"] for r in values),
            "p50_ms_median": statistics.median(r["p50_seconds"] * 1000 for r in values),
            "peak_rss_mib_max": max(r["peak_sampled_rss_mib"] for r in values),
            "cpu_percent_median": statistics.median(r["server_cpu_percent"] for r in values),
        } for endpoint, values in runs[mode].items()}
        startup_mode = "cached" if mode == "jvm" else "libraries"
        summary[mode].update(
            startup_ms=starts[mode]["summary"][startup_mode]["listening_ms"]["median"],
            clean_build_seconds=builds[mode]["clean_median"],
            warm_debug_seconds=debug["incremental_median"],
            debug_runtime="JVM development classes (both release modes)",
            artifact_mib=(jar if mode == "jvm" else binary).stat().st_size / 2**20,
            distribution_mib=sum(p.stat().st_size for p in artifacts[mode]) / 2**20,
        )
    record = {"platform": platform.platform(),
              "method": "Alternating fresh JVM/GraalVM processes and migrated DBs; posts then echo, "
                        "10 seconds each, 50 connections, no HTTP warm-up; medians except maximum "
                        "RSS sampled every 100ms; CPU 100% is one core. All release build steps included.",
              "artifacts": hashes, "runs": runs, "verification": checks,
              "builds": builds, "debug_build": debug, "startup": starts, "summary": summary,
              "source_sha256": {str(p.relative_to(PROJECT)): digest(p)
                                for p in sorted([*PROJECT.joinpath("src/main").rglob("*"),
                                                 PROJECT / "build.gradle", PROJECT / "Taskfile.yaml"])
                                if p.is_file()}}
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
