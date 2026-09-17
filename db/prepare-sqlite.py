#!/usr/bin/env python3
"""Build the common optimized SQLite engine for Kotlin and OCaml."""

import argparse
import hashlib
import json
import shutil
import os
from pathlib import Path
import platform
import shlex
import subprocess
import urllib.request
import zipfile

CONFIG = json.loads((Path(__file__).resolve().parent / "sqlite-config.json").read_text())
RELEASE = CONFIG["release"]
SHA256 = CONFIG["sha256"]
DEFINES = CONFIG["defines"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, help="Also install headers and pkg-config metadata")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    tools = args.project.resolve() / ".tools"
    tools.mkdir(exist_ok=True)
    archive = tools / (RELEASE + ".zip")
    if not archive.exists():
        if args.offline:
            parser.error("SQLite source is not cached; rerun without --offline once")
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
    if args.prefix:
        args.prefix = args.prefix.resolve()
        args.output = args.prefix / "lib" / ("libsqlite3.dylib" if platform.system() == "Darwin" else "libsqlite3.so")
    if args.output is None:
        parser.error("--output or --prefix is required when compiling")
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        parser.error(f"unsupported native build platform: {system}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = shlex.split(os.environ.get("CC", "cc")) + CONFIG["cflags"]
    command += ["-D" + define for define in DEFINES]
    command += (["-dynamiclib", "-Wl,-install_name,@rpath/libsqlite3.dylib"] if system == "Darwin" else ["-shared", "-fPIC"])
    command += [str(source / "sqlite3.c"), "-o", str(args.output)]
    if system == "Linux":
        command += ["-pthread", "-ldl", "-lm"]
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)
    if args.prefix:
        (args.prefix / "include").mkdir(parents=True, exist_ok=True)
        for name in ("sqlite3.h", "sqlite3ext.h"):
            shutil.copy2(source / name, args.prefix / "include" / name)
        pkgconfig = args.prefix / "lib/pkgconfig"
        pkgconfig.mkdir(exist_ok=True)
        (pkgconfig / "sqlite3.pc").write_text(
            f"prefix={args.prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n"
            f"Name: SQLite\nDescription: Shared benchmark SQLite configuration\nVersion: {CONFIG['version']}\n"
            "Libs: -L${libdir} -lsqlite3\nCflags: -I${includedir}\n")
    args.output.with_suffix(args.output.suffix + ".build.json").write_text(
        json.dumps({"config": CONFIG, "command": command,
                    "source_sha256": hashlib.sha256((source / "sqlite3.c").read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
