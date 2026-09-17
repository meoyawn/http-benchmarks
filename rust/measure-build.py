#!/usr/bin/env python3
"""Measure three complete release builds and three real source-edit rebuilds."""
import argparse
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["cargo", "fetch", "--locked"], cwd=PROJECT, check=True)
    subprocess.run(["python3", str(ROOT / "db/prepare-sqlite.py"), "--project", str(PROJECT), "--prepare-only"], check=True)
    config = json.loads((ROOT / "db/sqlite-config.json").read_text())
    cargo = ["cargo", "build", "--release", "--offline", "--locked"]
    env = dict(os.environ)
    env.pop("CARGO_TARGET_DIR", None)

    with tempfile.TemporaryDirectory(prefix="rust-build-") as temporary:
        root = Path(temporary)
        source = root / "rust"
        source.mkdir()
        for name in ("src", ".cargo"):
            shutil.copytree(PROJECT / name, source / name)
        for name in ("Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml"):
            shutil.copy2(PROJECT / name, source / name)
        shutil.copytree(ROOT / "db", root / "db", ignore=shutil.ignore_patterns("*.sqlite*", ".tools"))
        (source / ".tools").mkdir()
        shutil.copy2(PROJECT / ".tools" / (config["release"] + ".zip"), source / ".tools")

        def build(label, target, clean):
            commands = []
            if clean:
                commands.append(["python3", "../db/prepare-sqlite.py", "--project", ".", "--prefix", ".tools/sqlite", "--offline"])
            commands.append(cargo + ["--target-dir", str(target)])
            with (output / f"{label}.log").open("w") as log:
                start = time.perf_counter()
                for command in commands:
                    subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                elapsed = time.perf_counter() - start
            print(f"{label}: {elapsed:.3f}s", flush=True)
            return elapsed

        clean = [build(f"clean-{i}", root / f"target-{i}", True) for i in range(1, 4)]
        target = root / "target-3"
        startup_source = source / "src/lib.rs"
        original = startup_source.read_text()
        marker = '"Listening on {} (SQLite {}, {} HTTP workers + 1 writer)"'
        if original.count(marker) != 1:
            raise RuntimeError("cannot find unique startup log for source-edit rebuild")
        incremental = []
        for index in range(1, 4):
            startup_source.write_text(original.replace(marker, marker[:-1] + f', build timing {index}"'))
            incremental.append(build(f"incremental-{index}", target, False))
    result = {"unit": "seconds", "command": cargo, "rustc": subprocess.check_output(["rustc", "--version"], cwd=PROJECT, text=True).strip(),
              "clean": clean, "incremental": incremental, "clean_median": statistics.median(clean), "incremental_median": statistics.median(incremental),
              "clean_definition": "Fresh Cargo target directory per build; recompile shared SQLite C with -O3 -DNDEBUG, mimalloc C, all Rust dependencies and application; native CPU target, fat LTO, panic=abort, link and strip. Rust standard library is prebuilt.",
              "incremental_definition": "Retain the final clean target directory and native SQLite library; change the startup log string in a disposable source copy; recompile application, repeat fat LTO and relink. Cargo release incremental compilation is disabled.",
              "excludes": "Toolchain/dependency/source downloads, tests and source-copy preparation. No changes to working source or its target directory."}
    (output / "build-times.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
