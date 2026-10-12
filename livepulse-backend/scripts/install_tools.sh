#!/usr/bin/env bash
# Download static binaries (ffmpeg, ffprobe, mc) into project/.tools/.
#
# Why static binaries instead of brew:
#   macOS 13 is Homebrew Tier 3 — bottle compilation routinely fails on the
#   ffmpeg dependency tree (openssl/x264/svt-av1). evermeet.cx ships a
#   self-contained universal2 ffmpeg that just works. mc is a single Go
#   binary from min.io.
#
# Idempotent: skips downloads when the binary already exists and reports
# its version. To force re-download, pass --force.

set -euo pipefail

# Resolve project root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLS_DIR="$PROJECT_ROOT/.tools"

FORCE=false
[[ "${1:-}" == "--force" ]] && FORCE=true

mkdir -p "$TOOLS_DIR"

# --- Colors (only when stdout is a TTY) ---
if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_BLUE=$'\033[0;34m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RESET=""
fi
info() { printf "%s[tools]%s %s\n" "$C_BLUE" "$C_RESET" "$1"; }
ok()   { printf "%s[tools]%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
skip() { printf "%s[tools]%s %s\n" "$C_YELLOW" "$C_RESET" "$1"; }

# Detect arch — evermeet ships universal2 (works on Intel + Apple Silicon),
# but mc has separate amd64 and arm64 builds.
ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  x86_64)  MC_ARCH="amd64" ;;
  arm64)   MC_ARCH="arm64" ;;
  *) echo "Unsupported architecture: $ARCH_RAW" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------
# ffmpeg + ffprobe (evermeet.cx, universal2 static build)
# ---------------------------------------------------------------
install_ffmpeg() {
  local name="$1" url="$2"
  local dest="$TOOLS_DIR/$name"
  if [[ -x "$dest" ]] && ! $FORCE; then
    skip "$name already present ($("$dest" -version 2>/dev/null | head -1 | cut -c1-60))"
    return
  fi
  info "Downloading $name from evermeet.cx ..."
  local tmp
  tmp="$(mktemp -d)"
  curl -fSL "$url" -o "$tmp/$name.zip"
  (cd "$tmp" && unzip -q "$name.zip")
  mv "$tmp/$name" "$dest"
  chmod +x "$dest"
  # Strip quarantine attribute so Gatekeeper doesn't refuse to execute.
  xattr -d com.apple.quarantine "$dest" 2>/dev/null || true
  rm -rf "$tmp"
  ok "$name installed: $("$dest" -version 2>/dev/null | head -1 | cut -c1-60)"
}

install_ffmpeg ffmpeg  "https://evermeet.cx/ffmpeg/getrelease/zip"
install_ffmpeg ffprobe "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

# ---------------------------------------------------------------
# mc (MinIO client, single Go binary)
# ---------------------------------------------------------------
MC_DEST="$TOOLS_DIR/mc"
if [[ -x "$MC_DEST" ]] && ! $FORCE; then
  skip "mc already present ($("$MC_DEST" --version 2>/dev/null | head -1 | cut -c1-60))"
else
  info "Downloading mc (darwin-$MC_ARCH) ..."
  curl -fSL "https://dl.min.io/client/mc/release/darwin-$MC_ARCH/mc" -o "$MC_DEST"
  chmod +x "$MC_DEST"
  xattr -d com.apple.quarantine "$MC_DEST" 2>/dev/null || true
  ok "mc installed: $("$MC_DEST" --version 2>/dev/null | head -1 | cut -c1-60)"
fi

ok "All tools ready in $TOOLS_DIR"
echo
echo "Add to PATH for this shell:"
echo "  export PATH=\"$TOOLS_DIR:\$PATH\""
echo "(The Makefile does this automatically.)"
