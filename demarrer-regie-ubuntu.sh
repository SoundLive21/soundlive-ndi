#!/usr/bin/env bash
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
if ! /usr/bin/python3 -c 'import tkinter' >/dev/null 2>&1; then
    echo "Installe Tkinter avec sudo apt install python3-tk."
    exit 1
fi
exec /usr/bin/python3 -B "$base/regie-windows.py"
