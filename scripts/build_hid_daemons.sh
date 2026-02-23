#!/bin/sh
# Build Spotifone C HID daemons on Car Thing
#
# Outputs:
#   /opt/spotifone/daemon/hid_keyboard
#   /opt/spotifone/daemon/button_listener
#
# Usage:
#   ./build_hid_daemons.sh
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CC="${CC:-gcc}"
CFLAGS="-O2 -Wall -Wextra"

HID_SRC="$ROOT_DIR/../daemon/hid_keyboard.c"
HID_OUT="$ROOT_DIR/../daemon/hid_keyboard"
BTN_SRC="$ROOT_DIR/../daemon/button_listener.c"
BTN_OUT="$ROOT_DIR/../daemon/button_listener"

if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists dbus-1 2>/dev/null; then
    DBUS_CFLAGS="$(pkg-config --cflags dbus-1)"
    DBUS_LIBS="$(pkg-config --libs dbus-1)"
else
    DBUS_CFLAGS="-I/usr/include/dbus-1.0 -I/usr/lib/dbus-1.0/include"
    DBUS_LIBS="-ldbus-1"
fi

echo "Building $HID_OUT from $HID_SRC"
$CC $CFLAGS $DBUS_CFLAGS -o "$HID_OUT" "$HID_SRC" $DBUS_LIBS -lbluetooth

echo "Building $BTN_OUT from $BTN_SRC"
$CC $CFLAGS -o "$BTN_OUT" "$BTN_SRC"

echo "Built:"
echo "  $HID_OUT"
echo "  $BTN_OUT"
