#!/usr/bin/env bash
# Pixel Forge installer — double-click "Install Pixel Forge.desktop",
# or run this in a terminal with BASH (never sh — this script uses
# pipefail):
#     bash install.sh
#     bash install.sh --with-image-engine
# Creates a venv under ~/.local/share/pixel-forge, installs Pixel
# Forge + PySide6, and puts `pixel-forge` / `pixel-forge-gui` on
# your PATH plus Pixel Forge in your app menu. --with-image-engine
# also builds the image engine from source and downloads its model
# (needs git, cmake, a C++ compiler; the prebuilt engine binary
# needs glibc 2.43 and Mint ships 2.39, so there is no shortcut).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="$HOME/.local/share/pixel-forge"
VENV="$PREFIX/venv"
BIN="$HOME/.local/bin"

SD_REPO="https://github.com/leejet/stable-diffusion.cpp"
SD_MODEL_FILE="LCM_Dreamshaper_v7-f16.gguf"
SD_MODEL_URL="https://huggingface.co/Steward/lcm-dreamshaper-v7-gguf/resolve/main/${SD_MODEL_FILE}"
# Shared with the Design Shell: one download serves both programs.
SD_MODEL_HOME="$HOME/design-shell-models"
WITH_IMAGE=0
for arg in "$@"; do
  case "$arg" in
    --with-image-engine) WITH_IMAGE=1 ;;
  esac
done

echo "Installing Pixel Forge…"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed. Install it first, then re-run this."
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3-venv is missing. Run this once, then re-run the installer:"
  echo "  sudo apt install python3.12-venv python3-pip"
  exit 1
fi

# ffmpeg (optional — only needed for video import; image import
# works without it). Warn-never-refuse: a failed install just means
# video import stays unavailable until ffmpeg is installed.
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is not installed — video import needs it."
  if [ -t 0 ]; then
    read -rp "Install ffmpeg now? [y/N] " _answer || true
    case "${_answer:-N}" in
      [Yy]*)
        sudo apt-get update && sudo apt-get install -y ffmpeg \
          || echo "WARNING: ffmpeg could not be installed — video import will stay unavailable until you run: sudo apt install ffmpeg"
        ;;
      *)
        echo "Skipped ffmpeg — to enable video import later, run: sudo apt install ffmpeg"
        ;;
    esac
  else
    echo "To enable video import later, run: sudo apt install ffmpeg"
  fi
fi

# Image engine (stable-diffusion.cpp + LCM model) — only for
# prompt import; everything else works without it.
find_sd() {
  if [ -n "${PIXELFORGE_SD_CLI:-}" ] && [ -x "${PIXELFORGE_SD_CLI}" ]; then
    echo "$PIXELFORGE_SD_CLI"; return
  fi
  if command -v sd-cli >/dev/null 2>&1; then command -v sd-cli; return; fi
  for c in /opt/sd.cpp/bin/sd-cli "$HOME/.local/bin/sd-cli" "$HOME/sd.cpp/build/bin/sd-cli"; do
    if [ -x "$c" ]; then echo "$c"; return; fi
  done
  echo ""
}

find_sd_model() {
  if [ -n "${PIXELFORGE_SD_MODEL:-}" ] && [ -f "${PIXELFORGE_SD_MODEL}" ]; then
    echo "$PIXELFORGE_SD_MODEL"; return
  fi
  for home_dir in "$SD_MODEL_HOME" "$HOME/pixel-forge-models"; do
    if [ -f "$home_dir/$SD_MODEL_FILE" ]; then echo "$home_dir/$SD_MODEL_FILE"; return; fi
    first="$(ls "$home_dir"/*.gguf 2>/dev/null | head -1 || true)"
    if [ -n "$first" ]; then echo "$first"; return; fi
  done
  echo ""
}

SD_BIN="$(find_sd)"
if [ "$WITH_IMAGE" = "1" ]; then
  if [ -z "$SD_BIN" ]; then
    # The prebuilt Linux binary needs glibc 2.43 (Ubuntu 26.04);
    # Mint ships 2.39, so the engine is built from source here.
    # The clone MUST be --recursive: ggml is a submodule, and a
    # non-recursive clone fails the build.
    for tool in git cmake g++; do
      if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: building the image engine needs $tool." >&2
        echo "       Fix: sudo apt install git cmake build-essential" >&2
        echo "       then run ./install.sh --with-image-engine again." >&2
        exit 1
      fi
    done
    echo "Building the image engine (stable-diffusion.cpp) from source..."
    echo "(one-time compile — allow up to half an hour on a small machine)"
    TMPD="$(mktemp -d)"
    git clone --depth 1 --recursive "$SD_REPO" "$TMPD/sd"
    cmake -S "$TMPD/sd" -B "$TMPD/sd/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build "$TMPD/sd/build" -j"$(nproc)" >/dev/null
    sudo rm -rf /opt/sd.cpp
    sudo mkdir -p /opt/sd.cpp/bin
    sudo cp "$TMPD/sd/build/bin/"* /opt/sd.cpp/bin/
    sudo ln -sf /opt/sd.cpp/bin/sd-cli /usr/local/bin/sd-cli
    rm -rf "$TMPD"
    SD_BIN="/opt/sd.cpp/bin/sd-cli"
    echo "Image engine built: $SD_BIN"
  else
    echo "Image engine already installed: $SD_BIN"
  fi
  if [ -z "$(find_sd_model)" ]; then
    echo "Downloading the image model ($SD_MODEL_FILE, about 2.1 GB)..."
    mkdir -p "$SD_MODEL_HOME"
    curl -fSL "$SD_MODEL_URL" -o "$SD_MODEL_HOME/$SD_MODEL_FILE"
    echo "Model downloaded to $SD_MODEL_HOME/$SD_MODEL_FILE"
  else
    echo "Image model already downloaded: $(find_sd_model)"
  fi
fi

mkdir -p "$PREFIX" "$BIN"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating virtualenv at $VENV …"
  python3 -m venv "$VENV"
fi

echo "Installing packages (Pixel Forge, PySide6)…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$DIR"

ln -sf "$VENV/bin/pixel-forge" "$BIN/pixel-forge"
ln -sf "$VENV/bin/pixel-forge-gui" "$BIN/pixel-forge-gui"

# Icons (render from the SVG if the PNGs are missing — a git clone
# ships only the SVG, the release zip ships the PNGs too)
if [ ! -f "$DIR/icons/pixel-forge-256.png" ] && [ -f "$DIR/icons/pixel-forge.svg" ]; then
  "$VENV/bin/python" -c "
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
import sys
renderer = QSvgRenderer('$DIR/icons/pixel-forge.svg')
if renderer.isValid():
    for size in (256, 128, 48):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        image.save('$DIR/icons/pixel-forge-%d.png' % size)
" 2>/dev/null || echo "(could not render icons from the SVG — continuing)"
fi
for spec in "48 pixel-forge-48.png" "128 pixel-forge-128.png" "256 pixel-forge-256.png"; do
  set -- $spec
  dest="$HOME/.local/share/icons/hicolor/${1}x${1}/apps"
  mkdir -p "$dest"
  cp "$DIR/icons/$2" "$dest/pixel-forge.png" 2>/dev/null || true
done
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$DIR/icons/pixel-forge.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/pixel-forge.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# Menu launcher
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/pixel-forge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pixel Forge
Comment=Turn images and videos into pixel art and sprites
Exec=$BIN/pixel-forge-gui
Icon=pixel-forge
Terminal=false
Categories=Graphics;
StartupNotify=true
EOF
chmod 644 "$HOME/.local/share/applications/pixel-forge.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo
echo "== Image engine (for prompt import) =="
SD_BIN="$(find_sd)"
SD_MODEL="$(find_sd_model)"
if [ -n "$SD_BIN" ]; then
  echo "sd-cli: $SD_BIN"
else
  echo "sd-cli: NOT FOUND — prompt import will refuse until the engine exists."
  echo "        Fix: ./install.sh --with-image-engine   (builds it from source)"
fi
if [ -n "$SD_MODEL" ]; then
  echo "image model: $SD_MODEL"
else
  echo "image model: NOT FOUND — download $SD_MODEL_FILE (about 2.1 GB) into"
  echo "        $SD_MODEL_HOME/  or run ./install.sh --with-image-engine"
fi

echo
echo "Pixel Forge is installed."
echo "  GUI:  open Pixel Forge from the menu, or run:  pixel-forge-gui"
echo "  CLI:  pixel-forge convert photo.png --size 32 --colors 16"
echo "  Video: pixel-forge frames clip.mp4 --fps 8 --out frames/"
echo "  Prompt: pixel-forge prompt \"a knight\" --out knight/  (needs the image engine)"
echo
if [ -t 0 ]; then
  read -rp "Press Enter to close." _dummy
fi
