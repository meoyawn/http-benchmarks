#!/bin/sh
# Keep opam state local and leave the user's shell/default switch untouched.
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export OPAMROOT="${OCAML_BENCH_OPAMROOT:-$project/.tools/opam}"
export OPAMSWITCH="${OCAML_BENCH_SWITCH:-$project/.tools/flambda}"
export OCAML_BENCH_SQLITE_PREFIX="$project/.tools/sqlite"
if [ -d "$project/.tools/sqlite/lib/pkgconfig" ]; then
  export PKG_CONFIG_PATH="$project/.tools/sqlite/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
fi
if command -v brew >/dev/null 2>&1; then
  # Only the optional Lwt comparison needs libev.
  if libev=$(brew --prefix libev 2>/dev/null) && [ -d "$libev/include" ]; then
    export CPATH="$libev/include${CPATH:+:$CPATH}"
    export LIBRARY_PATH="$libev/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
  fi
fi
if command -v pkgx >/dev/null 2>&1; then
  exec pkgx +ocaml.org@5.5.1 +opam.ocaml.org@2.6.0 +sqlite.org@3.53.4 +pkg-config +gnu.org/gmp /bin/sh -c '
    if [ -d "$OCAML_BENCH_SQLITE_PREFIX/lib/pkgconfig" ]; then
      export PKG_CONFIG_PATH="$OCAML_BENCH_SQLITE_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    fi
    exec opam "$@"
  ' sh "$@"
fi
if command -v brew >/dev/null 2>&1; then
  export PATH="$(brew --prefix ocaml)/bin:$(brew --prefix opam)/bin:$PATH"
  export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:+$PKG_CONFIG_PATH:}$(brew --prefix sqlite)/lib/pkgconfig"
  exec opam "$@"
fi
echo 'Install pkgx (preferred), or brew install ocaml opam sqlite pkgconf.' >&2
exit 1
