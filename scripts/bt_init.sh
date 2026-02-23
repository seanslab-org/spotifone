#!/bin/sh
# Spotifone Bluetooth Initialization
# Resets BCM4345C0 via GPIO, attaches UART, starts BlueZ stack,
# and launches mic_bridge + C HID/button daemons.
#
# Usage: /scripts/bt_init.sh
# Runs as systemd service (bt-init.service)

LOG=/tmp/bt_init.log
exec > "$LOG" 2>&1
echo "$(date): bt_init.sh starting"

# Fixed public address for Spotifone identity on host devices.
BT_PUBLIC_ADDR="30:E3:D6:05:AA:CE"
# Keyboard-first validation mode:
# - Disable mic/HFP startup
# - Skip auto-reconnect loop
# - Present as keyboard-focused identity for pairing
KEYBOARD_FIRST=0

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

# Step 2b: Set public Bluetooth MAC address
# Keep a stable, explicit identity for macOS pairing cache behavior.
if btmgmt --index 0 public-addr "$BT_PUBLIC_ADDR" > /dev/null 2>&1; then
    echo "hci0 public address set to $BT_PUBLIC_ADDR"
else
    echo "WARNING: failed to set hci0 public address to $BT_PUBLIC_ADDR"
fi

# Step 3: Bring adapter up
hciconfig hci0 up
if [ "$KEYBOARD_FIRST" = "1" ]; then
    hciconfig hci0 class 0x002540
    echo "hci0 configured: class=keyboard (0x002540)"
else
    # Device class 0x240404 = Audio (Major Service: Audio) + Audio/Video (Major Class) + Headset (Minor)
    hciconfig hci0 class 0x240404
    echo "hci0 configured: class=audio-headset (0x240404)"
fi

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

# Step 5: Start bluetoothd with SDP compat mode
# -C = SDP compat (required for external ProfileManager1 SDP registration)
if pgrep -x bluetoothd > /dev/null; then
    echo "bluetoothd already running"
else
    if [ "$KEYBOARD_FIRST" = "1" ]; then
        # Disable built-in input + media plugins so external HID profile owns input.
        start-stop-daemon --start --background --pidfile /var/run/bluetoothd.pid --make-pidfile --exec /usr/sbin/bluetoothd -- -n -C -P input,a2dp,avrcp
    else
        start-stop-daemon --start --background --pidfile /var/run/bluetoothd.pid --make-pidfile --exec /usr/sbin/bluetoothd -- -n -C -P input
    fi
    sleep 2
    if [ "$KEYBOARD_FIRST" = "1" ]; then
        echo "bluetoothd started (-C -P input,a2dp,avrcp)"
    else
        echo "bluetoothd started (-C -P input)"
    fi
fi

# Step 5b: Set IO capability for pairing
if [ "$KEYBOARD_FIRST" = "1" ]; then
    btmgmt --index 0 io-cap 3
    echo "btmgmt: io-cap=NoInputNoOutput"
else
    btmgmt --index 0 io-cap 0
    echo "btmgmt: io-cap=DisplayOnly"
fi
btmgmt --index 0 bredr on > /dev/null 2>&1 || true
if [ "$KEYBOARD_FIRST" = "1" ]; then
    btmgmt --index 0 le off > /dev/null 2>&1 || true
    echo "btmgmt: BR/EDR on, LE off (keyboard-first)"
else
    btmgmt --index 0 le on > /dev/null 2>&1 || true
fi

# Wait for BlueZ adapter object to be ready before property sets.
ADAPTER_READY=0
for _i in $(seq 1 20); do
    if dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 \
        org.freedesktop.DBus.Properties.Get string:'org.bluez.Adapter1' \
        string:'Address' >/dev/null 2>&1; then
        ADAPTER_READY=1
        break
    fi
    sleep 0.5
done
if [ "$ADAPTER_READY" -ne 1 ]; then
    echo "WARNING: /org/bluez/hci0 not ready before adapter configuration"
fi

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

if [ "$KEYBOARD_FIRST" = "1" ]; then
    hciconfig hci0 class 0x002540
    echo "keyboard-first class applied (0x002540)"
fi

# Step 6b: Configure ALSA mixer for PDM microphone
# Audio In Source must be set to PDMIN (item 4) — without this, ALSA capture
# reads from an invalid source and produces noise instead of mic audio
amixer -c 0 cset name='Audio In Source' 4 > /dev/null 2>&1
echo "ALSA Audio In Source set to PDMIN"

# Step 7: Start mic_bridge (HFP audio daemon)
# mic_bridge registers HFP-HF profile, handles SCO audio, and sends Cypress vendor cmd
if [ "$KEYBOARD_FIRST" = "1" ]; then
    start-stop-daemon --stop --pidfile /var/run/spotifone_mic.pid --retry 1 > /dev/null 2>&1 || true
    pkill -f '/opt/spotifone/daemon/mic_bridge' 2>/dev/null || true
    rm -f /var/run/spotifone_mic.pid
    echo "keyboard-first: mic_bridge disabled"
else
    if [ -x /opt/spotifone/daemon/mic_bridge ]; then
        start-stop-daemon --start --background \
            --pidfile /var/run/spotifone_mic.pid --make-pidfile \
            --startas /bin/sh -- -c '/opt/spotifone/daemon/mic_bridge > /tmp/mic_bridge.log 2>&1'
        sleep 2
        echo "mic_bridge started"
    else
        echo "WARNING: mic_bridge binary not found at /opt/spotifone/daemon/mic_bridge"
    fi
fi

# Step 8: Start C HID keyboard daemon
# Disable old Python listeners first, then launch native C HID daemon.
start-stop-daemon --stop --pidfile /var/run/spotifone.pid --retry 1 > /dev/null 2>&1 || true
pkill -f '/opt/spotifone/daemon/hid_keyboard' 2>/dev/null || true
pkill -f '/opt/spotifone/src/hid_keyboard.py' 2>/dev/null || true
pkill -f '/opt/spotifone/src/run_all.py' 2>/dev/null || true
rm -f /var/run/spotifone.pid /tmp/spotifone_hid.sock

if [ -x /opt/spotifone/daemon/hid_keyboard ]; then
    start-stop-daemon --start --background \
        --pidfile /var/run/spotifone.pid --make-pidfile \
        --startas /bin/sh -- -c '/opt/spotifone/daemon/hid_keyboard > /tmp/hid_keyboard.log 2>&1'
    sleep 2
    echo "C HID keyboard daemon started"
else
    echo "WARNING: hid_keyboard binary not found at /opt/spotifone/daemon/hid_keyboard"
fi

# Step 9: Start C button listener daemon
start-stop-daemon --stop --pidfile /var/run/spotifone_btn.pid --retry 1 > /dev/null 2>&1 || true
pkill -f '/opt/spotifone/daemon/button_listener' 2>/dev/null || true
pkill -f '/opt/spotifone/src/button_listener.py' 2>/dev/null || true
rm -f /var/run/spotifone_btn.pid

if [ -x /opt/spotifone/daemon/button_listener ]; then
    start-stop-daemon --start --background \
        --pidfile /var/run/spotifone_btn.pid --make-pidfile \
        --startas /bin/sh -- -c '/opt/spotifone/daemon/button_listener > /tmp/button.log 2>&1'
    echo "C button listener daemon started"
else
    echo "WARNING: button_listener binary not found at /opt/spotifone/daemon/button_listener"
fi

# Keep Classic discoverability/page scan enabled for HFP discovery and reconnect.
hciconfig hci0 piscan
echo "Classic BT discoverable (piscan)"

# Some BlueZ paths may alter CoD during profile registration; enforce final class.
if [ "$KEYBOARD_FIRST" = "1" ]; then
    hciconfig hci0 class 0x002540
    echo "keyboard-first: final class enforced to 0x002540"
fi

# Step 10: Auto-reconnect to previously paired devices (background with retries)
# As an HFP headset, we initiate reconnection on boot. macOS does NOT auto-page
# generic HFP devices — we must actively page the Mac. macOS page scan is
# infrequent (~1.28s window every ~10s), so we need many retries over minutes.
# Steps: set Trusted, increase page timeout, then retry Device1.Connect.
if [ "$KEYBOARD_FIRST" != "1" ]; then
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
else
    echo "keyboard-first: auto-reconnect loop disabled"
fi

echo "$(date): bt_init.sh complete"

# Keep btattach running (it maintains the UART connection)
wait $BTATTACH_PID
