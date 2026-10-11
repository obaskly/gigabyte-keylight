# Keyboard Lights

<img width="358" height="803" alt="Screenshot from 2026-10-11 16-38-56" src="https://github.com/user-attachments/assets/834ff4a8-790b-4c16-953c-99a4d581a064" />

A small PyQt6 app (and command-line tool) for the keyboard backlight colour and brightness on Gigabyte
laptops running Linux. Gigabyte's own Control Center is Windows-only, and on Linux the only control you
normally get is the on/off toggle (Fn+Space).

It works because the keyboard speaks the standard **HID LampArray** protocol (usage page 0x59), so no
reverse engineering of Gigabyte software is needed. The app writes to that one HID interface and touches
nothing else.

> Not affiliated with Gigabyte. Use at your own risk.

## Check that your laptop is supported

Tested on a **Gigabyte Gaming A16 CVH**, Ubuntu with Python 3.12, kernel 7.0, X11 session.

The keyboard must show up as USB ID **0414:8104**:

```
lsusb | grep -i 0414:8104
```

- If it prints a line, continue.
- If it prints nothing, your model probably uses a different controller. Find your keyboard's ID with
  `lsusb`, and if it exposes a LampArray interface you can change `VID, PID` at the top of `kbd.py` and
  `idVendor`/`idProduct` in `70-gigabyte-kbd.rules`. This is untested, so expect to experiment.

The keyboard has a **single lamp**: one colour for the whole keyboard, not per key. The device also has
no hardware brightness control, so brightness is done by scaling the colour.

## What you need first

| Need | How to get it |
| --- | --- |
| Linux with `udev` and a desktop session | Any current distro. Tested on Ubuntu. |
| Python 3.9+ with `venv` | `sudo apt install python3 python3-venv` (or have [`uv`](https://docs.astral.sh/uv/) installed) |
| Qt xcb library (X11 sessions) | `sudo apt install libxcb-cursor0` (Qt 6.5+ will not start without it) |
| `sudo` rights | Only for the one-time access rule below |

PyQt6 itself is installed for you into a private `.venv` by `install.sh`. Nothing is installed system-wide
with pip.

## Install

Run these once, from the folder you cloned into.

**1. Set up the app (no sudo).** Creates `.venv`, installs PyQt6 and adds a **Keyboard Lights** entry to
your app menu:

```
./install.sh
```

**2. Give your user access to the keyboard (needs sudo).** By default the device node is root-only, so
without this step the window shows `ACCESS DENIED`. This installs a udev rule that grants access to your
logged-in user, for the LampArray interface only:

```
sudo sh install-rule.sh
```

The script lists `/dev/hidraw*` at the end. The line for your keyboard's LampArray node should now end in
`+` (for example `crw-rw----+`). It works immediately, with no reboot or re-login.

Optional: put a launcher on your Desktop as well:

```
cp ~/.local/share/applications/keyboard-lights.desktop ~/Desktop/
gio set ~/Desktop/keyboard-lights.desktop metadata::trusted true
```

## Use

Open **Keyboard Lights** from the app menu, or run it directly:

```
.venv/bin/python kbd_gui.py
```

- Drag the hue ring, use the saturation and luminance sliders, type a hex colour, or click a preset.
- **POWER** turns the lights black. **FIRMWARE MODE** hands control back to the keyboard's built-in effect.
- Your last colour and brightness are saved to `~/.config/gigabyte-kbd/state.json`.

If you see `ACCESS DENIED` in the window, step 2 above has not been done (or did not take effect). The
window shows the exact command to run and reconnects on its own once access works.

### Command line

The GUI and the CLI share the same saved colour:

```
python3 kbd.py info                      # read-only: lamp count and attributes
python3 kbd.py color 255 0 128           # solid colour, remembered
python3 kbd.py color 0 255 0 -b 40       # colour at 40% brightness
python3 kbd.py brightness +10            # set (40) or step (+10 / -10) brightness
python3 kbd.py restore                   # re-apply the remembered colour
python3 kbd.py off                       # lights black (colour is remembered)
python3 kbd.py auto on                   # give control back to the firmware
python3 kbd.py test                      # red, green, blue, then back to firmware
```

`kbd.py` only needs the Python standard library, so the CLI works without the venv. Bind `restore`,
`brightness +10` and `brightness -10` to keyboard shortcuts in your desktop settings if you want hotkeys.

## Troubleshooting

**`ACCESS DENIED` / `permission denied`**
Run `sudo sh install-rule.sh` and check that `ls -l /dev/hidraw*` shows a `+` on the LampArray node. The rule
file must sort before `73-seat-late.rules` (it is installed as `70-gigabyte-kbd.rules` for this reason).
If you still have a `99-gigabyte-kbd.rules` from an older version, `install-rule.sh` removes it.

**`DEVICE NOT FOUND`**
The app looks for a HID interface with ID 0414:8104 whose report descriptor starts with the LampArray
usage. Check `lsusb | grep 0414:8104` and see the support section above.

**The window does not start: `could not load the Qt platform plugin "xcb"`**
`sudo apt install libxcb-cursor0`.

**`./install.sh` says venv support is missing**
`sudo apt install python3-venv`, or install `uv`.

**Colour resets after reboot, suspend or Fn+Space**
Not fully tested. `python3 kbd.py restore` re-applies your saved colour, so you can run it at login or
resume if needed.

## Safety notes

- Only the LampArray interface (USB interface 4) is touched. The keyboard has other HID interfaces,
  including vendor-specific ones that may be related to firmware, and the udev rule deliberately does not
  grant access to those.
- Colour and brightness values are clamped to their valid ranges before anything is sent.
- Only tested on X11. Wayland is untested.

## Uninstall

```
sudo rm /etc/udev/rules.d/70-gigabyte-kbd.rules
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw --action=change
rm ~/.local/share/applications/keyboard-lights.desktop
rm -r ~/.config/gigabyte-kbd
```

Then delete the project folder. Run `python3 kbd.py auto on` first if you want the firmware's own lighting
back immediately.

## Files

| File | Purpose |
| --- | --- |
| `kbd_gui.py` | The PyQt6 window |
| `kbd.py` | HID LampArray code and the command-line tool |
| `install.sh` | Creates the venv, installs PyQt6, adds the app-menu launcher |
| `install-rule.sh` | Installs the udev rule (needs sudo) |
| `70-gigabyte-kbd.rules` | The udev rule |
| `keylight.png` | App icon |
