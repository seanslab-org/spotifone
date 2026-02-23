#!/bin/sh
# Build Spotifone mic_bridge on the Car Thing (aarch64 Debian Bullseye)
#
# Prerequisites (on device):
#   apt-get install gcc libbluetooth-dev libdbus-1-dev
#
# Usage:
#   ./build_mic_bridge.sh              # build from daemon/ source
#   CC=aarch64-linux-gnu-gcc ./build_mic_bridge.sh  # cross-compile
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT_DIR/../daemon/mic_bridge.c"
OUT="${OUT:-$ROOT_DIR/../daemon/mic_bridge}"

CC="${CC:-gcc}"
CFLAGS="-O2 -Wall -Wextra"

# D-Bus flags
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists dbus-1 2>/dev/null; then
    DBUS_CFLAGS="$(pkg-config --cflags dbus-1)"
    DBUS_LIBS="$(pkg-config --libs dbus-1)"
else
    DBUS_CFLAGS="-I/usr/include/dbus-1.0 -I/usr/lib/dbus-1.0/include"
    DBUS_LIBS="-ldbus-1"
fi

# Bluetooth flags
BT_LIBS="-lbluetooth"

echo "Building $OUT from $SRC"
$CC $CFLAGS $DBUS_CFLAGS -o "$OUT" "$SRC" $DBUS_LIBS $BT_LIBS -lsbc -lpthread
echo "Built $OUT"
