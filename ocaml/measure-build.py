#!/usr/bin/env python3
"""Measure application clean builds and source-edit rebuilds with warm dependencies."""

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Resolve pkgx/opam once, outside the timed interval. No tools or dependencies
    # are downloaded or installed by this script.
    encoded = subprocess.check_output([str(PROJECT / "toolchain.sh"), "exec", "--", "python3", "-c",
        "import os,json; print(json.dumps(dict(os.environ)))"], text=True)
    env = dict(json.loads(encoded), DUNE_CACHE="disabled")
    command = ["dune", "build", "--profile", "release", "bin/bench.exe"]

    def build(label, copy):
        with (output / f"{label}.log").open("w") as log:
            start = time.perf_counter()
            subprocess.run(command, cwd=copy, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            elapsed = time.perf_counter() - start
        print(f"{label}: {elapsed:.3f}s", flush=True)
        return elapsed

    with tempfile.TemporaryDirectory(prefix="ocaml-build-") as temporary:
        copy = Path(temporary)
        for name in ("lib", "http", "bin", "config"):
            shutil.copytree(PROJECT / name, copy / name)
        for name in ("dune-project", "dune"):
            shutil.copy2(PROJECT / name, copy / name)
        clean = []
        for i in range(3):
            subprocess.run(["dune", "clean"], cwd=copy, env=env, check=True)
            clean.append(build(f"clean-{i + 1}", copy))
        source = copy / "http/eio_server.ml"
        original = source.read_text()
        marker = 'Listening on %s (SQLite %s)'
        if original.count(marker) != 1:
            raise RuntimeError("startup log marker must occur exactly once")
        incremental = []
        for i in range(3):
            source.write_text(original.replace(marker, marker + f", build timing {i + 1}"))
            incremental.append(build(f"incremental-{i + 1}", copy))
    result = {
        "unit": "seconds", "command": command,
        "ocaml_version": subprocess.check_output(["ocamlopt", "-version"], env=env, text=True).strip(),
        "dune_version": subprocess.check_output(["dune", "--version"], env=env, text=True).strip(),
        "clean": clean, "incremental": incremental,
        "clean_median": statistics.median(clean), "incremental_median": statistics.median(incremental),
        "clean_definition": "Remove _build; compile all application modules and link native executable; precompiled opam dependencies and compiler retained; Dune shared cache disabled",
        "incremental_definition": "Warm _build; change listening log string in disposable source copy, recompile changed module and relink",
        "excludes": "Toolchain/dependency installation, migration, tests, source copying, pkgx/opam environment resolution",
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
