#!/bin/sh
# Install the udev rule that gives the logged-in user access to the keyboard backlight.
set -e
rm -f /etc/udev/rules.d/99-gigabyte-kbd.rules
install -m 644 "$(dirname "$(readlink -f "$0")")/70-gigabyte-kbd.rules" /etc/udev/rules.d/
udevadm control --reload
udevadm trigger --subsystem-match=hidraw --action=change
sleep 1
ls -l /dev/hidraw*
