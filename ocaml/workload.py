"""Keep Go's load/RSS protocol and add whole-process CPU-time accounting."""
import importlib.util
import json
from pathlib import Path
import subprocess
import time

spec = importlib.util.spec_from_file_location("go_workload", Path(__file__).resolve().parent.parent / "go/measure-http.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PAYLOADS = base.PAYLOADS
wait_ready = base.wait_ready
stop = base.stop


def cpu_seconds(pid):
    raw = subprocess.check_output(["ps", "-o", "time=", "-p", str(pid)], text=True).strip()
    days, clock = raw.split("-", 1) if "-" in raw else ("0", raw)
    return int(days) * 86400 + sum(float(part) * 60 ** i for i, part in enumerate(reversed(clock.split(":"))))


def measure(process, endpoint, socket_path, output, oha, duration="10s"):
    start_cpu = cpu_seconds(process.pid)
    start = time.monotonic()
    result = base.measure(process, endpoint, socket_path, output, oha, duration)
    wall = time.monotonic() - start
    used = cpu_seconds(process.pid) - start_cpu
    result.update(server_cpu_seconds=used, cpu_observation_wall_seconds=wall,
                  server_cpu_percent=used / wall * 100,
                  cpu_method="Whole-process ps CPU time delta / wall time around the load; includes every OCaml domain and runtime thread; 100% = one CPU core")
    output.with_suffix(".metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
