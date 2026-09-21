#!/bin/sh
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export CABAL_DIR="$project/.tools/cabal"
exec pkgx +ghc@9.14.1 +cabal@3.14.2.0 +pkg-config -- "$@"
