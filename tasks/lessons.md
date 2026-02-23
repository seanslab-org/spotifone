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

## From On-Device Integration (Phase 2)
11. **Python 3.9 doesn't support `str | None`** — Device has Python 3.9, PEP 604 union types need 3.10+. Use `Optional[str]` from typing.
12. **BCM4345C0 BT init requires GPIO reset** — GPIOX_17 (pin 493) must be toggled (low→high) before UART attach. Without reset, chip doesn't respond.
13. **Use `btattach -P bcm` not `hciattach`** — `hciattach bcm43xx` times out, `hciattach any` skips firmware. `btattach -P bcm -B /dev/ttyS1` is correct: uploads Cypress firmware, HCI 5.2.
14. **Device `/run` is ramfs, not tmpfs** — Reports 0 bytes free, causing systemd to refuse daemon-reload. Use `start-stop-daemon` to start services instead.
15. **ADB shell kills child processes on exit** — Background processes die when adb shell session ends. Use `start-stop-daemon` with `--background` for persistent daemons.
16. **D-Bus daemon needs restart after PID file stale** — Remove `/run/dbus/pid` before restarting dbus-daemon, or it refuses to start.
17. **Use `btattach -P bcm` not `hciattach any`** — `hciattach any` creates HCI adapter WITHOUT firmware (radio barely works, HCI 4.2). `btattach -P bcm -B /dev/ttyS1` uploads Cypress firmware properly (HCI 5.2, full BLE).
18. **macOS rejects NoInputNoOutput pairing for HID** — Must use `DisplayOnly` agent + `btmgmt io-cap 1` to trigger passkey-based pairing. "Just Works" (NoInputNoOutput) doesn't work for HID on macOS.
19. **Classic BT HID doesn't work on macOS** — sdptool KEYB registration isn't enough; needs actual L2CAP PSM listeners. Use BLE HOGP (GATT HID) instead.
20. **BLE-only requires `noscan`** — `hciconfig hci0 noscan` disables Classic BT scan modes. Without this, macOS sees the device via Classic BT and shows no Connect button (no recognized services).
21. **GATT encrypt flags break connections** — Adding `encrypt-read`/`encrypt-notify` flags to GATT characteristics prevents macOS from connecting at all. Use plain `read`/`notify` flags; encryption is handled at the link layer.
22. **btmgmt advertising must be enabled** — `btmgmt --index 0 advertising on` is required in addition to RegisterAdvertisement for BLE advertising to actually work on some BlueZ versions.

## From Audio Bridging (Phase 2b)
23. **Amlogic ALSA `Audio In Source` defaults to invalid** — The mixer control defaults to `0xFFFFFFFF` (no source). Must explicitly set to `PDMIN` (item 4) for PDM microphone capture. Without this, arecord captures noise/silence.
24. **MEMS PDM mics need digital gain** — PDM mics have low sensitivity (~-26 dBFS @94dB SPL). Raw capture is ~2-3% of full scale. Apply 30-32dB software gain (32x multiplier) before sending to SCO.
25. **SCO is bidirectional — must drain incoming data** — Mac sends audio to device even in HF role. If not drained, kernel SCO buffer overflows → "Connection reset by peer". Add `while(read(sco_fd, buf, sz) > 0)` in audio loop.
26. **BlueZ overwrites device class on profile registration** — `hciconfig hci0 class` gets overridden when bluetoothd registers profiles. Use `btmgmt --index 0 class <major> <minor>` for persistent class, or set class AFTER profile registration.
27. **D-Bus policy file required for custom bus names** — mic_bridge needs `/etc/dbus-1/system.d/spotifone.conf` to own `org.spotifone.mic`. Without it: "not allowed to own service".
28. **SCO voice=0x0060 IS 16-bit S16_LE** — voice=0x0060 = CVSD, 16-bit linear PCM. The BCM4345C0 with HCI SCO routing expects S16_LE at 8kHz (16,000 bytes/sec). The half-speed issue with default SO_SNDBUF was a kernel buffer throttling problem, not a format mismatch. Converting to 8-bit (`>>8`) produces wrong speed. S16_LE direct is correct.
29. **SCO drain must use MSG_DONTWAIT** — After making SCO socket blocking (for reliable writes), the drain `read()` loop blocks forever. Use `recv(sco_fd, buf, sz, MSG_DONTWAIT)` to drain non-blocking while keeping writes blocking.
30. **arecord pipe delivers in ALSA-period bursts, not steady stream** — ALSA delivers PCM in period-sized chunks (e.g. 1024 bytes every 64ms). fread() drains each burst instantly, then blocks. Without explicit pacing, SCO writes are bursty (16 packets in ~2ms, then 60ms gap), which overflows/underflows the BT controller's CVSD encoder FIFO. Fix: use `clock_nanosleep(TIMER_ABSTIME)` to pace one SCO packet per write period.
31. **fread() from pipe can return partial/odd bytes** — At ALSA period boundaries, fread() may return fewer bytes than requested, including odd byte counts. For S16_LE data, odd bytes corrupt sample alignment. Guard with `n &= ~(size_t)1` after every fread.
