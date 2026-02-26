#!/bin/sh
# Spotifone Auto-Reconnect
# Connects to previously paired devices after BT stack is up.
# Launched from bt_init.sh as a background process.
#
# Uses Device1.Connect (all profiles at once) rather than per-profile
# ConnectProfile — BlueZ connects all registered profiles (HFP + HID)
# in a single call.

LOG=/tmp/auto_reconnect.log
MAX_RETRIES=5
RETRY_INTERVAL=10

log() {
    echo "$(date): $*" >> "$LOG"
}

log "auto_reconnect.sh starting"

# Wait for both daemons to be running (max 30s)
WAIT_MAX=30
WAITED=0
while [ "$WAITED" -lt "$WAIT_MAX" ]; do
    MIC_UP=0
    HID_UP=0
    pgrep -f '/opt/spotifone/daemon/mic_bridge' > /dev/null 2>&1 && MIC_UP=1
    pgrep -f '/opt/spotifone/daemon/hid_keyboard' > /dev/null 2>&1 && HID_UP=1

    if [ "$MIC_UP" -eq 1 ] && [ "$HID_UP" -eq 1 ]; then
        log "Both daemons ready (mic_bridge + hid_keyboard)"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
done

if [ "$WAITED" -ge "$WAIT_MAX" ]; then
    log "WARNING: Timed out waiting for daemons (mic=$MIC_UP hid=$HID_UP), proceeding anyway"
fi

# Determine adapter BD address and paired device directory
ADAPTER_ADDR=$(hciconfig hci0 2>/dev/null | grep 'BD Address' | awk '{print $3}')
if [ -z "$ADAPTER_ADDR" ]; then
    log "ERROR: hci0 not found, exiting"
    exit 1
fi

ADAPTER_DIR="/var/lib/bluetooth/${ADAPTER_ADDR}"
if [ ! -d "$ADAPTER_DIR" ]; then
    log "ERROR: No adapter directory at ${ADAPTER_DIR}, exiting"
    exit 1
fi

# Enumerate paired devices (those with [LinkKey] section = Classic BT paired)
PAIRED_DEVICES=""
for info_file in "$ADAPTER_DIR"/*/info; do
    [ -f "$info_file" ] || continue
    grep -q '\[LinkKey\]' "$info_file" || continue
    DEV_MAC=$(basename "$(dirname "$info_file")")
    PAIRED_DEVICES="${PAIRED_DEVICES} ${DEV_MAC}"
done

if [ -z "$PAIRED_DEVICES" ]; then
    log "No paired devices found, exiting"
    exit 0
fi

log "Paired devices:${PAIRED_DEVICES}"

# Attempt to connect each paired device
for DEV_MAC in $PAIRED_DEVICES; do
    DEV_DBUS=$(echo "$DEV_MAC" | tr ':' '_')
    DEV_PATH="/org/bluez/hci0/dev_${DEV_DBUS}"

    # Read device name from info file for logging
    DEV_INFO="$ADAPTER_DIR/$DEV_MAC/info"
    DEV_NAME=$(grep '^Name=' "$DEV_INFO" 2>/dev/null | cut -d= -f2)
    DEV_LABEL="${DEV_NAME:-$DEV_MAC}"

    ATTEMPT=1
    CONNECTED=0
    while [ "$ATTEMPT" -le "$MAX_RETRIES" ]; do
        log "Connecting to ${DEV_LABEL} (attempt ${ATTEMPT}/${MAX_RETRIES})"

        # Check if device object exists in BlueZ (it should for paired devices)
        RESULT=$(dbus-send --system --print-reply --dest=org.bluez "$DEV_PATH" \
            org.freedesktop.DBus.Properties.Get \
            string:'org.bluez.Device1' string:'Connected' 2>&1)

        if echo "$RESULT" | grep -q 'boolean true'; then
            log "${DEV_LABEL} already connected"
            CONNECTED=1

            # macOS may have auto-reconnected HFP only (not HID) — trigger HID connect
            # regardless so buttons work on first connection without a manual cycle.
            sleep 1
            python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
addr = bytes.fromhex(sys.argv[1].replace(':', ''))
s.sendto(b'\xff' + addr, '/tmp/spotifone_hid.sock')
" "$DEV_MAC" 2>/dev/null && log "${DEV_LABEL} HID connect triggered (already-connected path)" \
                           || log "${DEV_LABEL} HID connect IPC failed (non-fatal)"

            break
        fi

        # Attempt connection (timeout 15s via dbus-send default)
        CONN_RESULT=$(dbus-send --system --print-reply --dest=org.bluez "$DEV_PATH" \
            org.bluez.Device1.Connect 2>&1)

        if echo "$CONN_RESULT" | grep -q 'method return'; then
            log "${DEV_LABEL} connected successfully"
            CONNECTED=1

            # Trigger outbound HID connect — Device1.Connect only establishes
            # HFP (outbound RFCOMM). HID requires the device to connect L2CAP
            # to the host's PSM 17/19. Send [0xFF, bd_addr[6]] to hid_keyboard.
            sleep 1
            python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
addr = bytes.fromhex(sys.argv[1].replace(':', ''))
s.sendto(b'\xff' + addr, '/tmp/spotifone_hid.sock')
" "$DEV_MAC" 2>/dev/null && log "${DEV_LABEL} HID connect triggered" \
                           || log "${DEV_LABEL} HID connect IPC failed (non-fatal)"

            break
        else
            ERR_MSG=$(echo "$CONN_RESULT" | grep 'Error' | head -1)
            log "${DEV_LABEL} connect failed: ${ERR_MSG:-unknown error}"
        fi

        ATTEMPT=$((ATTEMPT + 1))
        if [ "$ATTEMPT" -le "$MAX_RETRIES" ]; then
            log "Retrying in ${RETRY_INTERVAL}s..."
            sleep "$RETRY_INTERVAL"
        fi
    done

    if [ "$CONNECTED" -eq 0 ]; then
        log "${DEV_LABEL} failed after ${MAX_RETRIES} attempts"
    fi
done

log "auto_reconnect.sh finished"
