#!/usr/bin/env python3
"""Measure C# variants using the repository's shared load and validation protocol."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import sqlite3
import statistics
import subprocess

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

shared = load(ROOT / 'rust/measure-http.py', 'shared')
startup = load(ROOT / 'measure-startup.py', 'startup')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--command', default='pkgx +dotnet bin/jit/csharp')
    parser.add_argument('--mode', nargs='+', choices=['jit', 'aot', 'r2r'], help='Measure published apphosts in rotating order; excludes pkgx startup')
    parser.add_argument('--variant', nargs=2, action='append', metavar=('NAME', 'ARGS'))
    parser.add_argument('--case', nargs=3, action='append', metavar=('NAME', 'COMMAND', 'ARGS'), help='Compare explicit candidate commands')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--duration', default='10s')
    parser.add_argument('--startup', action='store_true')
    parser.add_argument('--fresh-bundle-extraction', action='store_true', help='Use a fresh native-library extraction directory for each launch')
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    variants = dict(args.variant or [('default', '')])
    if len(variants) != len(args.variant or [('default', '')]) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', name) for name in variants):
        parser.error('variant names must be unique safe directory names')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    command = shlex.split(args.command)
    if args.case:
        if args.mode or args.variant:
            parser.error('--case cannot be combined with --mode or --variant')
        commands = {name: shlex.split(cmd) for name, cmd, _ in args.case}
        variants = {name: options for name, _, options in args.case}
        if len(variants) != len(args.case) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', name) for name in variants):
            parser.error('case names must be unique safe directory names')
    elif args.mode:
        runtimes = subprocess.check_output(['pkgx', 'dotnet', '--list-runtimes'], text=True)
        runtime_path = re.search(r'Microsoft.NETCore.App .* \[(.*)\]', runtimes)[1]
        os.environ['DOTNET_ROOT'] = str(Path(runtime_path).parent.parent)
        commands = {f'{mode}-{name}': [str(PROJECT / 'bin' / mode / 'csharp')]
                    for mode in args.mode for name in variants}
        variants = {f'{mode}-{name}': options for mode in args.mode for name, options in variants.items()}
    else:
        commands = {name: command for name in variants}
    artifacts = {}
    for cmd in commands.values():
        artifact = Path(cmd[0])
        if artifact.is_file():
            for path in artifact.parent.iterdir():
                if path.is_file() and path.suffix not in ('.pdb', '.json'):
                    artifacts[str(path)] = shared.digest(path)
    source_hashes = {p.name: shared.digest(p) for p in sorted(PROJECT.glob('*.cs'))}
    results = {name: [] for name in variants}
    for index in range(args.rounds):
        names = list(variants)
        shift = index % len(names)
        for name in names[shift:] + names[:shift]:
            directory = output / f'{name}-{index + 1}'
            directory.mkdir()
            database = directory / 'bench.sqlite'
            with sqlite3.connect(database) as db:
                db.executescript((ROOT / 'db/migrations/001_init.up.sql').read_text())
            socket = Path(f'/tmp/csharp-bench-{os.getpid()}.sock')
            if os.path.lexists(socket):
                raise RuntimeError(f'socket already exists: {socket}')
            options = shlex.split(variants[name])
            environment = [f'DOTNET_BUNDLE_EXTRACT_BASE_DIR={directory / "extracted"}'] if args.fresh_bundle_extraction else []
            while options and '=' in options[0] and not options[0].startswith('-'):
                environment.append(options.pop(0))
            run = (['env'] + environment if environment else []) + commands[name] + ['-db', str(database), '-socket', str(socket)] + options
            print(f'{name} round {index + 1}', flush=True)
            if args.startup:
                result = startup.measure(run, f'Listening on {socket}'.encode(), socket, directory, PROJECT)
                print(result, flush=True)
            else:
                (directory / 'command.json').write_text(json.dumps(run, indent=2) + '\n')
                result = {}
                with (directory / 'server.log').open('w') as log:
                    server = subprocess.Popen(run, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
                    try:
                        shared.workload.wait_ready(server, socket)
                        for endpoint in shared.workload.PAYLOADS:
                            sample = shared.workload.measure(server, endpoint, socket, directory / endpoint, [str(ROOT / 'loadgen/bombard')], args.duration)
                            sample.pop('rss_samples')
                            result[endpoint] = sample
                    finally:
                        shared.workload.stop(server)
                if server.returncode != 0:
                    raise RuntimeError((directory / 'server.log').read_text())
                result['verification'] = shared.verify(database, result['posts']['status_codes']['201'])
            results[name].append(result)
    if args.startup:
        summary = {name: {'median_ms': statistics.median(r['launch_to_listening_ms'] for r in runs)} for name, runs in results.items()}
    else:
        summary = {name: {endpoint: {
            'rps_median': statistics.median(r[endpoint]['requests_per_second'] for r in runs),
            'rps_min': min(r[endpoint]['requests_per_second'] for r in runs),
            'rps_max': max(r[endpoint]['requests_per_second'] for r in runs),
            'p50_ms': statistics.median(r[endpoint]['p50_seconds'] * 1000 for r in runs),
            'peak_rss_mib': max(r[endpoint]['peak_sampled_rss_mib'] for r in runs),
            'cpu_percent': statistics.median(r[endpoint]['server_cpu_percent'] for r in runs),
        } for endpoint in shared.workload.PAYLOADS} for name, runs in results.items()}
    if any(shared.digest(Path(path)) != digest for path, digest in artifacts.items()):
        raise RuntimeError('a measured artifact changed during the run')
    record = {'platform': platform.platform(), 'rounds': args.rounds, 'duration': args.duration,
              'commands': commands, 'variants': variants, 'runs': results, 'summary': summary,
              'artifacts_sha256': artifacts,
              'artifact_bytes': {path: Path(path).stat().st_size for path in artifacts},
              'dotnet_root': os.environ.get('DOTNET_ROOT'),
              'fresh_bundle_extraction': args.fresh_bundle_extraction,
              'dotnet': subprocess.check_output(['pkgx', 'dotnet', '--info'], cwd=PROJECT, text=True),
              'load_generator': subprocess.check_output([str(ROOT / 'loadgen/bombard'), '-version'], text=True).strip(),
              'source_sha256': source_hashes}
    (output / 'summary.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(summary, indent=2), flush=True)

if __name__ == '__main__':
    main()
