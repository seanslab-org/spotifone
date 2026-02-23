#!/bin/sh
# Spotifone Bluetooth Initialization
# Resets BCM4345C0 via GPIO, attaches UART, starts BlueZ stack,
# launches mic_bridge (HFP audio) and button_listener (PTT).
#
# Usage: /scripts/bt_init.sh
# Runs as systemd service (bt-init.service)

LOG=/tmp/bt_init.log
exec > "$LOG" 2>&1
echo "$(date): bt_init.sh starting"

# Step 1: GPIO reset BCM4345C0 (GPIOX_17 = 493)
GPIOX_17=493
if [ ! -f /sys/class/gpio/gpio${GPIOX_17}/direction ]; then
    echo "${GPIOX_17}" > /sys/class/gpio/export
    echo out > /sys/class/gpio/gpio${GPIOX_17}/direction
    sleep 0.5
fi
echo 0 > /sys/class/gpio/gpio${GPIOX_17}/value
sleep 0.5
echo 1 > /sys/class/gpio/gpio${GPIOX_17}/value
sleep 1
echo "GPIO reset done"

# Step 2: Attach UART with BCM firmware upload
# btattach -P bcm uploads Cypress firmware (HCI 4.2→5.2)
/usr/bin/btattach -P bcm -B /dev/ttyS1 &
BTATTACH_PID=$!
sleep 5

# Verify HCI adapter appeared
if hciconfig hci0 > /dev/null 2>&1; then
    echo "hci0 created (firmware loaded)"
else
    echo "ERROR: hci0 not found after btattach"
    exit 1
fi

# Step 3: Bring adapter up
# Device class 0x240404 = Audio (Major Service: Audio) + Audio/Video (Major Class) + Headset (Minor)
# This makes macOS show Spotifone as an audio device
hciconfig hci0 up
hciconfig hci0 class 0x240404
echo "hci0 configured: class=audio-headset (0x240404)"

# Step 4: Start D-Bus if not running
if [ ! -S /run/dbus/system_bus_socket ] || ! pgrep -x dbus-daemon > /dev/null; then
    mkdir -p /run/dbus
    rm -f /run/dbus/pid
    dbus-daemon --system
    sleep 1
    echo "D-Bus started"
else
    echo "D-Bus already running"
fi

# Step 5: Start bluetoothd with SDP compat mode + input plugin disabled
# -C = SDP compat (required for ProfileManager1 SDP registration)
# -P input = disable BlueZ input plugin (frees L2CAP PSM 17/19 for our HID server)
if pgrep -x bluetoothd > /dev/null; then
    echo "bluetoothd already running"
else
    start-stop-daemon --start --background --pidfile /var/run/bluetoothd.pid --make-pidfile --exec /usr/sbin/bluetoothd -- -n -C -P input
    sleep 2
    echo "bluetoothd started (-C -P input)"
fi

# Step 5b: Set IO capability for pairing
btmgmt --index 0 io-cap 1
echo "btmgmt: io-cap=DisplayOnly"

# Step 6: Configure adapter via D-Bus
dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 \
    org.freedesktop.DBus.Properties.Set string:'org.bluez.Adapter1' \
    string:'Alias' variant:string:'Spotifone'

dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 \
    org.freedesktop.DBus.Properties.Set string:'org.bluez.Adapter1' \
    string:'Discoverable' variant:boolean:true

dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 \
    org.freedesktop.DBus.Properties.Set string:'org.bluez.Adapter1' \
    string:'DiscoverableTimeout' variant:uint32:0

dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 \
    org.freedesktop.DBus.Properties.Set string:'org.bluez.Adapter1' \
    string:'Pairable' variant:boolean:true

echo "Adapter configured as Spotifone"

# Step 6b: Configure ALSA mixer for PDM microphone
# Audio In Source must be set to PDMIN (item 4) — without this, ALSA capture
# reads from an invalid source and produces noise instead of mic audio
amixer -c 0 cset name='Audio In Source' 4 > /dev/null 2>&1
echo "ALSA Audio In Source set to PDMIN"

# Step 7: Start mic_bridge (HFP audio daemon)
# mic_bridge registers HFP-HF profile, handles SCO audio, and sends Cypress vendor cmd
if [ -x /opt/spotifone/daemon/mic_bridge ]; then
    start-stop-daemon --start --background \
        --pidfile /var/run/spotifone_mic.pid --make-pidfile \
        --startas /bin/sh -- -c '/opt/spotifone/daemon/mic_bridge > /tmp/mic_bridge.log 2>&1'
    sleep 2
    echo "mic_bridge started"
else
    echo "WARNING: mic_bridge binary not found at /opt/spotifone/daemon/mic_bridge"
fi

# Step 8: Start Classic BT HID keyboard server + pairing agent
# Replaces BLE HOGP (run_all.py) with Classic BT HID (hid_keyboard.py)
# Registers HID SDP via ProfileManager1, listens on L2CAP PSM 17+19
start-stop-daemon --start --background \
    --pidfile /var/run/spotifone.pid --make-pidfile \
    --startas /bin/sh -- -c 'python3 /opt/spotifone/src/hid_keyboard.py > /tmp/spotifone.log 2>&1'
sleep 3
echo "HID keyboard server started"

# Enable Classic BT discoverability (needed for HID discovery via SDP)
hciconfig hci0 piscan
echo "Classic BT discoverable (piscan)"

# Step 9: Start button listener (button #1 → PTT + HID)
start-stop-daemon --start --background \
    --pidfile /var/run/spotifone_btn.pid --make-pidfile \
    --startas /bin/sh -- -c 'python3 /opt/spotifone/src/button_listener.py > /tmp/button.log 2>&1'
echo "Button listener started"

# Step 10: Auto-reconnect to previously paired devices (background with retries)
# As an HFP headset, we initiate reconnection on boot. macOS does NOT auto-page
# generic HFP devices — we must actively page the Mac. macOS page scan is
# infrequent (~1.28s window every ~10s), so we need many retries over minutes.
# Steps: set Trusted, increase page timeout, then retry Device1.Connect.
ADAPTER_DIR="/var/lib/bluetooth/$(hciconfig hci0 2>/dev/null | grep 'BD Address' | awk '{print $3}')"
(
    sleep 3  # let mic_bridge finish profile registration

    # Increase HCI page timeout (slots of 0.625ms; 16384 = ~10s scan window)
    hciconfig hci0 pageto 16384 2>/dev/null

    if [ -d "$ADAPTER_DIR" ]; then
        for dev_dir in "$ADAPTER_DIR"/*/info; do
            [ -f "$dev_dir" ] || continue
            grep -q '\[LinkKey\]' "$dev_dir" || continue
            DEV_MAC=$(basename "$(dirname "$dev_dir")")
            DEV_DBUS=$(echo "$DEV_MAC" | tr ':' '_')
            DEV_PATH="/org/bluez/hci0/dev_${DEV_DBUS}"
            DEV_NAME=$(grep '^Name=' "$dev_dir" | cut -d= -f2)

            # Mark device as trusted (BlueZ auto-accepts incoming connections)
            dbus-send --system --print-reply --dest=org.bluez "$DEV_PATH" \
                org.freedesktop.DBus.Properties.Set \
                string:'org.bluez.Device1' string:'Trusted' \
                variant:boolean:true >/dev/null 2>&1
            echo "Set ${DEV_NAME:-$DEV_MAC} as trusted"

            ATTEMPT=1
            while [ "$ATTEMPT" -le 30 ]; do
                # Check if already connected (Mac may have connected to us)
                CONNECTED=$(dbus-send --system --print-reply --dest=org.bluez "$DEV_PATH" \
                    org.freedesktop.DBus.Properties.Get \
                    string:'org.bluez.Device1' string:'Connected' 2>/dev/null | grep boolean | awk '{print $3}')
                if [ "$CONNECTED" = "true" ]; then
                    echo "Already connected to ${DEV_NAME:-$DEV_MAC}"
                    break
                fi

                echo "Reconnect attempt $ATTEMPT to ${DEV_NAME:-$DEV_MAC}"
                if dbus-send --system --print-reply --dest=org.bluez "$DEV_PATH" \
                    org.bluez.Device1.Connect >/dev/null 2>&1; then
                    echo "Connected to ${DEV_NAME:-$DEV_MAC}"
                    break
                fi
                ATTEMPT=$((ATTEMPT + 1))
                sleep 10
            done
        done
    fi
) >> /tmp/bt_init.log 2>&1 &

echo "$(date): bt_init.sh complete"

# Keep btattach running (it maintains the UART connection)
wait $BTATTACH_PID
