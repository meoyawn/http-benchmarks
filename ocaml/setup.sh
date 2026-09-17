#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"

# pkgx supplies the bootstrap OCaml, opam, SQLite, pkg-config and GMP.
# pkgconf is needed by opam's macOS system dependency probe.
if command -v pkgx >/dev/null 2>&1; then
  if [ "$(uname -s)" = Darwin ]; then
    if ! command -v pkgconf >/dev/null 2>&1; then brew install pkgconf; fi
    # pkgx dylibs use install names relative to the package cache.
    export LDFLAGS="${LDFLAGS:-} -Wl,-rpath,${PKGX_DIR:-$HOME/.pkgx}"
  fi
elif command -v brew >/dev/null 2>&1; then
  brew install ocaml opam sqlite pkgconf gmp
else
  echo 'Install pkgx (preferred), or Homebrew, then rerun ./setup.sh.' >&2
  exit 1
fi

python3 ../db/prepare-sqlite.py --project . --prefix .tools/sqlite

if [ ! -f .tools/opam/config ]; then
  ./toolchain.sh init --bare --no-setup --disable-shell-hook --disable-sandboxing -y
fi
if [ ! -d .tools/flambda/_opam ]; then
  ./toolchain.sh switch create "$PWD/.tools/flambda" \
    --packages=ocaml-variants.5.5.1+options,ocaml-option-flambda --jobs=8 -y
fi
./toolchain.sh install --deps-only --locked --assume-depexts -y ./http-benchmark.opam
# sqlite3 records native search paths when installed. Reconfigure an existing
# switch too, so upgrading from pkgx's generic engine cannot silently keep it.
fingerprint=$(python3 -c 'import hashlib,pathlib; print(str(pathlib.Path(".tools/sqlite").resolve()) + ":" + hashlib.sha256(pathlib.Path("../db/sqlite-config.json").read_bytes()).hexdigest())')
if [ "$(cat .tools/sqlite-binding-config 2>/dev/null || true)" != "$fingerprint" ]; then
  ./toolchain.sh reinstall --assume-depexts -y sqlite3
  printf '%s\n' "$fingerprint" > .tools/sqlite-binding-config
fi
