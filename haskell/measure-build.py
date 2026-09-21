#!/usr/bin/env python3
"""Three clean application release builds and three public-type debug edits."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time

import build

PROJECT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = json.loads(subprocess.check_output([str(PROJECT / 'toolchain.sh'), 'python3', '-c', 'import os,json; print(json.dumps(dict(os.environ)))'], text=True))
    records = []
    with tempfile.TemporaryDirectory(prefix='haskell-build-') as temp:
        copy = Path(temp)
        for name in ('app', 'src', 'cbits', 'comparison'):
            shutil.copytree(PROJECT / name, copy / name)
        for name in ('http-benchmark.cabal', 'cabal.project', 'cabal.project.freeze'):
            shutil.copy2(PROJECT / name, copy / name)
        build.configure(copy)

        def run(label, mode, directory):
            command = ['cabal', 'build', 'exe:haskell-benchmark', '--offline', f'--builddir={directory}',
                       '--flags=-comparisons dev' if mode == 'debug' else '--flags=-comparisons -dev',
                       '--disable-optimization' if mode == 'debug' else '--enable-optimization=2']
            with (output / f'{label}.log').open('w') as log:
                start = time.perf_counter()
                subprocess.run(command, cwd=copy, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                binaries = [p for p in (copy / directory).rglob('haskell-benchmark') if p.is_file()]
                assert len(binaries) == 1, binaries
                binary = binaries[0]
                if mode == 'release':
                    subprocess.run(['strip', str(binary)], stdout=log, stderr=log, check=True)
                    if platform.system() == 'Darwin':
                        subprocess.run(['codesign', '--force', '--sign', '-', str(binary)], stdout=log, stderr=log, check=True)
                elapsed = time.perf_counter() - start
            record = {'label': label, 'seconds': elapsed, 'command': command,
                      'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'bytes': binary.stat().st_size}
            records.append(record)
            print(f'{label}: {elapsed:.3f}s', flush=True)
            return record

        for i in range(3):
            run(f'clean-{i + 1}', 'release', f'release-{i + 1}')
        previous = run('debug-warmup', 'debug', 'debug')
        control = run('debug-no-change', 'debug', 'debug')
        assert control['sha256'] == previous['sha256']
        originals = {p: p.read_text() for p in [*(copy / 'src').glob('*.hs'), *(copy / 'app').glob('*.hs')]}
        for i in range(3):
            patches = []
            for path, source in originals.items():
                updated = source.replace('NewPost', f'NewPostEdit{i + 1}')
                path.write_text(updated)
                patches.extend(difflib.unified_diff(source.splitlines(True), updated.splitlines(True), fromfile=str(path.relative_to(copy)), tofile=str(path.relative_to(copy))))
            (output / f'debug-edit-{i + 1}.patch').write_text(''.join(patches))
            current = run(f'debug-edit-{i + 1}', 'debug', 'debug')
            assert current['sha256'] != previous['sha256'], 'source edit did not change executable'
            previous = current
    result = {'runs': records,
              'clean_median_seconds': statistics.median(r['seconds'] for r in records if r['label'].startswith('clean-')),
              'debug_median_seconds': statistics.median(r['seconds'] for r in records if r['label'].startswith('debug-edit-')),
              'clean_definition': 'Fresh Cabal build directory; compile application Haskell and C shim, link, strip and sign. Compiler, optimized dependencies and shared SQLite stay cached.',
              'debug_definition': 'Cabal -O0 incremental build after renaming public NewPost type/constructor and all consumers across modules. Warm dependencies; no-change control excluded; executable hash must change.',
              'excludes': 'pkgx environment resolution, compiler/dependency downloads/builds, SQLite engine build, copying/edits, tests and startup'}
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'}, indent=2))


if __name__ == '__main__':
    main()
