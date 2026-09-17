#!/usr/bin/env python3
"""Fetch verified SQLite source and build the host's bundled FFM library."""

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shlex
import subprocess
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parent
RELEASE = "sqlite-amalgamation-3530400"
SHA256 = "1e71ddf93849c6a6ecf58b827c0692073d2dd7ee40196158068f7b29f422e87d"
# SQLite's recommended options also used by Tailscale. Keep thread safety for
# independent connections, foreign keys, STRICT tables, and the standard WAL policy.
DEFINES = [
    "SQLITE_THREADSAFE=2", "SQLITE_DQS=0", "SQLITE_DEFAULT_MEMSTATUS=0",
    "SQLITE_LIKE_DOESNT_MATCH_BLOBS", "SQLITE_MAX_EXPR_DEPTH=0",
    "SQLITE_OMIT_DEPRECATED", "SQLITE_OMIT_PROGRESS_CALLBACK",
    "SQLITE_OMIT_SHARED_CACHE", "SQLITE_USE_ALLOCA", "SQLITE_OMIT_LOAD_EXTENSION",
    "SQLITE_OMIT_AUTOINIT",
    "HAVE_USLEEP=1",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    tools = PROJECT / ".tools"
    tools.mkdir(exist_ok=True)
    archive = tools / (RELEASE + ".zip")
    if not archive.exists():
        if args.offline:
            parser.error("SQLite source is not cached; run ./gradlew shadowJar once online")
        with urllib.request.urlopen(f"https://sqlite.org/2026/{RELEASE}.zip", timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise RuntimeError("SQLite source checksum mismatch")
        archive.write_bytes(data)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError(f"SQLite source checksum mismatch: {archive}")
    source = tools / RELEASE
    source.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as files:
        for name in ("sqlite3.c", "sqlite3.h", "sqlite3ext.h"):
            data = files.read(RELEASE + "/" + name)
            target = source / name
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
    if args.prepare_only:
        return
    if args.output is None:
        parser.error("--output is required when compiling")
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        parser.error(f"unsupported native build platform: {system}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = shlex.split(os.environ.get("CC", "cc")) + ["-O3", "-DNDEBUG"]
    command += ["-D" + define for define in DEFINES]
    command += (["-D_DARWIN_C_SOURCE=1", "-dynamiclib"] if system == "Darwin" else ["-shared", "-fPIC"])
    command += [str(source / "sqlite3.c"), "-o", str(args.output)]
    if system == "Linux":
        command += ["-pthread", "-ldl", "-lm"]
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
