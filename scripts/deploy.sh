#!/bin/bash
# Deploy Spotifone to Car Thing via ADB
#
# Usage: ./deploy.sh [adb_serial]
# Default serial: 12345678

set -e

SERIAL="${1:-12345678}"
REMOTE_DIR="/opt/spotifone"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Deploying Spotifone to device ${SERIAL}:${REMOTE_DIR}"

# Create remote directories
adb -s "$SERIAL" shell "mkdir -p ${REMOTE_DIR}/src ${REMOTE_DIR}/daemon ${REMOTE_DIR}/scripts"

# Push Python source
adb -s "$SERIAL" push "${PROJECT_DIR}/src/." "${REMOTE_DIR}/src/"

# Push systemd units
adb -s "$SERIAL" push "${SCRIPT_DIR}/spotifone.service" "/etc/systemd/system/" 2>/dev/null || true
adb -s "$SERIAL" push "${SCRIPT_DIR}/bt-init.service" "/etc/systemd/system/"

# Push BT init script
adb -s "$SERIAL" push "${SCRIPT_DIR}/bt_init.sh" "/scripts/bt_init.sh"
adb -s "$SERIAL" shell "chmod +x /scripts/bt_init.sh"

# Push D-Bus policy (allows mic_bridge to own org.spotifone.mic bus name)
adb -s "$SERIAL" push "${SCRIPT_DIR}/spotifone-dbus.conf" "/etc/dbus-1/system.d/spotifone.conf"

# Push C daemon source + build script (for on-device compilation)
adb -s "$SERIAL" push "${PROJECT_DIR}/daemon/." "${REMOTE_DIR}/daemon/"
adb -s "$SERIAL" push "${SCRIPT_DIR}/build_mic_bridge.sh" "${REMOTE_DIR}/scripts/build_mic_bridge.sh"
adb -s "$SERIAL" push "${SCRIPT_DIR}/build_hid_daemons.sh" "${REMOTE_DIR}/scripts/build_hid_daemons.sh"
adb -s "$SERIAL" shell "chmod +x ${REMOTE_DIR}/scripts/build_mic_bridge.sh"
adb -s "$SERIAL" shell "chmod +x ${REMOTE_DIR}/scripts/build_hid_daemons.sh"

# Push mic_bridge binary if it exists (pre-compiled)
if [ -f "${PROJECT_DIR}/daemon/mic_bridge" ]; then
    adb -s "$SERIAL" push "${PROJECT_DIR}/daemon/mic_bridge" "${REMOTE_DIR}/daemon/mic_bridge"
    adb -s "$SERIAL" shell "chmod +x ${REMOTE_DIR}/daemon/mic_bridge"
    echo "  mic_bridge binary deployed"
fi

# Push C HID binaries if they exist (pre-compiled)
if [ -f "${PROJECT_DIR}/daemon/hid_keyboard" ]; then
    adb -s "$SERIAL" push "${PROJECT_DIR}/daemon/hid_keyboard" "${REMOTE_DIR}/daemon/hid_keyboard"
    adb -s "$SERIAL" shell "chmod +x ${REMOTE_DIR}/daemon/hid_keyboard"
    echo "  hid_keyboard binary deployed"
fi

if [ -f "${PROJECT_DIR}/daemon/button_listener" ]; then
    adb -s "$SERIAL" push "${PROJECT_DIR}/daemon/button_listener" "${REMOTE_DIR}/daemon/button_listener"
    adb -s "$SERIAL" shell "chmod +x ${REMOTE_DIR}/daemon/button_listener"
    echo "  button_listener binary deployed"
fi

# Flush writes to disk (prevents 0-byte files after unclean reboot)
adb -s "$SERIAL" shell sync

echo ""
echo "Deploy complete."
echo "  BT init:    adb -s $SERIAL shell /scripts/bt_init.sh"
echo "  Build mic:  adb -s $SERIAL shell 'cd ${REMOTE_DIR} && scripts/build_mic_bridge.sh'"
echo "  Build HID:  adb -s $SERIAL shell 'cd ${REMOTE_DIR} && scripts/build_hid_daemons.sh'"
echo "  Mic bridge: adb -s $SERIAL shell ${REMOTE_DIR}/daemon/mic_bridge"
echo "  HID server: adb -s $SERIAL shell ${REMOTE_DIR}/daemon/hid_keyboard"
echo "  Buttons:    adb -s $SERIAL shell ${REMOTE_DIR}/daemon/button_listener"
