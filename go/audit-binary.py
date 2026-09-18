#!/usr/bin/env python3
"""Verify the optimized symbol companion has one HTTP/JSON/SQLite implementation."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", type=Path, help="optimized executable built without stripping")
    parser.add_argument("release", type=Path, help="same build with -ldflags='-s -w'")
    args = parser.parse_args()
    symbols = subprocess.check_output(["go", "tool", "nm", str(args.symbols)], text=True)
    servers = [name for name, entry in {
        "net/http": "net/http.(*Server).Serve",
        "Hertz": "github.com/cloudwego/hertz/pkg/protocol/http1.Server.Serve",
        "fasthttp": "github.com/valyala/fasthttp.(*Server).Serve",
    }.items() if any(line.endswith(" T " + entry) for line in symbols.splitlines())]
    codecs = [name for name, entry in {
        "encoding/json (v1 compatibility API + v2 engine)": "encoding/json/v2.",
        "goccy": "github.com/goccy/go-json/internal/",
        "Sonic": "github.com/bytedance/sonic/internal/",
        "GJSON": "github.com/tidwall/gjson.",
        "fastjson": "github.com/valyala/fastjson.",
        "jsoniter": "github.com/json-iterator/go.",
        "segmentio": "github.com/segmentio/encoding/json.",
    }.items() if entry in symbols]
    engines = [line for line in symbols.splitlines() if line.endswith(" T _sqlite3_open_v2") or line.endswith(" T sqlite3_open_v2")]
    info = subprocess.check_output(["go", "version", "-m", str(args.release)], text=True)
    assert len(servers) == 1, servers
    assert len(codecs) == 1, codecs
    assert len(engines) == 1, engines
    assert "github.com/tailscale/sqlite" in info
    assert "github.com/tailscale/sqlite/cgosqlite" in symbols
    native = subprocess.check_output(["otool", "-L", str(args.release)], text=True) if platform.system() == "Darwin" else ""
    assert "libsqlite" not in native.lower(), native
    report = {"http_server": servers, "json": codecs, "sqlite": "Tailscale cgosqlite, one static engine",
              "release_bytes": args.release.stat().st_size,
              "release_mib": args.release.stat().st_size / (1 << 20),
              "release_sha256": hashlib.sha256(args.release.read_bytes()).hexdigest(),
              "symbols_sha256": hashlib.sha256(args.symbols.read_bytes()).hexdigest(),
              "build_info": info, "native_links": native}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
