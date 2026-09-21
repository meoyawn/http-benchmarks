#!/usr/bin/env python3
"""Build the same application with time-manager 0.3.2 for the regression control."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

import build

PROJECT = Path(__file__).resolve().parent


def main():
    copy = PROJECT / '.tools/timer-baseline'
    copy.mkdir(parents=True, exist_ok=True)
    for name in ('app', 'src', 'cbits', 'comparison'):
        shutil.copytree(PROJECT / name, copy / name, dirs_exist_ok=True)
    for name in ('http-benchmark.cabal', 'cabal.project', 'cabal.project.freeze'):
        shutil.copy2(PROJECT / name, copy / name)
    freeze = copy / 'cabal.project.freeze'
    text = freeze.read_text()
    assert 'any.time-manager ==0.4.0' in text
    freeze.write_text(text.replace('any.time-manager ==0.4.0', 'any.time-manager ==0.3.2'))
    build.configure(copy)
    tool = str(PROJECT / 'toolchain.sh')
    flags = ['--flags=-comparisons -dev']
    subprocess.run([tool, 'cabal', 'build', 'exe:haskell-benchmark', *flags], cwd=copy, check=True)
    binary = Path(subprocess.check_output([tool, 'cabal', 'list-bin', 'exe:haskell-benchmark', *flags], cwd=copy, text=True).strip())
    destination = PROJECT / 'bin/haskell-timer-old'
    destination.parent.mkdir(exist_ok=True)
    shutil.copy2(binary, destination)
    subprocess.run(['strip', str(destination)], check=True)
    if platform.system() == 'Darwin':
        subprocess.run(['codesign', '--force', '--sign', '-', str(destination)], check=True)
    source = [p for name in ('app', 'src', 'cbits') for p in (copy / name).rglob('*') if p.is_file()]
    source += [copy / name for name in ('http-benchmark.cabal', 'cabal.project', 'cabal.project.freeze')]
    record = {'time-manager': '0.3.2', 'binary_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
              'source_sha256': {str(p.relative_to(copy)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source}}
    destination.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
    print(destination)


if __name__ == '__main__':
    main()
