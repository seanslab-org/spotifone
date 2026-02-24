# Spotifone — Task Tracker

## Phase 0: Flash Device
- [x] Install pyamlboot (venv at `/Users/seansong/seanslab/VibeThing/superbird-tool/.venv/`)
- [x] Flash Car Thing with Debian firmware via superbird-tool (all partitions, ~22 min)
- [x] Configure USB gadget service (RNDIS + ADB via armhf compat libs)
- [x] Configure SSH (keys + password auth)
- [x] Set U-Boot env to boot Debian by default (`pick_boot_slot=run boot_slot_debian;`)
- [x] Verify device boots to Debian, ADB accessible via USB

## Phase 1: Project Scaffolding & Core Architecture
- [x] Create directory structure (src/, tests/, daemon/, scripts/, tasks/)
- [x] Implement button.py + test_button.py — press/hold/click state machine
- [x] Implement hid.py + test_hid.py — HID key mapping and PTT events
- [x] Implement audio.py + test_audio.py — mute/unmute and PTT controller
- [x] Implement service.py + test_service.py — orchestrator wiring PTT → HID + audio
- [x] Implement hardware.py + test_hardware.py — Linux input events, BlueZ, ALSA
- [x] Implement main.py — CLI entry point with arg parsing
- [x] Create C daemon stubs (gattd.c, mic_bridge.c, protocol.h, Makefile)
- [x] Create deploy.sh and spotifone.service

## Phase 2: On-Device Integration
- [x] Flash device and boot Debian
- [x] Deploy Python source code to device (ADB push)
- [x] Fix Python 3.9 compatibility (Optional[str] instead of str | None)
- [x] Install BlueZ 5.55 + dependencies (offline dpkg)
- [x] Bring up BCM4345C0 Bluetooth (GPIO reset → hciattach → bluetoothd)
- [x] Configure adapter: name "Spotifone", class keyboard, discoverable
- [x] Hardware integration tests passing on device (5/5)
- [x] Created bt_init.sh boot script + bt-init.service
- [x] BLE HID GATT server (ble_hid_gatt.py) — Python D-Bus GATT with HID Service
- [x] BLE HID pairing with macOS (DisplayOnly agent + btmgmt io-cap 1)
- [x] Test keystroke delivery via GATT notification (Right Alt press/release confirmed)
- [x] Socket IPC for key event injection (/tmp/spotifone_hid.sock)
- [x] Updated bt_init.sh: btattach -P bcm (firmware), btmgmt io-cap/advertising, noscan
- [x] Wire physical button to send Right Alt (0xE6) via socket IPC (button_listener.py updated)
- **BLE HOGP approach abandoned** — macOS address caching, dual-mode conflicts, never delivered key events reliably. See lesson #33.

## Phase 2c: Classic BT HID Keyboard (replaces BLE HOGP)
- [x] Create hid_keyboard.py — Classic BT HID server (L2CAP PSM 17+19, SDP via ProfileManager1)
- [x] Modify bt_init.sh — `-P input` flag, start hid_keyboard.py instead of run_all.py, piscan
- [x] Update deploy.sh — add HID server usage hint
- [x] Update lessons.md — correct #19, add #33
- [ ] Test `-P input` doesn't break HFP audio (on device)
- [ ] Test L2CAP sockets bind to PSM 17+19 (on device)
- [ ] Test SDP record registration visible via sdptool browse (on device)
- [ ] Test Mac discovers HID profile on Spotifone (on Mac)
- [ ] Test pairing with HID profile (on Mac)
- [ ] Test HID report delivery — Right Alt press/release (on Mac)
- [ ] Test button-to-Mac end-to-end (physical button → Mac key event)
- [x] Test HFP + HID coexistence (both profiles work simultaneously)
  - [x] Fix: add `-P input` to combined-mode bluetoothd in bt_init.sh (was missing, BlueZ input plugin stole PSM 17/19)
  - [x] Fix: remove agent registration from hid_keyboard.c (dual-agent conflict with mic_bridge)
  - [x] Deploy and verify on device: reboot, check logs, re-pair, test both profiles — VERIFIED
- [ ] Boot-to-ready automation (bt_init.sh → hid_keyboard.py on device startup)

## Phase 2b: Audio Bridging (HFP Mic)
- [x] Port VibeThing bt_mic_bridge.c → daemon/mic_bridge.c (1258-line HFP-HF daemon)
- [x] Update Makefile with D-Bus/BlueZ/pthread link flags
- [x] Create scripts/build_mic_bridge.sh for on-device compilation
- [x] Update button_listener.py for dual PTT (HID + mic_bridge control)
- [x] Update bt_init.sh: device class 0x240404 (audio headset), mic_bridge startup
- [x] Update deploy.sh: push mic_bridge binary + build script
- [x] Install alsa-utils on device (24 .deb packages via ADB)
- [x] Test PDM microphone captures audio (confirmed signal, RMS>0)
- [x] Build mic_bridge on device (gcc, ARM aarch64 ELF)
- [x] Test mic_bridge daemon starts without errors
- [x] Mac discovers Spotifone as audio device (class 0x240404)
- [x] Mac pairs for HFP audio connection (full SLC handshake)
- [x] Mic input appears in Mac Sound > Input settings
- [x] Audio streams from Car Thing mic to Mac (ALSA PDMIN + 32x digital gain)
- [x] Fix SCO audio distortion (timing/sample analysis)
  - [x] Add timer-based write pacing (clock_nanosleep per SCO packet)
  - [x] Add S16_LE alignment guard for partial fread()
  - [x] Add enhanced write-rate diagnostics
  - [x] Deploy to device, build, test audio quality — VERIFIED CLEAN
- [ ] PTT button starts/stops streaming via control socket
- [ ] Boot-to-ready automation

## Phase 3: Display & Branding
- [x] Create `convert_logo.py` — extract mic icon from horizontal logo, render vertical 480×800 layout
- [x] Generate `logo.fb` (BGR888 for `/dev/fb0`) and `logo.png` (preview)
- [x] Create `setup_display.sh` — unbind fbcon, clear fb, write logo to `/dev/fb0`
- [x] Integrate into `bt_init.sh` Step 1b (display before BT bring-up)
- [x] Update `deploy.sh` to push display assets
- [x] Fix noisy framebuffer background (vtcon0 not vtcon1, add dd zero pre-clear)
- [x] Generate `bootup.bmp` (16-bit R5G6B5) for Amlogic boot logo partition
- [x] Flash boot logo to `/dev/logo` (replaces stock blue house)
- [ ] Reboot test: verify boot logo renders during U-Boot stage
- [ ] Full boot sequence test: boot logo → runtime framebuffer (seamless transition)

## Open Bugs
- [ ] **BUG: Runtime logo + background still shows color noise pixels** — fbcon unbind (vtcon0 glob) and `dd if=/dev/zero` pre-clear deployed but not fully effective. Noise persists in black areas around logo/text. Needs deeper investigation (possible double-buffer page flip, or another process writing to fb0 after setup_display.sh).
- [ ] **BUG: Boot logo still shows old Spotify logo** — R5G6B5 `bootup.bmp` generated and flashed to `/dev/logo` partition, but U-Boot still renders old image on reboot. Possible causes: U-Boot reads from a different partition slot, BMP header not accepted by Amlogic parser, or logo partition not the active one (check env `logo=` param).
- [x] **BUG: No BT auto-reconnect on boot** — Fixed: added `scripts/auto_reconnect.sh` (launched from bt_init.sh Step 11) that enumerates paired devices and calls `Device1.Connect` with retry logic. Also added `AutoConnect=true` to hid_keyboard.c RegisterProfile options.

## Acceptance Criteria
- All unit tests pass (`python3 -m pytest tests/ -v`)
- Clean module imports (tests import from src/, not from other test files)
- Hardware abstraction fully mockable
- Single orchestrator pattern (service.py)
- mic_bridge compiles and runs on device
- Mac can discover, pair, and use Spotifone as microphone

## Review
- Tests run: 86
- Outcomes: 86 passed, 0 failed (pytest 9.0.2, Python 3.14.3)
- Known limitations: C daemons are structural stubs (IPC + signal handling only, no BlueZ/ALSA integration yet).
- Device access: ADB over USB (serial 12345678). SSH not yet working (sshd privilege separation issue).
- Boot mode: Debian is default boot. Stock Buildroot available via burn mode + env override.
- USB gadget: RNDIS + ADB. macOS RNDIS doesn't work (inactive link), but ADB works fine.
- adbd on Debian: stock armhf binary with armhf compat libs copied from Buildroot.
