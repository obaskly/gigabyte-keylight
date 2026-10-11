#!/usr/bin/env python3
"""Gigabyte laptop keyboard backlight control over the standard HID LampArray
interface (usage page 0x59, USB 0414:8104). The keyboard exposes a single lamp,
so one colour covers the whole keyboard. Needs read/write access to the hidraw
node: run with sudo, or run install-rule.sh next to this file.

  kbd.py info                      read-only: lamp count, kind, per-lamp attributes
  kbd.py color R G B [-b PERCENT]  solid colour (0-255 per channel), remembered
  kbd.py brightness N|+N|-N        set or step brightness 0-100, keeps the colour
  kbd.py restore                   re-apply the remembered colour and brightness
  kbd.py off                       lights black (the remembered colour is kept)
  kbd.py auto on|off               hand control back to / take it from the firmware
  kbd.py test                      red, green, blue for 1.5 s each, then back to firmware
"""
import argparse
import fcntl
import glob
import json
import os
import struct
import sys
import time

VID, PID = 0x0414, 0x8104
LAMPARRAY_PREFIX = bytes([0x05, 0x59, 0x09, 0x01])  # Usage Page Lighting, Usage LampArray

# Report IDs and total lengths (id byte + payload), from the device's report descriptor.
R_ATTRS, R_REQ, R_RESP, R_MULTI, R_RANGE, R_CTRL = 1, 2, 3, 4, 5, 6
LEN = {R_ATTRS: 23, R_REQ: 3, R_RESP: 29, R_MULTI: 51, R_RANGE: 10, R_CTRL: 2}
UPDATE_COMPLETE = 0x01

STATE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                     "gigabyte-kbd", "state.json")


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def hidiocsfeature(n):
    return _ioc(3, 0x06, n)


def hidiocgfeature(n):
    return _ioc(3, 0x07, n)


def find_device():
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(f"{node}/device/uevent").read()
            desc = open(f"{node}/device/report_descriptor", "rb").read()
        except OSError:
            continue
        if f"HID_ID=0003:{VID:08X}:{PID:08X}" in uevent and desc.startswith(LAMPARRAY_PREFIX):
            return "/dev/" + os.path.basename(node)
    sys.exit("LampArray interface for 0414:8104 not found")


def get_feature(fd, rid):
    buf = bytearray(LEN[rid])
    buf[0] = rid
    fcntl.ioctl(fd, hidiocgfeature(len(buf)), buf, True)
    return bytes(buf)


def set_feature(fd, data):
    fcntl.ioctl(fd, hidiocsfeature(len(data)), bytes(data))


def attributes(fd):
    _, count, w, h, d, kind, interval = struct.unpack("<BHIIIII", get_feature(fd, R_ATTRS))
    return dict(count=count, width_um=w, height_um=h, depth_um=d, kind=kind, min_interval_us=interval)


def lamp_attributes(fd, lamp_id):
    set_feature(fd, struct.pack("<BH", R_REQ, lamp_id))
    raw = get_feature(fd, R_RESP)
    (_, lid, x, y, z, latency, purposes, rl, gl, bl, il, prog, binding) = struct.unpack("<BHIIIIIBBBBBB", raw)
    return dict(id=lid, x=x, y=y, z=z, latency_us=latency, purposes=purposes,
                levels=(rl, gl, bl, il), programmable=prog, input_binding=binding)


def set_autonomous(fd, on):
    set_feature(fd, struct.pack("<BB", R_CTRL, 1 if on else 0))


def fill(fd, count, r, g, b, intensity=255):
    set_feature(fd, struct.pack("<BBHHBBBB", R_RANGE, UPDATE_COMPLETE, 0, count - 1, r, g, b, intensity))


def scale(v, pct):
    return max(0, min(255, round(v * pct / 100)))


def show(fd, count, rgb, pct):
    # The lamp has one intensity level, so brightness is applied by scaling RGB.
    set_autonomous(fd, False)
    fill(fd, count, *(scale(v, pct) for v in rgb), scale(255, pct))


def load_state():
    try:
        with open(STATE) as f:
            s = json.load(f)
        return (int(s["r"]), int(s["g"]), int(s["b"])), float(s["brightness"])
    except (OSError, ValueError, KeyError):
        return (255, 255, 255), 100.0


def save_state(rgb, pct):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(dict(r=rgb[0], g=rgb[1], b=rgb[2], brightness=pct), f)


def parse_brightness(text, current):
    value = current + float(text) if text.startswith(("+", "-")) else float(text)
    return max(0.0, min(100.0, value))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    c = sub.add_parser("color")
    c.add_argument("r", type=int)
    c.add_argument("g", type=int)
    c.add_argument("b", type=int)
    c.add_argument("-b", "--brightness", type=float, default=None, help="percent, default: current level")
    br = sub.add_parser("brightness")
    br.add_argument("value", help="0-100, or +N / -N to step")
    sub.add_parser("restore")
    sub.add_parser("off")
    a = sub.add_parser("auto")
    a.add_argument("state", choices=["on", "off"])
    sub.add_parser("test")
    args = p.parse_args()

    path = find_device()
    try:
        fd = os.open(path, os.O_RDWR)
    except PermissionError:
        sys.exit(f"{path}: permission denied, run with sudo or install the udev rule")
    attrs = attributes(fd)
    n = attrs["count"]

    if args.cmd == "info":
        print(f"{path}: {attrs}")
        for i in range(min(n, 8)):
            print(lamp_attributes(fd, i))
        if n > 8:
            print(f"... {n - 8} more lamps")
    elif args.cmd == "color":
        _, pct = load_state()
        rgb = tuple(max(0, min(255, v)) for v in (args.r, args.g, args.b))
        pct = pct if args.brightness is None else max(0.0, min(100.0, args.brightness))
        show(fd, n, rgb, pct)
        save_state(rgb, pct)
    elif args.cmd == "brightness":
        rgb, pct = load_state()
        pct = parse_brightness(args.value, pct)
        show(fd, n, rgb, pct)
        save_state(rgb, pct)
    elif args.cmd == "restore":
        show(fd, n, *load_state())
    elif args.cmd == "off":
        show(fd, n, (0, 0, 0), 0)
    elif args.cmd == "auto":
        set_autonomous(fd, args.state == "on")
    elif args.cmd == "test":
        set_autonomous(fd, False)
        try:
            for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                fill(fd, n, *rgb)
                time.sleep(1.5)
        finally:
            set_autonomous(fd, True)


if __name__ == "__main__":
    main()
