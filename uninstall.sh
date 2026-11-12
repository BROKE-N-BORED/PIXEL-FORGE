#!/usr/bin/env bash
# Pixel Forge uninstaller — run:  bash uninstall.sh
# Removes the venv, PATH links, menu launcher and icons.
set -euo pipefail

PREFIX="$HOME/.local/share/pixel-forge"

rm -rf "$PREFIX/venv"
rm -f "$HOME/.local/bin/pixel-forge" "$HOME/.local/bin/pixel-forge-gui"
rm -f "$HOME/.local/share/applications/pixel-forge.desktop"
rm -f "$HOME/.local/share/icons/hicolor/48x48/apps/pixel-forge.png"
rm -f "$HOME/.local/share/icons/hicolor/128x128/apps/pixel-forge.png"
rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/pixel-forge.png"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/pixel-forge.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$PREFIX"
  echo "Pixel Forge removed completely."
else
  echo "Pixel Forge removed."
fi
