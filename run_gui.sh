#!/usr/bin/env bash
# Launch Unlimited-OCR Studio (Linux) with the Python chosen by setup; runs setup first if needed.
# In the release this script is named UnlimitedOCR-Studio. Files given as arguments are queued.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APP="$DIR/app"
[ -f "$DIR/app.py" ] && APP="$DIR"
PYF="$APP/python_path.txt"

python_ok() { [ -f "$PYF" ] && [ -x "$(cat "$PYF")" ]; }

if ! python_ok; then
    if [ -t 0 ]; then
        "$DIR/setup.sh" || exit 1
    else
        # started from a file manager / menu: run setup in a terminal window
        export UOCR_SETUP_PAUSE=1
        for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal mate-terminal xterm; do
            command -v "$t" >/dev/null 2>&1 || continue
            case "$t" in
                gnome-terminal) "$t" --wait -- "$DIR/setup.sh" ;;
                *) "$t" -e "$DIR/setup.sh" ;;
            esac
            break
        done
    fi
    if ! python_ok; then
        msg="Setup did not complete. Run setup.sh in a terminal to retry."
        command -v zenity >/dev/null 2>&1 && zenity --error --text="$msg" || echo "$msg" >&2
        exit 1
    fi
fi
exec "$(cat "$PYF")" "$APP/app.py" "$@"
