#!/bin/sh
# Test BlueALSA as alternative audio transport for HFP
#
# This script tests whether BlueALSA can handle HFP audio instead of
# our custom mic_bridge SCO socket approach. BlueALSA handles codec
# negotiation, format conversion, and SCO transport automatically.
#
# Prerequisites:
#   - BT initialized (bt_init.sh steps 1-6 done)
#   - bluetoothd running with -C (SDP compat)
#   - Mac paired with Spotifone
#   - mic_bridge NOT running (would conflict)
#
# Usage: /opt/spotifone/scripts/test_bluealsa.sh

LOG=/tmp/bluealsa_test.log
exec > "$LOG" 2>&1
echo "$(date): BlueALSA test starting"

# Step 1: Check if bluealsa binary exists
if ! command -v bluealsa > /dev/null 2>&1; then
    echo "ERROR: bluealsa not found. Install with: apt-get install bluez-alsa-utils"
    exit 1
fi
echo "bluealsa found: $(which bluealsa)"

# Step 2: Kill any existing mic_bridge (would conflict on HFP profile)
pkill -f mic_bridge 2>/dev/null
sleep 1

# Step 3: Set ALSA mixer for PDM mic
amixer -c 0 cset name='Audio In Source' 4 > /dev/null 2>&1
echo "ALSA Audio In Source set to PDMIN"

# Step 4: Start bluealsa daemon with HFP-HF profile
# -p hfp-hf: Register as Hands-Free (device connects to Mac AG)
# -i hci0: Use the BCM4345C0 adapter
pkill -f bluealsa 2>/dev/null
sleep 1
bluealsa -p hfp-hf -i hci0 &
BLUEALSA_PID=$!
sleep 3

if kill -0 $BLUEALSA_PID 2>/dev/null; then
    echo "bluealsa started (PID=$BLUEALSA_PID)"
else
    echo "ERROR: bluealsa failed to start"
    exit 1
fi

# Step 5: List available BlueALSA devices
echo "--- BlueALSA devices ---"
bluealsa-aplay --list-devices 2>&1 || echo "(no devices yet — need BT connection)"
echo "---"

# Step 6: Wait for Mac to connect
echo "Waiting for Mac HFP connection..."
echo "On Mac: System Settings > Bluetooth > Spotifone > Connect"
echo "Then open System Settings > Sound > Input > select Spotifone"
echo ""
echo "Once connected, run this to stream mic audio:"
echo "  arecord -D plughw:0,0 -f S16_LE -r 8000 -c 1 -t raw | \\"
echo "    aplay -D bluealsa:DEV=XX:XX:XX:XX:XX:XX,PROFILE=sco -f S16_LE -r 8000 -c 1 -t raw"
echo ""
echo "Replace XX:XX:XX:XX:XX:XX with Mac's BT address."
echo "Find it with: bluealsa-aplay --list-devices"

# Keep running so bluealsa stays alive
echo "$(date): Waiting for connections (Ctrl+C to stop)..."
wait $BLUEALSA_PID
