#!/usr/bin/env python3
"""Alternate fresh Kotlin and Go processes; report medians and maximum sampled RSS."""

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = {language: {endpoint: [] for endpoint in ("posts", "echo")} for language in ("kotlin", "go")}
    commands = []
    for i in range(args.rounds):
        for language in (("go", "kotlin") if i % 2 == 0 else ("kotlin", "go")):
            directory = output / f"{language}-{i+1}"
            script = ROOT / ("go" if language == "go" else "kotlin") / "measure-http.py"
            command = [sys.executable, str(script), str(directory), "--socket", f"/tmp/{language}-compare-{os.getpid()}.sock"]
            subprocess.run(command, check=True)
            commands.append(command)
            for endpoint in ("posts", "echo"):
                result = json.loads((directory / (endpoint + ".metrics.json")).read_text())
                results[language][endpoint].append({
                    "rps": result["requests_per_second"],
                    "p50_ms": result["p50_seconds"] * 1000,
                    "peak_rss_mib": result["peak_sampled_rss_mib"],
                })
    summary = {"commands": commands, "runs": results, "summary": {}}
    for language, workloads in results.items():
        summary["summary"][language] = {}
        for endpoint, runs in workloads.items():
            row = {
                "rps_median": statistics.median(r["rps"] for r in runs),
                "rps_min": min(r["rps"] for r in runs),
                "rps_max": max(r["rps"] for r in runs),
                "p50_ms_median": statistics.median(r["p50_ms"] for r in runs),
                "peak_rss_mib_max": max(r["peak_rss_mib"] for r in runs),
            }
            summary["summary"][language][endpoint] = row
            print(f"{language} {endpoint}: {row['rps_median']:.0f} RPS, {row['p50_ms_median']:.3f} ms, {row['peak_rss_mib_max']:.1f} MiB peak RSS", flush=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
