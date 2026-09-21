#!/usr/bin/env python3
"""macOS native stack samples and GHC GC statistics; diagnostic, not table data."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'loadgen'))
import workload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--capabilities', type=int, nargs='+', default=[2, 4])
    parser.add_argument('--endpoints', nargs='+', default=['posts', 'echo'], choices=['posts', 'echo'])
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--fork', default='pinned', choices=['default', 'pinned'])
    parser.add_argument('--sqlite-step', default='no-callback', choices=['safe', 'hybrid', 'no-callback'])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = args.binary or Path(subprocess.check_output([str(ROOT / 'haskell/toolchain.sh'), 'cabal', 'list-bin', 'exe:haskell-benchmark'], cwd=ROOT / 'haskell', text=True).strip())
    workload.prepare()
    for n in args.capabilities:
        for endpoint in args.endpoints:
            directory = output / f'n{n}-{endpoint}'
            directory.mkdir()
            database = directory / 'bench.sqlite'
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / 'db/migrations/001_init.up.sql').read_text())
            sock = Path(f'/tmp/haskell-profile-{os.getpid()}.sock')
            command = [str(binary), '-db', str(database), '-socket', str(sock), '-fork', args.fork,
                       '-sqlite-step', args.sqlite_step,
                       '+RTS', f'-N{n}', '-A8m', '-s', '-RTS']
            (directory / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
            with (directory / 'server.log').open('w') as log, (directory / 'sample.log').open('w') as sample_log:
                server = subprocess.Popen(command, stdout=log, stderr=log)
                try:
                    workload.wait_ready(server, sock)
                    sampler = subprocess.Popen(['sample', str(server.pid), '7', '-file', str(directory / 'stacks.txt')], stdout=sample_log, stderr=sample_log)
                    result = workload.measure(server, endpoint, sock, directory / 'load', duration='10s')
                    sampler.wait(timeout=20)
                    if sampler.returncode != 0:
                        raise RuntimeError('native sampling failed; see sample.log')
                    with (directory / 'memory.txt').open('w') as memory:
                        subprocess.run(['vmmap', '-summary', str(server.pid)], stdout=memory, stderr=memory, check=True)
                    result.pop('rss_samples')
                    (directory / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
                finally:
                    workload.stop(server)
            if server.returncode != 0 or sock.exists():
                raise RuntimeError('profiled server did not shut down cleanly')
            workload.verify(database, result['requests'] if endpoint == 'posts' else 0)
            print(f'Profile written: {directory}', flush=True)


if __name__ == '__main__':
    main()
