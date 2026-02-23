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

# Step 5: Start bluetoothd with SDP compat mode
if pgrep -x bluetoothd > /dev/null; then
    echo "bluetoothd already running"
else
    start-stop-daemon --start --background --pidfile /var/run/bluetoothd.pid --make-pidfile --exec /usr/sbin/bluetoothd -- -n -C
    sleep 2
    echo "bluetoothd started"
fi

# Step 5b: Set IO capability and enable advertising
btmgmt --index 0 io-cap 1
btmgmt --index 0 advertising on
echo "btmgmt: io-cap=DisplayOnly, advertising=on"

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

# Step 8: Start GATT server + pairing agent (BLE HID — parked but keep running)
start-stop-daemon --start --background \
    --pidfile /var/run/spotifone.pid --make-pidfile \
    --startas /bin/sh -- -c 'python3 /opt/spotifone/src/run_all.py > /tmp/spotifone.log 2>&1'
sleep 3
echo "GATT server started"

# Step 9: Start button listener (button #1 → PTT + HID)
start-stop-daemon --start --background \
    --pidfile /var/run/spotifone_btn.pid --make-pidfile \
    --startas /bin/sh -- -c 'python3 /opt/spotifone/src/button_listener.py > /tmp/button.log 2>&1'
echo "Button listener started"

echo "$(date): bt_init.sh complete"

# Keep btattach running (it maintains the UART connection)
wait $BTATTACH_PID
