#!/usr/bin/env bash
#
# ┌──────────────────────────────────────────────────────────────┐
# │ label.sh  « open this labelling package in SLEAP »           │
# └──────────────────────────────────────────────────────────────┘
# Opens labels.pkg.slp (next to this script) in SLEAP. Installs SLEAP first if
# it is not there yet. Save with Ctrl+S; the file on the stick is what goes back.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="@SLEAP_TOOL@"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
BIN=""
[ -x "$UV" ] && BIN="$("$UV" tool dir 2>/dev/null)/$TOOL/bin"
if [ -z "$BIN" ] || [ ! -x "$BIN/sleap-label" ]; then
  echo "SLEAP is not installed for $USER yet; installing it now."
  bash "$HERE/install_sleap.sh"
  UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
  BIN="$("$UV" tool dir)/$TOOL/bin"
fi
exec "$BIN/sleap-label" "$HERE/labels.pkg.slp"
