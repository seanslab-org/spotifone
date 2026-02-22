"""
Spotifone — CLI Entry Point

Spotify Car Thing → Bluetooth Mic + Keyboard

On Linux (Car Thing): uses real hardware (input events, BlueZ, ALSA)
On macOS: runs with mock hardware for development/testing
"""

import argparse
import logging
import os
import platform
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from service import SpotifoneService


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )


def main():
    parser = argparse.ArgumentParser(description='Spotifone — Bluetooth Mic + Keyboard')
    parser.add_argument('--platform', choices=['mac', 'windows'], default='mac',
                        help='Target host platform')
    parser.add_argument('--device', help='Bluetooth device address (AA:BB:CC:DD:EE:FF)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Debug logging')
    parser.add_argument('--no-hardware', action='store_true',
                        help='Skip real hardware (for dev/testing on macOS)')
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    is_linux = platform.system() == 'Linux'
    use_hw = is_linux and not args.no_hardware

    logger.info(f"Starting Spotifone (platform={args.platform}, hardware={use_hw})")

    svc = SpotifoneService(platform=args.platform)
    input_reader = None

    # Graceful shutdown
    def on_signal(signum, frame):
        logger.info("Shutdown signal received")
        if input_reader:
            input_reader.stop()
        svc.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Wire real hardware
    if use_hw:
        from hardware import InputEventReader, BlueZHIDClient, ALSAMic, BUTTON_NAME_TO_ID

        def on_button(name, pressed):
            bid = BUTTON_NAME_TO_ID.get(name)
            if bid is not None:
                svc.buttons.on_event(bid, pressed)

        input_reader = InputEventReader(button_callback=on_button)

        hid_client = BlueZHIDClient()
        hid_client.setup()
        svc.hid.set_client(hid_client)

        mic = ALSAMic()
        mic.find_source()

        logger.info("Hardware initialized")
    else:
        logger.info("Running without hardware")

    if not svc.start():
        logger.error("Failed to start Spotifone")
        return 1

    if input_reader:
        input_reader.start()

    if args.device:
        if svc.pair(args.device):
            logger.info(f"Paired with {args.device}")
        else:
            logger.warning(f"Failed to pair with {args.device}")

    logger.info("Spotifone running — press Ctrl+C to stop")

    try:
        while svc.running:
            time.sleep(1)
    except KeyboardInterrupt:
        if input_reader:
            input_reader.stop()
        svc.stop()

    return 0


if __name__ == '__main__':
    sys.exit(main())
