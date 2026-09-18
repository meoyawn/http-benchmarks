#!/usr/bin/env python3
"""Three clean releases and real public-type-edit debug rebuilds in a disposable copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="zig-build-") as temporary:
        root = Path(temporary)
        source = root / "zig"
        source.mkdir()
        for name in ("build.zig", "build.zig.zon", "zig.py"):
            shutil.copy2(PROJECT / name, source / name)
        shutil.copytree(PROJECT / "src", source / "src")
        shutil.copytree(ROOT / "testdata", root / "testdata")
        shutil.copytree(ROOT / "db", root / "db", ignore=shutil.ignore_patterns("*.sqlite*", ".tools"))
        # Warm downloaded sources are outside the timed compiler caches.
        shutil.copytree(PROJECT / "zig-pkg", source / "zig-pkg", symlinks=True)
        (source / ".tools").mkdir()
        config = json.loads((ROOT / "db/sqlite-config.json").read_text())
        shutil.copy2(PROJECT / ".tools" / (config["release"] + ".zip"), source / ".tools")
        sqlite = ["python3", "../db/prepare-sqlite.py", "--project", ".", "--prefix", ".tools/sqlite", "--offline"]
        def build(label, mode, cache, native=False):
            commands = ([sqlite] if native else []) + [["python3", "zig.py", "build", f"-Doptimize={mode}", "--cache-dir", str(cache / "local"), "--global-cache-dir", str(cache / "global")]]
            with (output / f"{label}.log").open("w") as log:
                start = time.perf_counter()
                for command in commands: subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
                seconds = time.perf_counter() - start
            print(f"{label}: {seconds:.3f}s", flush=True)
            return {"seconds": seconds, "commands": commands, "sha256": digest(source / "zig-out/bin/zig")}
        clean = [build(f"clean-{i}", "ReleaseFast", root / f"cache-{i}", True) for i in range(1, 4)]
        debug_cache = root / "debug-cache"
        warm = build("debug-warmup", "Debug", debug_cache)
        control = build("debug-no-change", "Debug", debug_cache)
        originals = {p: p.read_text() for p in (source / "src").glob("*.zig")}
        edits = []
        previous = control["sha256"]
        for i in range(1, 4):
            for path, content in originals.items(): path.write_text(content.replace("NewPost", f"NewPostBuild{i}"))
            (output / f"edit-{i}.txt").write_text(f"Rename public NewPost to NewPostBuild{i} and every consumer in src/*.zig\n")
            result = build(f"debug-edit-{i}", "Debug", debug_cache)
            if result["sha256"] == previous: raise RuntimeError("source edit did not change binary")
            previous = result["sha256"]
            edits.append(result)
        report = {"zig": subprocess.check_output(["zig", "version"], text=True).strip(), "clean": clean,
                  "debug_warmup": warm, "debug_no_change": control, "debug_edits": edits,
                  "clean_median_seconds": statistics.median(r["seconds"] for r in clean),
                  "debug_median_seconds": statistics.median(r["seconds"] for r in edits),
                  "clean_definition": "Fresh local and global compiler caches each run; compile shared SQLite C, C bindings, the selected HTTP/runtime and JSON dependencies, Zig application, link and strip. Installed compiler and downloaded dependency sources stay warm.",
                  "debug_definition": "Warm local/global compiler caches and native SQLite; rename NewPost and all consumers in a disposable copy, full Debug build and link; warmup/no-change control excluded."}
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k.endswith("seconds")}, indent=2))


if __name__ == "__main__": main()
