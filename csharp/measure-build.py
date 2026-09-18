#!/usr/bin/env python3
"""Measure clean publishes and public-API debug rebuilds in a disposable source copy."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--mode', nargs='+', choices=['jit', 'aot', 'r2r'], default=['jit', 'aot'])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    modes = {
        'jit': ['--self-contained', 'false', '-p:PublishSingleFile=true'],
        'r2r': ['--self-contained', 'false', '-p:PublishReadyToRun=true'],
        'aot': ['-p:PublishAot=true', '-p:IlcOptimizationPreference=Speed'],
    }
    modes = {name: modes[name] for name in args.mode}
    with tempfile.TemporaryDirectory(prefix='csharp-build-') as temporary:
        root = Path(temporary)
        source = root / 'csharp'
        shutil.copytree(PROJECT, source, ignore=shutil.ignore_patterns('bin', 'obj', '.tools', '.task', '__pycache__'))
        shutil.copytree(ROOT / 'db', root / 'db', ignore=shutil.ignore_patterns('*.sqlite*', '.tools', '__pycache__'))
        (source / '.tools').mkdir()
        config = json.loads((ROOT / 'db/sqlite-config.json').read_text())
        shutil.copy2(PROJECT / '.tools' / (config['release'] + '.zip'), source / '.tools')
        # Prime dependency and compiler package caches; exclude downloads from timing.
        for mode, flags in modes.items():
            subprocess.run(['pkgx', 'dotnet', 'restore', '-r', 'osx-arm64'] + [f for f in flags if f.startswith('-p:')], cwd=source, check=True, stdout=subprocess.DEVNULL)
        def timed(label, commands):
            with (output / f'{label}.log').open('w') as log:
                start = time.perf_counter()
                for command in commands:
                    subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
                elapsed = time.perf_counter() - start
            print(f'{label}: {elapsed:.3f}s', flush=True)
            return elapsed
        clean = {mode: [] for mode in modes}
        for index in range(3):
            names = list(modes)
            for mode in names[index:] + names[:index]:
                for name in ['bin', 'obj', '.tools/sqlite']:
                    shutil.rmtree(source / name, ignore_errors=True)
                commands = [
                    ['python3', '../db/prepare-sqlite.py', '--project', '.', '--prefix', '.tools/sqlite', '--extra-source', 'sqlite-config.c', '--offline'],
                    ['pkgx', 'dotnet', 'publish', '-c', 'Release', '-r', 'osx-arm64', '--ignore-failed-sources'] + modes[mode],
                ]
                clean[mode].append(timed(f'{mode}-{index + 1}', commands))
        build = ['pkgx', 'dotnet', 'build', '-c', 'Debug', '--no-restore']
        # Restore without RID after the native publish; this setup is untimed.
        subprocess.run(['pkgx', 'dotnet', 'restore'], cwd=source, check=True, stdout=subprocess.DEVNULL)
        timed('debug-warmup', [build])
        control = timed('debug-no-change', [build])
        files = {p: p.read_text() for p in source.glob('*.cs')}
        hashes = []
        edits = []
        for index in range(3):
            patches = []
            for path, text in files.items():
                edited = text.replace('NewPost', f'NewPostBuild{index + 1}')
                patches.extend(difflib.unified_diff(text.splitlines(True), edited.splitlines(True), fromfile=path.name, tofile=path.name))
                path.write_text(edited)
            (output / f'debug-edit-{index + 1}.patch').write_text(''.join(patches))
            elapsed = timed(f'debug-edit-{index + 1}', [build])
            digest = hashlib.sha256((source / 'bin/Debug/net10.0/csharp.dll').read_bytes()).hexdigest()
            assert digest not in hashes
            hashes.append(digest)
            edits.append(elapsed)
        record = {'clean_seconds': clean, 'clean_medians': {mode: statistics.median(values) for mode, values in clean.items()},
                  'debug_seconds': edits, 'debug_median': statistics.median(edits), 'debug_no_change': control, 'debug_hashes': hashes,
                  'dotnet': subprocess.check_output(['pkgx', 'dotnet', '--info'], cwd=source, text=True),
                  'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(PROJECT.glob('*.cs*'))},
                  'definition': 'Clean: remove bin/obj/native SQLite library, compile shared SQLite plus ABI shim, publish application/dependencies (including framework-dependent single-file bundling with native SQLite for JIT, crossgen2 for R2R, or native compile/link/strip for AOT); installed SDK/runtime/NuGet caches retained. Debug: rename public NewPost and consumers across files, compile and link with warm caches. Includes pkgx/build startup; excludes source copying, edits, downloads and validation.'}
        (output / 'summary.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2))

if __name__ == '__main__':
    main()
