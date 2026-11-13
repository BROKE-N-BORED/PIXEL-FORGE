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
# Pinned — these MUST match ENGINE_RELEASE / MODEL_SHA256 in
# pixel_forge/imagegen.py; tests/test_installer.py checks they
# agree, so the two can never drift silently. The engine is
# checked out at this release (never master HEAD) and every model
# download is SHA-256-checked against this hash.
SD_RELEASE="master-1086-465adf3"
SD_MODEL_SHA256="0642060766117fdeb8cae0ccdc73ee974ac5779be1112d0a82f2b965bbd00e15"
# Exact size of the pinned model file, in bytes — MUST match
# MODEL_SIZE_BYTES in pixel_forge/imagegen.py (the installer
# tests check they agree). A file named SD_MODEL_FILE at any
# other size is a partial download, never the model.
SD_MODEL_SIZE=2134675232
# Shared with the Design Shell: one download serves both programs.
SD_MODEL_HOME="$HOME/design-shell-models"
# Where the built engine is installed (root-owned). Override with
# PIXELFORGE_SD_INSTALL_DIR to put it somewhere else.
SD_INSTALL_DIR="${PIXELFORGE_SD_INSTALL_DIR:-/opt/sd.cpp}"
# Where the engine source build lives (task 85) — a persistent
# folder under the install home, not a temp dir, so a failed or
# interrupted build resumes on the next run instead of starting
# the ~30-minute build over. Removed after a successful install.
# Same path the GUI installer uses (SD_BUILD_SUBDIR).
SD_BUILD_ROOT="$PREFIX/sd-build"
WITH_IMAGE=0
for arg in "$@"; do
  case "$arg" in
    --with-image-engine) WITH_IMAGE=1 ;;
  esac
done

# ---- failure tracking, the install summary, and the helpers the
# steps below use (task 32) ----
# FAILURES holds "step — what to do about it" for steps the install
# cannot work without; WARNINGS holds optional bits that did not
# land (icons, ffmpeg). Both are printed by print_summary at the
# end, and by the ERR trap on a mid-way abort — so the install
# never dies silently mid-script and never claims success it did
# not earn.
declare -a FAILURES=()
declare -a WARNINGS=()

record_failure() {  # $1 = step, $2 = the plain fix
  FAILURES+=("$1 — $2")
}

record_warning() {  # $1 = warning text
  WARNINGS+=("$1")
}

print_summary() {
  echo
  echo "== Install summary =="
  if [ "${#FAILURES[@]}" -gt 0 ]; then
    echo "Failed steps:"
    for item in "${FAILURES[@]}"; do
      echo "  - $item"
    done
  fi
  if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo "Warnings (the install still works without these):"
    for item in "${WARNINGS[@]}"; do
      echo "  - $item"
    done
  fi
  if [ "${#FAILURES[@]}" -eq 0 ] && [ "${#WARNINGS[@]}" -eq 0 ]; then
    echo "No failures, no warnings."
  fi
}

# On a mid-way abort (set -e), print the summary too instead of
# dying silently mid-script — the failing command is recorded as
# a failed step first (so the summary names it, instead of
# reading "No failures" next to an aborted install), then the
# summary is printed and the verdict is plain.
# (An ERR trap, not an explicit check after each step: the script
# already aborts on the first failure, so the trap is the one
# place the summary is guaranteed to run.)
trap 'record_failure "install step" "$BASH_COMMAND failed — see the error above"; echo; echo "ERROR: the install stopped early while running: $BASH_COMMAND"; print_summary; echo "Pixel Forge is NOT installed — fix the failed step above and run the installer again."; exit 1' ERR

check_network() {
  # One quick HEAD request to the package index — the install
  # downloads from it (pip), from apt, and (with
  # --with-image-engine) from GitHub and HuggingFace. A 10-second
  # timeout: a dead connection must fail fast, not hang the
  # installer. An HTTP error status still counts as online — the
  # server answered, so the machine is connected.
  python3 -c "
import sys, urllib.request, urllib.error
request = urllib.request.Request('https://pypi.org/simple/', method='HEAD')
try:
    urllib.request.urlopen(request, timeout=10)
except urllib.error.HTTPError:
    pass
except (urllib.error.URLError, OSError, ValueError):
    sys.exit(1)
" 2>/dev/null
}

detect_venv_package() {
  # The venv package is versioned (python3.12-venv, python3.11-venv
  # ...) — ask the system Python its own version instead of
  # hardcoding one. Falls back to the unversioned python3-venv
  # when the version cannot be detected.
  local version
  version="$(python3 -c "import sys; print('%d.%d' % (sys.version_info[0], sys.version_info[1]))" 2>/dev/null || echo "")"
  case "$version" in
    *[!0-9.]*) echo "python3-venv" ;;
    [0-9]*.[0-9]*) echo "python${version}-venv" ;;
    *) echo "python3-venv" ;;
  esac
}

backup_sd_cpp() {
  # Move the existing engine aside instead of wiping it — a failed
  # build must not destroy the working engine. Only called after
  # the new build succeeded. Returns 0 when there was nothing to
  # back up or the move worked; returns 1 when the backup move
  # failed, in which case the caller stops instead of wiping.
  if [ -d "$SD_INSTALL_DIR" ]; then
    echo "Backing up the existing image engine to ${SD_INSTALL_DIR}.bak…"
    sudo rm -rf "${SD_INSTALL_DIR}.bak" || return 1
    sudo mv "$SD_INSTALL_DIR" "${SD_INSTALL_DIR}.bak" || return 1
  fi
  return 0
}

build_jobs() {
  # How many parallel jobs the engine source build may use (task
  # 84): one g++ per core fills 8 GB of RAM and the machine swaps
  # to the HDD, freezing the desktop. Under 16 GB available memory
  # the build is capped at 2 jobs; at or above it, every core is
  # used. Unknown memory (no /proc/meminfo, or a value that is not
  # a number) means "use every core" — never a refusal. Mirrors
  # imagegen.build_jobs in the GUI installer. PIXELFORGE_MEMINFO
  # points at a fake meminfo (tests only).
  local avail_kb jobs cpus
  cpus="$(nproc)"
  jobs="$cpus"
  avail_kb="$(awk '/^MemAvailable:/ {print $2}' "${PIXELFORGE_MEMINFO:-/proc/meminfo}" 2>/dev/null || true)"
  case "$avail_kb" in
    ''|*[!0-9]*) ;;
    *)
      if [ "$avail_kb" -lt 16777216 ] && [ "$jobs" -gt 2 ]; then
        jobs=2
      fi
      ;;
  esac
  echo "$jobs"
}

verify_install() {
  # Prove the install works before claiming it: the venv exists,
  # the CLI runs, and the GUI imports. The import runs from a fresh
  # empty directory — `python -c` puts the working directory first
  # on sys.path, and this script runs from the extracted tree, which
  # contains a pixel_forge package (the v0.11 lesson). Failures are
  # recorded, not fatal — the summary at the end decides the verdict.
  local ok=1
  local _import_tmp
  if [ ! -x "$VENV/bin/python" ]; then
    record_failure "virtual environment" "the venv python is missing — re-run the installer"
    ok=0
  elif ! "$VENV/bin/python" --version >/dev/null 2>&1; then
    record_failure "virtual environment" "the venv python does not run — re-run the installer"
    ok=0
  fi
  if [ "$ok" = "1" ]; then
    if ! "$VENV/bin/pixel-forge" --version >/dev/null 2>&1; then
      record_failure "Pixel Forge command" "pixel-forge --version failed — the pip install may have failed; check your connection and re-run the installer"
      ok=0
    fi
    _import_tmp="$(mktemp -d)"
    if ! (cd "$_import_tmp" && "$VENV/bin/python" -c "import pixel_forge.gui" >/dev/null 2>&1); then
      record_failure "Pixel Forge GUI" "the installed GUI does not import — the pip install may have failed; check your connection and re-run the installer"
      ok=0
    fi
    rm -rf "$_import_tmp"
  fi
  [ "$ok" = "1" ]
}

echo "Installing Pixel Forge…"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed. Install it first, then re-run this."
  exit 1
fi

VENV_PKG="$(detect_venv_package)"
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3-venv is missing. Run this once, then re-run the installer:"
  if [ "$VENV_PKG" = "python3-venv" ]; then
    echo "  (the Python version could not be detected — trying the unversioned package)"
  fi
  echo "  sudo apt install $VENV_PKG python3-pip"
  exit 1
fi

if ! python3 -m pip --help >/dev/null 2>&1; then
  echo "python3-pip is missing. Run this once, then re-run the installer:"
  echo "  sudo apt install python3-pip"
  exit 1
fi

# The install downloads packages (apt, pip — plus the engine
# source and the model with --with-image-engine), so check for
# internet first: an offline run stops here with a plain message
# instead of failing step by step with obscure errors.
if ! check_network; then
  echo "You need internet to install Pixel Forge."
  echo "The install downloads system packages (apt) and Python packages (pip)."
  if [ "$WITH_IMAGE" = "1" ]; then
    echo "With --with-image-engine it also downloads the image engine source (git) and the 2.1 GB model."
  fi
  echo "Check your connection and run the installer again."
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
  for c in "$SD_INSTALL_DIR/bin/sd-cli" "$HOME/.local/bin/sd-cli" "$HOME/sd.cpp/build/bin/sd-cli"; do
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
    JOBS="$(build_jobs)"
    if [ "$JOBS" -lt "$(nproc)" ]; then
      echo "Building with $JOBS parallel jobs (limited for low memory, so the desktop stays usable)…"
    else
      echo "Building with $JOBS parallel jobs…"
    fi
    SD_SRC="$SD_BUILD_ROOT/sd"
    SD_BUILD_DIR="$SD_SRC/build"
    # Resume (task 85): an earlier run may have left a checkout
    # and a half-built build folder here. Reuse them instead of
    # starting over — a freeze at 92% costs the remaining 8%, not
    # the whole half hour.
    if [ -d "$SD_SRC/.git" ]; then
      if [ "$(git -C "$SD_SRC" describe --tags --exact-match HEAD 2>/dev/null || true)" = "$SD_RELEASE" ]; then
        echo "Resuming from the engine checkout in $SD_SRC (already at $SD_RELEASE)…"
      else
        echo "Updating the engine checkout in $SD_SRC to $SD_RELEASE…"
        if ! git -C "$SD_SRC" fetch --depth 1 origin tag "$SD_RELEASE" \
            || ! git -C "$SD_SRC" checkout "$SD_RELEASE" \
            || ! git -C "$SD_SRC" submodule update --init --recursive; then
          echo "Could not update the checkout — checking it out again from scratch…"
          rm -rf "$SD_SRC"
          git clone --depth 1 --branch "$SD_RELEASE" --recursive "$SD_REPO" "$SD_SRC" \
            || { echo "ERROR: could not check out the image engine at $SD_RELEASE." >&2
                 echo "       Check your connection and run ./install.sh --with-image-engine again." >&2
                 exit 1; }
        fi
      fi
    else
      rm -rf "$SD_SRC"
      git clone --depth 1 --branch "$SD_RELEASE" --recursive "$SD_REPO" "$SD_SRC" \
        || { echo "ERROR: could not check out the image engine at $SD_RELEASE." >&2
             echo "       Check your connection and run ./install.sh --with-image-engine again." >&2
             exit 1; }
    fi
    if [ -d "$SD_BUILD_DIR" ] && [ ! -f "$SD_BUILD_DIR/CMakeCache.txt" ]; then
      echo "The engine build folder has no finished setup — clearing it and starting the build fresh…"
      rm -rf "$SD_BUILD_DIR"
    fi
    if [ -f "$SD_BUILD_DIR/CMakeCache.txt" ]; then
      echo "Resuming the engine build in $SD_BUILD_DIR…"
      if ! cmake --build "$SD_BUILD_DIR" -j"$JOBS" >/dev/null; then
        echo "The resumed build failed — clearing the build folder and rebuilding once from clean…"
        rm -rf "$SD_BUILD_DIR"
        cmake -S "$SD_SRC" -B "$SD_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
        cmake --build "$SD_BUILD_DIR" -j"$JOBS" >/dev/null
      fi
    else
      cmake -S "$SD_SRC" -B "$SD_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
      cmake --build "$SD_BUILD_DIR" -j"$JOBS" >/dev/null
    fi
    # The build succeeded — only now is the old engine touched, and
    # it is moved aside (backup_sd_cpp), never wiped. If the backup
    # move fails, stop here: the old engine stays in place. (The
    # build folder is kept, so the next run resumes from the built
    # engine instead of rebuilding.)
    if ! backup_sd_cpp; then
      echo "ERROR: could not back up the existing image engine at $SD_INSTALL_DIR." >&2
      echo "       The new build was not installed — the old engine is untouched." >&2
      exit 1
    fi
    sudo mkdir -p "$SD_INSTALL_DIR/bin"
    sudo cp "$SD_BUILD_DIR/bin/"* "$SD_INSTALL_DIR/bin/"
    sudo ln -sf "$SD_INSTALL_DIR/bin/sd-cli" /usr/local/bin/sd-cli
    # The engine is installed — the build files are multi-GB junk
    # now. Remove the checkout and the object files; the installed
    # engine stays in $SD_INSTALL_DIR.
    rm -rf "$SD_BUILD_ROOT"
    echo "Removed the engine build files from $SD_BUILD_ROOT (the installed engine stays in $SD_INSTALL_DIR)."
    SD_BIN="$SD_INSTALL_DIR/bin/sd-cli"
    echo "Image engine built: $SD_BIN"
  else
    echo "Image engine already installed: $SD_BIN"
  fi
  # A partial model download must not pass as the finished model:
  # a file named SD_MODEL_FILE at the wrong size is an interrupted
  # download, and the curl below resumes it (-C -) instead of the
  # install calling it done. A renamed or newer .gguf keeps the
  # old behavior — its size is unknown, so it is accepted as-is.
  SD_MODEL_FOUND="$(find_sd_model)"
  if [ -n "$SD_MODEL_FOUND" ] && [ "$(basename "$SD_MODEL_FOUND")" = "$SD_MODEL_FILE" ] \
      && [ "$(stat -c%s "$SD_MODEL_FOUND" 2>/dev/null || echo 0)" != "$SD_MODEL_SIZE" ]; then
    echo "The model download was interrupted — resuming it instead of starting over…"
    SD_MODEL_FOUND=""
  fi
  if [ -z "$SD_MODEL_FOUND" ]; then
    echo "Downloading the image model ($SD_MODEL_FILE, about 2.1 GB)..."
    mkdir -p "$SD_MODEL_HOME"
    curl -fSL -C - "$SD_MODEL_URL" -o "$SD_MODEL_HOME/$SD_MODEL_FILE"
    # Prove the download is the pinned model before accepting it:
    # a mismatched file is deleted, never kept.
    _actual="$(sha256sum "$SD_MODEL_HOME/$SD_MODEL_FILE" | cut -d' ' -f1)"
    if [ "$_actual" != "$SD_MODEL_SHA256" ]; then
      echo "ERROR: the downloaded model failed its SHA-256 check." >&2
      echo "       expected: $SD_MODEL_SHA256" >&2
      echo "       got:      $_actual" >&2
      echo "       The bad file was removed — run ./install.sh --with-image-engine again to retry." >&2
      rm -f "$SD_MODEL_HOME/$SD_MODEL_FILE"
      exit 1
    fi
    echo "Model downloaded to $SD_MODEL_HOME/$SD_MODEL_FILE"
  else
    echo "Image model already downloaded: $SD_MODEL_FOUND"
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
# ships only the SVG, the release zip ships the PNGs too). A failed
# render is reported, not swallowed: a missing icon means a silently
# icon-less menu entry. The install continues either way.
if [ ! -f "$DIR/icons/pixel-forge-256.png" ] && [ -f "$DIR/icons/pixel-forge.svg" ]; then
  echo "Rendering app icons from the SVG…"
  _icon_err="$(mktemp)"
  if "$VENV/bin/python" -c "
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
import sys
renderer = QSvgRenderer('$DIR/icons/pixel-forge.svg')
if not renderer.isValid():
    print('pixel-forge.svg is not a valid SVG file', file=sys.stderr)
    sys.exit(1)
for size in (256, 128, 48):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save('$DIR/icons/pixel-forge-%d.png' % size):
        print('could not write pixel-forge-%d.png' % size, file=sys.stderr)
        sys.exit(1)
" 2>"$_icon_err"; then
    echo "App icons rendered from pixel-forge.svg"
  else
    echo "WARNING: could not render app icons from the SVG — the menu entry will show a generic icon"
    sed 's/^/  /' "$_icon_err" | head -20
    record_warning "app icons could not be rendered from the SVG (see the log above)"
  fi
  rm -f "$_icon_err"
fi
for spec in "48 pixel-forge-48.png" "128 pixel-forge-128.png" "256 pixel-forge-256.png"; do
  set -- $spec
  dest="$HOME/.local/share/icons/hicolor/${1}x${1}/apps"
  mkdir -p "$dest"
  _cp_err="$(cp "$DIR/icons/$2" "$dest/pixel-forge.png" 2>&1)" || {
    echo "WARNING: could not install the ${1}x${1} app icon: $_cp_err"
    record_warning "the ${1}x${1} app icon could not be installed"
  }
done
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$DIR/icons/pixel-forge.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/pixel-forge.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  _cache_err="$(gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>&1)" || {
    echo "WARNING: gtk-update-icon-cache failed — the menu icon may not update until you log out: $_cache_err"
    record_warning "the icon cache could not be updated"
  }
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
  _db_err="$(update-desktop-database "$HOME/.local/share/applications" 2>&1)" || {
    echo "WARNING: update-desktop-database failed — the menu entry may not appear until you log out: $_db_err"
    record_warning "the desktop database could not be updated"
  }
fi

# Verify the install before claiming it (the GUI installer has the
# same check as venv_gui_imports — this is the shell side of it).
verify_install || true

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
print_summary
if [ "${#FAILURES[@]}" -gt 0 ]; then
  echo
  echo "Pixel Forge is NOT installed — fix the failed steps above and run the installer again."
  exit 1
fi
echo
echo "Pixel Forge is installed."
echo "  GUI:  open Pixel Forge from the menu, or run:  pixel-forge-gui"
echo "  CLI:  pixel-forge convert photo.png --size 32 --colors 16"
echo "  Video: pixel-forge frames clip.mp4 --fps 8 --out frames/"
echo "  Prompt: pixel-forge prompt \"a knight\" --out knight/  (needs the image engine)"
echo
if [ -t 0 ]; then
  read -rp "Press Enter to close." _dummy || true
fi
