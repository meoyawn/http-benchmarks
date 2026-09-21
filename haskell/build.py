#!/usr/bin/env python3
"""Build using pkgx; setup/downloads are separate from measured builds."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parent


def configure(project=PROJECT):
    sql = PROJECT / '.tools/sqlite'
    pkgx = Path(os.environ.get('PKGX_DIR', str(Path.home() / '.pkgx')))
    (project / 'cabal.project.local').write_text(
        f'extra-include-dirs: {sql}/include\nextra-lib-dirs: {sql}/lib\n'
        f'package *\n  ghc-options: -optl-Wl,-rpath,{sql}/lib -optl-Wl,-rpath,{pkgx}\n'
        f'  hsc2hs-options: --lflag=-Wl,-rpath,{sql}/lib --lflag=-Wl,-rpath,{pkgx}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--comparisons', action='store_true')
    args = parser.parse_args()
    tool = str(PROJECT / 'toolchain.sh')
    if args.setup:
        config = PROJECT / '.tools/cabal/config'
        config.parent.mkdir(parents=True, exist_ok=True)
        if not config.exists():
            subprocess.run([tool, 'cabal', 'user-config', 'init'], check=True)
        config.write_text(config.read_text().replace('http://hackage.haskell.org/', 'https://hackage-content.haskell.org/'))
        subprocess.run([tool, 'cabal', 'update'], cwd=PROJECT, check=True)
        subprocess.run(['python3', str(PROJECT.parent / 'db/prepare-sqlite.py'), '--project', str(PROJECT),
                        '--prefix', str(PROJECT / '.tools/sqlite')], check=True)
    configure()
    flags = ['--builddir=dist-debug', '--disable-optimization'] if args.debug else []
    flags += ['--flags=' + ('comparisons' if args.comparisons else '-comparisons') + (' dev' if args.debug else ' -dev')]
    subprocess.run([tool, 'cabal', 'build', 'exe:haskell-benchmark', *flags], cwd=PROJECT, check=True)
    binary = Path(subprocess.check_output([tool, 'cabal', 'list-bin', 'exe:haskell-benchmark', *flags], cwd=PROJECT, text=True).strip())
    destination = PROJECT / 'bin' / ('haskell-comparison' if args.comparisons else 'haskell-benchmark-debug' if args.debug else 'haskell-benchmark')
    destination.parent.mkdir(exist_ok=True)
    shutil.copy2(binary, destination)
    if not args.debug:
        subprocess.run(['strip', str(destination)], check=True)
        if platform.system() == 'Darwin':
            subprocess.run(['codesign', '--force', '--sign', '-', str(destination)], check=True)
    print(destination)


if __name__ == '__main__':
    main()
