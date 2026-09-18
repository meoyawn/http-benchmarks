"""Share the repository-wide randomized workload implementation."""
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("random_workload", Path(__file__).resolve().parent.parent / "loadgen/workload.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PAYLOADS = base.PAYLOADS
DEFAULT_COMMAND = base.DEFAULT_COMMAND
wait_ready = base.wait_ready
stop = base.stop
measure = base.measure
verify = base.verify
cpu_seconds = base.cpu_seconds
prepare = base.prepare
