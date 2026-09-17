"""Locate the common optimized SQLite engine without a runtime toolchain wrapper."""
import json
from pathlib import Path
import platform
import shlex
import subprocess

libdir = Path(subprocess.check_output(["pkg-config", "--variable=libdir", "sqlite3"], text=True).strip())
rpath = libdir
if platform.system() == "Darwin":
    library = (libdir / "libsqlite3.dylib").resolve()
    install_name = subprocess.check_output(["otool", "-D", str(library)], text=True).splitlines()[1].strip()
    if install_name.startswith("@rpath/"):
        suffix = install_name.removeprefix("@rpath/")
        if not str(library).endswith(suffix):
            raise RuntimeError(f"cannot resolve SQLite install name {install_name} against {library}")
        rpath = Path(str(library)[:-len(suffix)])
flags = ["-cclib", f"-Wl,-rpath,{rpath}"]
Path("link_flags.sexp").write_text("(" + " ".join(json.dumps(flag) for flag in flags) + ")\n")
c_flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "sqlite3"], text=True))
Path("c_flags.sexp").write_text("(" + " ".join(json.dumps(flag) for flag in c_flags) + ")\n")
