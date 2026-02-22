# Spotifone — Lessons Learned

## From VibeThing (predecessor project)
1. **Test files should NOT import from other test files** — VibeThing's test_vibething_service.py imported classes from test_button_handler.py etc. Tests should import from src/ modules only.
2. **Single orchestrator is cleaner** — VibeThing split logic across vibething.py, vibething_device.py, and ble_hid_device.py. Use one service.py orchestrator.
3. **C daemons need a shared protocol header** — VibeThing's gattd and mic_bridge had no shared IPC contract. Use protocol.h.
4. **Hardware abstraction must be mockable** — All hardware access goes through hardware.py with clear interfaces for testing.
