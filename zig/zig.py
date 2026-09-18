#!/usr/bin/env python3
"""Run pkgx Zig, selecting an installed arm64-compatible macOS SDK if needed."""
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
                raise SystemExit("Zig 0.15.2 needs a macOS SDK with arm64-macos linker stubs; install a compatible SDK.")
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
    os.execvpe("pkgx", ["pkgx", "zig@0.15.2", *sys.argv[1:]], env)


if __name__ == "__main__":
    main()
