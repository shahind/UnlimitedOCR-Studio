#!/usr/bin/env bash
# Setup (Linux): finds an installed Python (preferring one with CUDA PyTorch), installs only missing packages,
# and downloads the OCR model. Shows sizes and free space first. Safe to re-run.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
INSTALL=install.py
[ -f app/install.py ] && INSTALL=app/install.py

PY=""
for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1; then PY=$(command -v "$p"); break; fi
done
if [ -z "$PY" ]; then
    echo "No Python installation was found."
    echo "Install Python 3.10-3.13 (e.g. 'sudo apt install python3' or https://www.python.org/downloads/)"
    echo "and run this setup again."
    status=1
else
    "$PY" "$INSTALL" "$@"
    status=$?
fi
# keep the window open when started from the launcher in a new terminal
if [ -t 0 ] && [ -n "$UOCR_SETUP_PAUSE" ]; then
    echo
    read -r -p "Press Enter to close..." _
fi
exit $status
