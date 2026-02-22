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
adb -s "$SERIAL" shell "mkdir -p ${REMOTE_DIR}/src ${REMOTE_DIR}/daemon"

# Push Python source
adb -s "$SERIAL" push "${PROJECT_DIR}/src/." "${REMOTE_DIR}/src/"

# Push systemd unit
adb -s "$SERIAL" push "${SCRIPT_DIR}/spotifone.service" "/etc/systemd/system/"

# Push C daemon source (build on device)
adb -s "$SERIAL" push "${PROJECT_DIR}/daemon/." "${REMOTE_DIR}/daemon/"

# Enable service
adb -s "$SERIAL" shell "systemctl daemon-reload && systemctl enable spotifone 2>/dev/null"

echo "Deploy complete."
echo "  Start:   adb -s $SERIAL shell systemctl start spotifone"
echo "  Status:  adb -s $SERIAL shell systemctl status spotifone"
echo "  Logs:    adb -s $SERIAL shell journalctl -u spotifone -f"
