#!/usr/bin/env python3
"""Launch-to-bind of the built executable; pkgx/build/migration excluded."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import statistics

PROJECT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('startup', PROJECT.parent / 'measure-startup.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--capabilities', type=int, nargs='+', default=[2, 4])
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()
    if args.rounds < 1 or any(n < 1 for n in args.capabilities):
        parser.error('rounds and capabilities must be positive')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = PROJECT / 'bin/haskell-benchmark'
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    runs = {str(n): [] for n in args.capabilities}
    for index in range(args.rounds):
        order = args.capabilities if index % 2 == 0 else list(reversed(args.capabilities))
        for n in order:
            directory = output / f'n{n}-{index + 1}'
            directory.mkdir()
            database = directory / 'bench.sqlite'
            with sqlite3.connect(database) as db:
                db.executescript((PROJECT.parent / 'db/migrations/001_init.up.sql').read_text())
            sock = Path(f'/tmp/haskell-start-{os.getpid()}-{n}.sock')
            command = [str(binary), '-db', str(database), '-socket', str(sock), '+RTS', f'-N{n}', '-A8m', '-RTS']
            result = startup.measure(command, f'Listening on {sock}'.encode(), sock, directory, PROJECT)
            assert not sock.exists()
            runs[str(n)].append(result)
            print(f'N{n} run {index + 1}: {result["launch_to_listening_ms"]:.3f} ms', flush=True)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
    record = {'method': 'Fresh migrated databases/processes, alternating order; no cache flush; first post-bind log; untimed echo readiness check',
              'sha256': digest, 'runs': runs,
              'summary': {n: {'median_ms': statistics.median(r['launch_to_listening_ms'] for r in samples),
                              'min_ms': min(r['launch_to_listening_ms'] for r in samples),
                              'max_ms': max(r['launch_to_listening_ms'] for r in samples)} for n, samples in runs.items()}}
    (output / 'summary.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record['summary'], indent=2))


if __name__ == '__main__':
    main()
