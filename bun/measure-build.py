#!/usr/bin/env python3
"""Three clean standalone builds; development runs TypeScript without a build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    records = []
    with tempfile.TemporaryDirectory(prefix="bun-build-") as temporary:
        copy = Path(temporary)
        shutil.copytree(PROJECT / "src", copy / "src")
        for name in ("package.json", "bun.lock", "tsconfig.json"):
            shutil.copy2(PROJECT / name, copy / name)
        (copy / "node_modules").symlink_to(PROJECT / "node_modules", target_is_directory=True)

        def run(label):
            command = ["bun", "run", "build"]
            with (output / f"{label}.log").open("w") as log:
                start = time.perf_counter()
                subprocess.run(command, cwd=copy, stdout=log, stderr=subprocess.STDOUT, check=True)
                seconds = time.perf_counter() - start
            artifact = copy / "dist/server"
            record = {"label": label, "seconds": seconds, "command": command,
                      "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "bytes": artifact.stat().st_size}
            records.append(record)
            print(f"{label}: {seconds:.6f}s", flush=True)
            return record

        for index in range(3):
            if (copy / "dist").exists():
                shutil.rmtree(copy / "dist")
            run(f"clean-{index + 1}")
    result = {
        "bun_version": subprocess.check_output(["bun", "--version"], text=True).strip(),
        "runs": records,
        "clean_median_seconds": statistics.median(r["seconds"] for r in records if r["label"].startswith("clean-")),
        "debug_rebuild_seconds": 0,
        "clean_definition": "Remove dist; bun run build bundles/minifies the application and Valibot into a standalone executable containing the installed Bun runtime. No TypeScript type checking or Bun/SQLite compilation.",
        "debug_definition": "No development build step: bun --watch src/server.ts runs TypeScript directly. Zero denotes that no separate rebuild is required.",
        "excludes": "Dependency/runtime downloads, dependency install, source copying/edits, tests, type checking and server startup",
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    main()
