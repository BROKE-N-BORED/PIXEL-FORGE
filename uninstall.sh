#!/usr/bin/env bash
# Pixel Forge uninstaller — run:  bash uninstall.sh
# Removes the venv, PATH links, menu launcher and icons. Your
# sprite library, image models and the image engine are kept.
# With --purge, removes everything else too: the install folder,
# the image engine (/opt/sd.cpp), and — only if you say so —
# your sprite library and image models (those hold your data,
# so --purge asks about each one before deleting it, default No).
set -euo pipefail

PREFIX="$HOME/.local/share/pixel-forge"
SD_INSTALL_DIR="${PIXELFORGE_SD_INSTALL_DIR:-/opt/sd.cpp}"

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
  # The image engine lives outside the home folder (root-owned),
  # so it needs sudo. It holds no user data — just the program.
  if [ -d "$SD_INSTALL_DIR" ]; then
    if sudo rm -rf "$SD_INSTALL_DIR"; then
      echo "Removed the image engine ($SD_INSTALL_DIR)."
    else
      echo "WARNING: could not remove $SD_INSTALL_DIR — remove it by hand with: sudo rm -rf $SD_INSTALL_DIR"
    fi
  fi
  # User data — ask first, default No. Never deleted silently.
  # Without a terminal there is no way to ask, so it is kept and
  # the way to remove it by hand is printed.
  ask_remove() {  # $1 = path, $2 = description
    if [ ! -e "$1" ]; then
      return 0
    fi
    if [ -t 0 ]; then
      read -rp "Remove $2 ($1)? [y/N] " _answer || true
      case "${_answer:-N}" in
        [Yy]*)
          rm -rf "$1"
          echo "Removed $1."
          ;;
        *)
          echo "Kept $1."
          ;;
      esac
    else
      echo "Kept $1 — remove it by hand with: rm -rf $1"
    fi
  }
  ask_remove "$HOME/pixel-forge-library" "your sprite library"
  ask_remove "$HOME/design-shell-models" "the shared image models (also used by the Design Shell)"
  ask_remove "$HOME/pixel-forge-models" "the Pixel Forge image models"
  echo "Pixel Forge removed completely."
else
  echo "Pixel Forge removed."
  echo "Kept: your sprite library, image models, the image engine, and $PREFIX."
  echo "To remove those too, run: bash uninstall.sh --purge"
fi
