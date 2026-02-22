#!/bin/bash
# Deploy Spotifone to Car Thing via USB (CDC ECM network)
#
# Usage: ./deploy.sh [device_ip]
# Default IP: 172.16.42.2 (USB gadget CDC ECM)

set -e

DEVICE_IP="${1:-172.16.42.2}"
DEVICE_USER="root"
REMOTE_DIR="/opt/spotifone"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Deploying Spotifone to ${DEVICE_USER}@${DEVICE_IP}:${REMOTE_DIR}"

# Create remote directory
ssh "${DEVICE_USER}@${DEVICE_IP}" "mkdir -p ${REMOTE_DIR}/{src,daemon}"

# Sync Python source
scp -r "${PROJECT_DIR}/src/"*.py "${DEVICE_USER}@${DEVICE_IP}:${REMOTE_DIR}/src/"

# Sync systemd unit
scp "${SCRIPT_DIR}/spotifone.service" "${DEVICE_USER}@${DEVICE_IP}:/etc/systemd/system/"

# Build C daemons on device (if source changed)
scp "${PROJECT_DIR}/daemon/"*.{c,h} "${PROJECT_DIR}/daemon/Makefile" \
    "${DEVICE_USER}@${DEVICE_IP}:${REMOTE_DIR}/daemon/"
ssh "${DEVICE_USER}@${DEVICE_IP}" "cd ${REMOTE_DIR}/daemon && make"

# Install and enable service
ssh "${DEVICE_USER}@${DEVICE_IP}" "systemctl daemon-reload && systemctl enable spotifone"

echo "Deploy complete. Start with: ssh ${DEVICE_USER}@${DEVICE_IP} systemctl start spotifone"
