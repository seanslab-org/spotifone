# Spotifone

Repurpose a **Spotify Car Thing** into a dual-function Bluetooth device:

1. **Bluetooth Microphone** — HFP audio input for Mac and Windows
2. **Bluetooth Keyboard** — Sends Right Alt (Windows) / Right Option (Mac) via the round on-screen button as a push-to-talk trigger

Press the round button: your computer hears your voice and receives the keyboard shortcut simultaneously.

## Hardware Requirements

- **Spotify Car Thing** — Amlogic S905D2 SoC, aarch64, BCM4345C0 Bluetooth, onboard PDM microphone
- **USB cable** — USB-A to USB-C for flashing and ADB
- **Mac or Linux workstation** — for flashing firmware and deploying code

## Quick Start

If you already have a Car Thing running Debian with packages installed:

```bash
# 1. Deploy source to device
./scripts/deploy.sh

# 2. Build daemons on device
adb shell 'cd /opt/spotifone && ./scripts/build_mic_bridge.sh'
adb shell 'cd /opt/spotifone && ./scripts/build_hid_daemons.sh'

# 3. Enable boot service and reboot
adb shell 'systemctl enable bt-init.service'
adb shell reboot

# 4. Pair from your computer's Bluetooth settings — look for "Spotifone"
```

## Full Setup Guide

### Step 1: Flash Debian onto Car Thing

The Car Thing ships with a stock Buildroot image. You need to replace it with Debian Bullseye (aarch64).

**Host prerequisites (macOS):**

```bash
brew install python3 libusb android-platform-tools
python3 -m pip install git+https://github.com/superna9999/pyamlboot
```

**Host prerequisites (Linux):**

```bash
sudo apt install python3 libusb-1.0-0-dev android-tools-adb
sudo python3 -m pip install git+https://github.com/superna9999/pyamlboot
```

**Firmware:** Use the Debian Bullseye arm64 image from the [superbird-debian](https://github.com/bishopdynamics/superbird-debian-kiosk) project (or equivalent Car Thing Debian firmware).

**Flash:**

```bash
# Put Car Thing in USB Burn Mode:
#   1. Disconnect USB
#   2. Hold buttons 1 + 4 simultaneously
#   3. Plug in USB cable while holding
#   4. Release buttons

cd superbird-tool/
python3 superbird_tool.py --find_device        # Should find device in Burn Mode
python3 superbird_tool.py --burn_mode
python3 superbird_tool.py --restore_device /path/to/debian-firmware/
# Takes ~22 minutes

python3 superbird_tool.py --continue_boot
```

**Set Debian as default boot:**

```bash
# In U-Boot (via superbird_tool or UART console):
setenv pick_boot_slot 'run boot_slot_debian;'
env save
```

### Step 2: Verify ADB

After Debian boots, ADB should be available over USB:

```bash
adb devices
# Expected: 12345678    device

adb shell uname -a
# Expected: Linux ... aarch64 GNU/Linux
```

### Step 3: Install Device Packages

The Car Thing has no network. All packages must be transferred via ADB and installed with `dpkg`.

**Required packages** (Debian Bullseye arm64 `.deb` files):

| Category | Packages |
|----------|----------|
| **Bluetooth** | `bluez`, `bluez-firmware`, `libbluetooth3` |
| **D-Bus** | `dbus`, `libdbus-1-3` |
| **Audio** | `alsa-utils`, `libsbc1` |
| **Build tools** | `gcc`, `make`, `libc6-dev`, `libbluetooth-dev`, `libdbus-1-dev`, `libsbc-dev` |

Download these from [Debian packages](https://packages.debian.org/bullseye/) (select `arm64` architecture), then:

```bash
# Transfer to device
adb push *.deb /tmp/

# Install (order matters — install dependencies first)
adb shell 'dpkg -i /tmp/dbus*.deb /tmp/libbluetooth3*.deb /tmp/libdbus-1-3*.deb'
adb shell 'dpkg -i /tmp/bluez*.deb'
adb shell 'dpkg -i /tmp/alsa-utils*.deb /tmp/libsbc*.deb'
adb shell 'dpkg -i /tmp/gcc*.deb /tmp/make*.deb /tmp/libc6-dev*.deb'
adb shell 'dpkg -i /tmp/libbluetooth-dev*.deb /tmp/libdbus-1-dev*.deb /tmp/libsbc-dev*.deb'
adb shell 'dpkg --configure -a'

# Verify
adb shell 'bluetoothd --version && gcc --version | head -1 && which arecord'
```

### Step 4: Deploy Spotifone

```bash
cd spotifone/
./scripts/deploy.sh
```

This pushes to the device:
- Python source → `/opt/spotifone/src/`
- C daemon source → `/opt/spotifone/daemon/`
- Build scripts → `/opt/spotifone/scripts/`
- Boot script → `/scripts/bt_init.sh`
- systemd service → `/etc/systemd/system/bt-init.service`
- D-Bus policy → `/etc/dbus-1/system.d/spotifone.conf`

### Step 5: Build Daemons on Device

```bash
# Build HFP audio daemon (mic_bridge)
adb shell 'cd /opt/spotifone && ./scripts/build_mic_bridge.sh'

# Build HID keyboard + button listener
adb shell 'cd /opt/spotifone && ./scripts/build_hid_daemons.sh'

# Verify binaries
adb shell 'ls -la /opt/spotifone/daemon/mic_bridge /opt/spotifone/daemon/hid_keyboard /opt/spotifone/daemon/button_listener'
```

### Step 6: Enable Boot Service

```bash
adb shell 'systemctl enable bt-init.service'
adb shell reboot
```

After reboot, check logs:

```bash
adb shell 'cat /tmp/bt_init.log'        # Boot sequence
adb shell 'cat /tmp/mic_bridge.log'      # Audio daemon
adb shell 'cat /tmp/hid_keyboard.log'    # Keyboard daemon
adb shell 'cat /tmp/button.log'          # Button listener
```

Expected output from `bt_init.log`:
```
bluetoothd started (-C -P input)
mic_bridge started
C HID keyboard daemon started
C button listener daemon started
Classic BT discoverable (piscan)
```

### Step 7: Pair

1. Open Bluetooth settings on your Mac or Windows PC
2. Look for **"Spotifone"** in available devices
3. Click Connect/Pair
4. Accept the numeric confirmation if prompted
5. Spotifone appears as both an audio input device and a keyboard

**macOS alternative** (if "Connect" button doesn't appear in Settings):

```bash
brew install blueutil
blueutil --pair 30:E3:D6:05:AA:CE
```

## Usage

| Action | Result |
|--------|--------|
| **Press round button** | Right Alt/Option key DOWN + microphone starts streaming |
| **Release round button** | Right Alt/Option key UP + microphone stops streaming |
| **Press preset 1 button** | Sends keyboard key "9" |

The round button is a push-to-talk trigger: hold it to talk, release to stop. Your computer receives both the keyboard shortcut and the audio simultaneously.

## Architecture

Three C daemons run on the Car Thing, coordinated through Unix sockets:

```
┌─────────────────────┐
│   button_listener    │  Reads /dev/input/event0
│  (button_listener.c) │  Round button = code 1
└──────┬──────┬────────┘
       │      │
       │      │  2-byte [keycode, pressed]
       │      ▼
       │  ┌──────────────┐    L2CAP PSM 17+19
       │  │ hid_keyboard  │──────────────────────► Host (Mac/PC)
       │  │  (HID profile)│    HID reports          receives keys
       │  └──────────────┘
       │
       │  1-byte command (0x01=start, 0x00=stop)
       ▼
  ┌──────────────┐    SCO (8kHz S16_LE)
  │  mic_bridge   │──────────────────────────────► Host (Mac/PC)
  │ (HFP profile) │    Bluetooth audio              receives audio
  └──────────────┘
       ▲
       │  ALSA capture
  ┌──────────────┐
  │ PDM microphone│
  └──────────────┘
```

**Socket paths:**
- `/tmp/spotifone_hid.sock` — button_listener → hid_keyboard (key events)
- `/tmp/spotifone_mic.sock` — button_listener → mic_bridge (PTT control)

**Bluetooth profiles:**
- **HFP Hands-Free** (UUID `0x111E`) — mic_bridge registers via D-Bus ProfileManager1
- **HID** (UUID `0x1124`) — hid_keyboard registers via D-Bus ProfileManager1 with SDP record

**Pairing agent:** Only mic_bridge registers a BlueZ pairing agent (`NoInputNoOutput`). hid_keyboard does not register its own agent to avoid conflicts.

**BlueZ flags:** `bluetoothd -n -C -P input`
- `-C` — SDP compatibility mode (required for ProfileManager1)
- `-P input` — Disable built-in input plugin (frees L2CAP PSM 17/19 for our HID profile)

## Boot Sequence

`bt_init.sh` runs at boot via systemd (`bt-init.service` → `sysinit.target`):

1. GPIO reset BCM4345C0 (pin 493: low → high)
2. `btattach -P bcm -B /dev/ttyS1` — upload Cypress firmware (HCI 4.2 → 5.2)
3. Set public BD address (`30:E3:D6:05:AA:CE`)
4. Start D-Bus and bluetoothd
5. Configure adapter: name "Spotifone", class `0x240404` (audio headset), discoverable
6. Set ALSA mixer: "Audio In Source" → PDMIN (item 4)
7. Start mic_bridge (HFP audio, owns pairing agent)
8. Start hid_keyboard (Classic BT HID profile)
9. Start button_listener (GPIO → socket bridge)
10. Enable `piscan` (Classic BT discoverable + connectable)
11. Mark previously paired devices as trusted

## Project Structure

```
spotifone/
├── daemon/                     # C daemons (compiled on device)
│   ├── Makefile
│   ├── mic_bridge.c            # HFP audio: RFCOMM SLC + SCO streaming
│   ├── hid_keyboard.c          # Classic BT HID: SDP + L2CAP PSM 17/19
│   ├── button_listener.c       # GPIO input → IPC socket bridge
│   ├── protocol.h              # Shared IPC definitions
│   └── gattd.c                 # BLE GATT stub (unused)
│
├── src/                        # Python source (unit-testable logic)
│   ├── button.py               # Button state machine
│   ├── hid.py                  # HID key mapping
│   ├── audio.py                # Audio mute/unmute
│   ├── service.py              # Orchestrator
│   ├── hardware.py             # Hardware abstraction
│   └── main.py                 # CLI entry point
│
├── tests/                      # Unit tests (134 tests, pytest)
│   ├── test_button.py
│   ├── test_hid.py
│   ├── test_audio.py
│   ├── test_service.py
│   ├── test_hardware.py
│   └── test_hid_keyboard.py
│
├── scripts/
│   ├── deploy.sh               # Push everything to device via ADB
│   ├── bt_init.sh              # Bluetooth init (runs at boot)
│   ├── bt-init.service         # systemd unit for bt_init.sh
│   ├── build_mic_bridge.sh     # Build mic_bridge on device
│   ├── build_hid_daemons.sh    # Build hid_keyboard + button_listener
│   └── spotifone-dbus.conf     # D-Bus policy for org.spotifone.mic
│
└── tasks/
    ├── todo.md                 # Task tracker
    └── lessons.md              # 40 lessons learned
```

## Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# Single file
python3 -m pytest tests/test_hid_keyboard.py -v

# Single test
python3 -m pytest tests/test_button.py::test_press_release -v
```

Tests require Python 3.9+ and pytest. They mock all hardware — no device needed.

## Device Details

| Property | Value |
|----------|-------|
| SoC | Amlogic S905D2 (aarch64) |
| OS | Debian Bullseye |
| Kernel | 4.9.113 |
| Python | 3.9.2 |
| BT chip | BCM4345C0 (Cypress) |
| BT firmware | HCI 5.2 (via btattach -P bcm) |
| BD address | 30:E3:D6:05:AA:CE |
| Device class | 0x240404 (Audio Headset) |
| Microphone | PDM MEMS, ALSA hw:0,0 |
| Audio format | S16_LE, 8kHz mono |
| ADB serial | 12345678 |
| BT reset GPIO | GPIOX_17 (pin 493) |

## Key Lessons

Some critical gotchas from development (full list in `tasks/lessons.md`):

- **GPIO reset is required** before BT firmware upload — GPIOX_17 must toggle low→high
- **`btattach -P bcm`** not `hciattach` — only btattach uploads Cypress firmware correctly
- **`-P input` is mandatory** — BlueZ's input plugin steals L2CAP PSM 17/19 from our HID profile
- **One pairing agent only** — mic_bridge owns it; hid_keyboard must not register its own
- **ALSA "Audio In Source"** defaults to invalid — must explicitly set to PDMIN (item 4)
- **PDM mic needs 32x gain** — raw capture is ~2-3% of full scale
- **SCO write pacing** — must use `clock_nanosleep` per packet; ALSA delivers in bursts that overflow the BT FIFO
- **macOS BD address caching** — change address via `btmgmt public-addr` to appear as a fresh device
- **`/run` is ramfs on Car Thing** — use `start-stop-daemon`, not systemctl for daemon management

## Troubleshooting

**Device not showing in ADB:**
- Hold buttons 1+4 and replug USB to enter burn mode, then `--continue_boot`
- Check USB cable — some cables are charge-only

**bluetoothd won't start:**
- Check D-Bus: `pgrep dbus-daemon` — must be running first
- Check `/run/dbus/pid` — delete stale PID file if D-Bus crashed

**HID keyboard not receiving keys:**
- Verify `-P input` in bluetoothd args: `cat /tmp/bt_init.log | grep bluetoothd`
- Check PSM 19 binding: `cat /tmp/hid_keyboard.log` should show "Listening for HID interrupt on PSM 19"

**Audio distortion / choppy:**
- Verify ALSA source: `amixer -c 0 cget name='Audio In Source'` should be item 4
- Check SCO write rate in mic_bridge logs — should be ~16,000 bytes/sec steady

**macOS won't show "Connect" button:**
- Use `blueutil --pair <addr>` from Terminal as workaround
- Or: remove device from Mac, change BD address on device, re-pair

**macOS won't auto-reconnect after reboot:**
- This is a known macOS limitation for HFP headset-class devices
- Manually connect from Mac Bluetooth settings, or use `blueutil --connect <addr>`

## License

MIT License — see `LICENSE` file. Spotify Car Thing hardware modifications are at your own risk.
