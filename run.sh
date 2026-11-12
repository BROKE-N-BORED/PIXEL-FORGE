#!/usr/bin/env bash
# Run Pixel Forge straight from this folder, without installing.
# Needs PySide6 for system python3 — if it is missing, install
# Pixel Forge first (double-click "Install Pixel Forge", or run
# bash install.sh). Usage: bash run.sh [image-to-open]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! python3 -c "import PySide6" 2>/dev/null; then
  echo "PySide6 is not installed for system python3."
  echo "Install Pixel Forge first: double-click 'Install Pixel Forge',"
  echo "or run:  bash install.sh"
  exit 1
fi

PYTHONPATH="$DIR" exec python3 -m pixel_forge.gui "$@"
