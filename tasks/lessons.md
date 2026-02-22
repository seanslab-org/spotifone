# Spotifone — Lessons Learned

## From VibeThing (predecessor project)
1. **Test files should NOT import from other test files** — VibeThing's test_vibething_service.py imported classes from test_button_handler.py etc. Tests should import from src/ modules only.
2. **Single orchestrator is cleaner** — VibeThing split logic across vibething.py, vibething_device.py, and ble_hid_device.py. Use one service.py orchestrator.
3. **C daemons need a shared protocol header** — VibeThing's gattd and mic_bridge had no shared IPC contract. Use protocol.h.
4. **Hardware abstraction must be mockable** — All hardware access goes through hardware.py with clear interfaces for testing.

## From Device Bring-Up (Phase 0)
5. **macOS RNDIS is broken** — macOS creates the interface but link stays "inactive". Always use ADB or CDC ECM for USB networking from Mac.
6. **FunctionFS requires active userspace daemon** — Adding `ffs.adb` to USB gadget config requires `adbd` to open the endpoints BEFORE attaching UDC. Without it, the entire gadget fails.
7. **Stock Buildroot is armhf, Debian is aarch64** — Can't directly run stock binaries on Debian. Copy armhf compat libs (`/lib/arm-linux-gnueabihf/`) and the armhf dynamic linker.
8. **Never use `set -e` in boot scripts** — One failure in USB gadget setup kills everything. Use explicit error handling and logging instead.
9. **`--send_env` with `env save` is persistent** — Writes to eMMC env partition. Not session-only. To switch boot modes, must enter burn mode and resend env.
10. **Use `sed` carefully on device** — Shell variable expansion can corrupt files. Use single-quoted heredocs or echo for writing files via ADB.
