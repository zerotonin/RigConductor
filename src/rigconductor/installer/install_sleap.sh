#!/usr/bin/env bash
#
# ┌──────────────────────────────────────────────────────────────┐
# │ install_sleap.sh  « SLEAP for labelling, no GPU, no sudo »   │
# └──────────────────────────────────────────────────────────────┘
# Installs SLEAP @SLEAP_VERSION@ (CPU build) for the current user with uv and
# adds a "SLEAP labelling" launcher to the desktop and the app menu.
# Needs an internet connection and about 3 GB in your home folder.
#
# Usage:  bash install_sleap.sh          (again with SLEAP_REINSTALL=1 to repair)
set -euo pipefail

VERSION="${SLEAP_VERSION:-@SLEAP_VERSION@}"
PYVER="${SLEAP_PYTHON:-@SLEAP_PYTHON@}"
CPU_INDEX="${SLEAP_CPU_INDEX:-@SLEAP_CPU_INDEX@}"
TOOL="@SLEAP_TOOL@"

say() { echo "== $*"; }

UV="$(command -v uv || true)"
[ -n "$UV" ] || UV="$HOME/.local/bin/uv"
if [ ! -x "$UV" ]; then
  say "installing uv (Python package manager) into ~/.local/bin"
  command -v curl >/dev/null || { echo "curl is missing: ask an admin for 'sudo apt install curl'"; exit 1; }
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  UV="$HOME/.local/bin/uv"
fi
say "uv: $("$UV" --version)"

FORCE=()
[ "${SLEAP_REINSTALL:-0}" = "1" ] && FORCE=(--force)
say "installing SLEAP $VERSION (CPU) - this takes 5 to 10 minutes"
"$UV" tool install "${FORCE[@]}" --python "$PYVER" "sleap[nn]==$VERSION" \
  --index "$CPU_INDEX" --index https://pypi.org/simple

BIN="$("$UV" tool dir)/$TOOL/bin"
say "testing the installation"
"$BIN/python" -I -c "import sleap, sleap_io, sleap_nn, torch; print('SLEAP', sleap.__version__, '| torch', torch.__version__)"

APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/sleap-labelling.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SLEAP labelling
Comment=Label fly poses for the Geurten lab models
Exec="$BIN/sleap-label" %f
Terminal=false
Categories=Science;
MimeType=application/x-hdf5;
DESKTOP
if [ -d "$HOME/Desktop" ]; then
  cp "$APPS/sleap-labelling.desktop" "$HOME/Desktop/"
  chmod +x "$HOME/Desktop/sleap-labelling.desktop"
  gio set "$HOME/Desktop/sleap-labelling.desktop" metadata::trusted true 2>/dev/null || true
fi
say "done: SLEAP is ready. Open a package with label.sh or the desktop icon."
echo "@sleap-python $BIN/python"
