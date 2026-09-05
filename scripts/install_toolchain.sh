#!/usr/bin/env bash
# Install the YosysHQ OSS CAD Suite (yosys, sby, z3, bitwuzla, boolector, yices, verilator,
# gtkwave, yosys-slang) into a colon-free directory outside the repository.
#
#   scripts/install_toolchain.sh                 # auto-detect platform, latest release
#   PLATFORM=linux-x64 scripts/install_toolchain.sh
#   RELEASE=2026-08-31 scripts/install_toolchain.sh
#   FSYN_TOOLCHAIN_DIR=/opt/oss-cad-suite scripts/install_toolchain.sh
#
# The install location defaults to $HOME/.local/opt/oss-cad-suite. It deliberately lives outside
# the repository: this workspace path contains a ':' which would corrupt $PATH entries.
set -euo pipefail

FSYN_TOOLCHAIN_DIR="${FSYN_TOOLCHAIN_DIR:-$HOME/.local/opt/oss-cad-suite}"
RELEASE="${RELEASE:-latest}"

detect_platform() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Darwin) case "$arch" in arm64) echo darwin-arm64 ;; x86_64) echo darwin-x64 ;; esac ;;
    Linux)  case "$arch" in aarch64|arm64) echo linux-arm64 ;; x86_64) echo linux-x64 ;; esac ;;
  esac
}

PLATFORM="${PLATFORM:-$(detect_platform)}"
if [[ -z "$PLATFORM" ]]; then
  echo "error: could not detect platform; set PLATFORM=linux-x64|linux-arm64|darwin-x64|darwin-arm64" >&2
  exit 1
fi

case ":$FSYN_TOOLCHAIN_DIR:" in
  *":"*":"*":"*) echo "error: FSYN_TOOLCHAIN_DIR must not contain ':' ($FSYN_TOOLCHAIN_DIR)" >&2; exit 1 ;;
esac

api="https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases"
if [[ "$RELEASE" == "latest" ]]; then api="$api/latest"; else api="$api/tags/$RELEASE"; fi

echo "==> Resolving OSS CAD Suite release ($RELEASE) for $PLATFORM"
url="$(curl -fsSL "$api" | python3 -c '
import json, sys
platform = sys.argv[1]
assets = json.load(sys.stdin)["assets"]
hits = [a["browser_download_url"] for a in assets if platform in a["name"] and a["name"].endswith(".tgz")]
if not hits:
    sys.exit("no asset for platform " + platform)
print(hits[0])
' "$PLATFORM")"
echo "    $url"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "==> Downloading"
curl -fL --progress-bar -o "$tmp/suite.tgz" "$url"

echo "==> Extracting to $FSYN_TOOLCHAIN_DIR"
rm -rf "$FSYN_TOOLCHAIN_DIR"
mkdir -p "$(dirname "$FSYN_TOOLCHAIN_DIR")"
tar -xzf "$tmp/suite.tgz" -C "$tmp"
mv "$tmp/oss-cad-suite" "$FSYN_TOOLCHAIN_DIR"

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "==> Removing macOS quarantine attributes"
  xattr -r -d com.apple.quarantine "$FSYN_TOOLCHAIN_DIR" 2>/dev/null || true
fi

export PATH="$FSYN_TOOLCHAIN_DIR/bin:$PATH"
echo "==> Installed $(cat "$FSYN_TOOLCHAIN_DIR/VERSION")"
yosys -V
sby --help 2>&1 | head -n 1
z3 --version
printf 'bitwuzla %s\n' "$(bitwuzla --version)"
printf 'boolector %s\n' "$(boolector --version | head -n 1)"
verilator --version
echo
echo "Add the tools to your shell with:   source scripts/env.sh"
