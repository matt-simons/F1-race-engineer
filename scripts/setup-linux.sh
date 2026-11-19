#!/usr/bin/env bash
# One-off setup on Linux (Bazzite, Fedora, SteamOS...): makes a private Python
# environment in .venv, installs the Piper voice and downloads a voice model.
# Nothing is installed system-wide, so it's safe on immutable systems.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m f1engineer download-voice
.venv/bin/python -m f1engineer say "Radio check. Loud and clear."
echo
echo "Done. Start the engineer with: ./engineer.sh"
