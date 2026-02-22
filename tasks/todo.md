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
- [ ] Deploy and test button detection
- [ ] Deploy and test BLE HID pairing
- [ ] Deploy and test audio bridging
- [ ] Implement full C daemon logic (gattd, mic_bridge)

## Acceptance Criteria
- All unit tests pass (`python3 -m pytest tests/ -v`)
- Clean module imports (tests import from src/, not from other test files)
- Hardware abstraction fully mockable
- Single orchestrator pattern (service.py)

## Review
- Tests run: 86
- Outcomes: 86 passed, 0 failed (pytest 9.0.2, Python 3.14.3)
- Known limitations: C daemons are structural stubs (IPC + signal handling only, no BlueZ/ALSA integration yet).
- Device access: ADB over USB (serial 12345678). SSH not yet working (sshd privilege separation issue).
- Boot mode: Debian is default boot. Stock Buildroot available via burn mode + env override.
- USB gadget: RNDIS + ADB. macOS RNDIS doesn't work (inactive link), but ADB works fine.
- adbd on Debian: stock armhf binary with armhf compat libs copied from Buildroot.
