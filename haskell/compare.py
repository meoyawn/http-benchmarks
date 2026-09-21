#!/usr/bin/env python3
"""Short diagnostic sweeps using the shared randomized load and correctness gates."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'loadgen'))
import measure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['libraries', 'scaling', 'scheduling', 'migration', 'sqlite', 'heap', 'timer', 'writer'])
    parser.add_argument('output')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--duration', default='3s')
    args = parser.parse_args()
    configs = {}

    def add(name, n, *flags):
        step = [] if '-sqlite-step' in flags else ['-sqlite-step', 'safe']
        configs[name] = (ROOT / 'haskell/bin/haskell-comparison', [*step, *flags, '+RTS', f'-N{n}', '-A8m', '-RTS'], {})

    if args.suite == 'libraries':
        add('haskell-warp-jsonifier', 2, '-fork', 'default', '-writer', 'bound', '-codec', 'jsonifier')
        add('haskell-warp-aeson', 2, '-fork', 'default', '-writer', 'bound', '-codec', 'aeson')
        add('haskell-snap-jsonifier', 2, '-http', 'snap', '-writer', 'bound', '-codec', 'jsonifier')
    elif args.suite == 'scaling':
        for http in ['warp', 'snap']:
            for n in [1, 2, 4, 8]:
                add(f'haskell-{http}-{n}', n, '-http', http, '-fork', 'default', '-writer', 'bound', '-codec', 'jsonifier')
    elif args.suite == 'scheduling':
        for writer in ['bound', 'unbound']:
            add(f'haskell-default-{writer}-2', 2, '-fork', 'default', '-writer', writer, '-codec', 'jsonifier')
            for n in [2, 4, 8]:
                add(f'haskell-pinned-{writer}-{n}', n, '-fork', 'pinned', '-writer', writer, '-codec', 'jsonifier')
    elif args.suite == 'migration':
        for n in [1, 2, 4, 8]:
            for migration in ['default', 'disabled']:
                name = f'haskell-migration-{migration}-{n}'
                add(name, n, '-fork', 'pinned', '-writer', 'bound', '-codec', 'jsonifier')
                if migration == 'disabled':
                    configs[name][2]['GHCRTS'] = '-qm'
    elif args.suite == 'sqlite':
        for n in [1, 2, 4, 8]:
            for step in ['safe', 'hybrid', 'no-callback']:
                add(f'haskell-step-{step}-{n}', n, '-fork', 'pinned', '-writer', 'bound', '-sqlite-step', step, '-codec', 'jsonifier')
    elif args.suite == 'heap':
        for name, rts, codec in [('default', [], 'jsonifier'), ('g1', ['-G1'], 'jsonifier'),
                                 ('a2m', ['-A2m'], 'jsonifier'), ('aeson', [], 'aeson')]:
            key = f'haskell-heap-{name}'
            add(key, 1, '-sqlite-step', 'no-callback', '-codec', codec)
            _, flags, env = configs[key]
            configs[key] = (ROOT / 'haskell/bin/haskell-benchmark', [*flags[:-1], *rts, '-RTS'], env)
    elif args.suite == 'writer':
        for n in [2, 4]:
            for writer in ['bound', 'unbound']:
                for step in ['safe', 'hybrid', 'no-callback']:
                    key = f'haskell-writer-{writer}-{step}-{n}'
                    add(key, n, '-fork', 'pinned', '-writer', writer, '-sqlite-step', step)
                    _, flags, env = configs[key]
                    configs[key] = (ROOT / 'haskell/bin/haskell-benchmark', flags, env)
    elif args.suite == 'timer':
        for n in [1, 2, 4, 8]:
            for variant in ['old', 'new']:
                key = f'haskell-timer-{variant}-{n}'
                add(key, n, '-fork', 'pinned', '-sqlite-step', 'hybrid')
                _, flags, env = configs[key]
                binary = 'haskell-timer-old' if variant == 'old' else 'haskell-benchmark'
                configs[key] = (ROOT / 'haskell/bin' / binary, flags, env)
    measure.CONFIG_NAMES = tuple(configs)
    measure.configurations = lambda names: {name: configs[name] for name in names}
    # Preflight every experimental configuration before starting timed loads.
    spec = importlib.util.spec_from_file_location('preflight', ROOT / 'loadgen/test-servers.py')
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    sys.argv = [str(ROOT / 'loadgen/test-servers.py'), '--config', *configs]
    preflight.main()
    sys.argv = [str(ROOT / 'loadgen/measure.py'), args.output, '--config', *configs,
                '--rounds', str(args.rounds), '--duration', args.duration]
    measure.main()


if __name__ == '__main__':
    main()
