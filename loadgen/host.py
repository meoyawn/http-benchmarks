"""Record host topology and actual affinity capability, without claiming pinning."""
import ctypes as c
import platform
import subprocess


def inspect():
    result = {"platform": platform.platform(), "transport": "HTTP/1.1 over Unix domain sockets",
              "isolation": "same physical host; no enforced CPU, LLC or NUMA separation",
              "nic_queues": "not applicable to Unix domain sockets"}
    if platform.system() == "Darwin":
        names = ["hw.physicalcpu", "hw.logicalcpu", "hw.nperflevels", "hw.memsize",
                 "hw.perflevel0.physicalcpu", "hw.perflevel0.logicalcpu", "hw.perflevel0.cpusperl2", "hw.perflevel0.l2cachesize",
                 "hw.perflevel1.physicalcpu", "hw.perflevel1.logicalcpu", "hw.perflevel1.cpusperl2", "hw.perflevel1.l2cachesize"]
        raw = subprocess.check_output(["sysctl", *names], text=True)
        result["sysctl"] = {k: int(v) for k, v in (line.split(": ", 1) for line in raw.splitlines())}
        result["smt"] = result["sysctl"]["hw.logicalcpu"] != result["sysctl"]["hw.physicalcpu"]
        lib = c.CDLL("/usr/lib/libSystem.B.dylib")
        lib.mach_thread_self.restype = c.c_uint32
        lib.thread_policy_set.argtypes = [c.c_uint32, c.c_int, c.POINTER(c.c_int), c.c_uint32]
        lib.mach_error_string.argtypes = [c.c_int]
        lib.mach_error_string.restype = c.c_char_p
        thread = lib.mach_thread_self()
        policy = c.c_int(1)
        code = lib.thread_policy_set(thread, 4, c.byref(policy), 1)
        result["affinity_probe"] = {"policy": "THREAD_AFFINITY_POLICY (cache hint, not hard binding)",
                                    "return_code": code, "message": lib.mach_error_string(code).decode()}
        if code == 0:
            policy.value = 0
            lib.thread_policy_set(thread, 4, c.byref(policy), 1)
        result["thermal"] = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True).stdout
    return result
