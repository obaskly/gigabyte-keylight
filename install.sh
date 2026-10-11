#!/bin/sh
# Set up Keyboard Lights for the current user: a private venv with PyQt6 and an app-menu launcher.
# No sudo needed. The one step that needs it is install-rule.sh (access to the keyboard device).
set -e
here=$(dirname "$(readlink -f "$0")")
cd "$here"

if [ ! -x .venv/bin/python ]; then
    if python3 -c "import venv, ensurepip" 2>/dev/null; then
        python3 -m venv .venv
    elif command -v uv >/dev/null 2>&1; then
        uv venv .venv
    else
        echo "python3 venv support is missing. Run: sudo apt install python3-venv" >&2
        exit 1
    fi
fi

if .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
else
    uv pip install --python .venv/bin/python -r requirements.txt
fi

apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps"
cat > "$apps/keyboard-lights.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Keyboard Lights
Comment=Colour and brightness for the Gigabyte keyboard backlight
Exec="$here/.venv/bin/python" "$here/kbd_gui.py"
Icon=$here/keylight.png
Terminal=false
Categories=Utility;
StartupWMClass=keyboard-lights
DESKTOP

echo
echo "Installed. Launcher: $apps/keyboard-lights.desktop"
echo "Next: sudo sh \"$here/install-rule.sh\"   (one time, gives your user access to the keyboard)"
