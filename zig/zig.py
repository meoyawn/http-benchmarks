#!/usr/bin/env python3
"""Run installed Zig 0.16, selecting an arm64-compatible macOS SDK if needed."""
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys


def main():
    env = dict(os.environ)
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        sdk = Path(subprocess.check_output(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip())

        def supports_arm64(path):
            stub = path / "usr/lib/libSystem.tbd"
            return stub.is_file() and "arm64-macos" in stub.read_text().split("install-name:", 1)[0]

        if not supports_arm64(sdk):
            candidates = sorted(p for p in Path("/Library/Developer/CommandLineTools/SDKs").glob("MacOSX*.sdk") if supports_arm64(p))
            if not candidates:
                raise SystemExit("Zig needs a macOS SDK with arm64-macos linker stubs; install a compatible SDK.")
            sdk = candidates[-1].resolve()
            directory = Path(__file__).resolve().parent / ".tools/sdk-bin"
            directory.mkdir(parents=True, exist_ok=True)
            shim = directory / "xcrun"
            shim.write_text("#!/bin/sh\n"
                            "if [ \"$1\" = '--sdk' ] && [ \"$2\" = 'macosx' ] && [ \"$3\" = '--show-sdk-path' ]; then\n"
                            f"    printf '%s\\n' {shlex.quote(str(sdk))}\n    exit 0\nfi\n"
                            'exec /usr/bin/xcrun "$@"\n')
            shim.chmod(0o755)
            env["PATH"] = str(directory) + os.pathsep + env["PATH"]
            print(f"Zig SDK: {sdk}", flush=True)
    version = subprocess.check_output(["zig", "version"], text=True).strip()
    if version != "0.16.0":
        raise SystemExit(f"Expected installed Zig 0.16.0, got {version}")
    os.execvpe("zig", ["zig", *sys.argv[1:]], env)


if __name__ == "__main__":
    main()
