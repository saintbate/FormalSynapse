# Source this file to put the OSS CAD Suite and the project venv on PATH.
#   source scripts/env.sh
# Honors FSYN_TOOLCHAIN_DIR (default: $HOME/.local/opt/oss-cad-suite).
# Compatible with bash and zsh.

FSYN_TOOLCHAIN_DIR="${FSYN_TOOLCHAIN_DIR:-$HOME/.local/opt/oss-cad-suite}"
export FSYN_TOOLCHAIN_DIR

if [ ! -x "$FSYN_TOOLCHAIN_DIR/bin/sby" ]; then
  echo "warning: OSS CAD Suite not found at $FSYN_TOOLCHAIN_DIR; run scripts/install_toolchain.sh" >&2
fi

case ":$PATH:" in
  *":$FSYN_TOOLCHAIN_DIR/bin:"*) ;;
  *) export PATH="$FSYN_TOOLCHAIN_DIR/bin:$PATH" ;;
esac

# Project CLI. The repository path may contain ':' (it does on the original dev machine), which
# makes `.venv/bin` unusable as a PATH entry, so `fsyn` is exposed as a `uv run` wrapper instead
# of activating the virtualenv. Resolve the repo root from this script's location.
if [ -n "${BASH_SOURCE:-}" ]; then
  FSYN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
elif [ -n "${ZSH_VERSION:-}" ]; then
  FSYN_ROOT="$(cd "$(dirname "${(%):-%x}")/.." && pwd)"
else
  FSYN_ROOT="$(pwd)"
fi
export FSYN_ROOT

if [ -f "$FSYN_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$FSYN_ROOT/.env"
  set +a
fi

fsyn() { uv run --project "$FSYN_ROOT" fsyn "$@"; }
