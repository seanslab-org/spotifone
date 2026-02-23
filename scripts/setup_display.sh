#!/bin/sh
# Spotifone Display Setup
# Disables fbcon and writes pre-converted logo to framebuffer.
#
# Usage: /opt/spotifone/scripts/setup_display.sh

LOGO_FB="/opt/spotifone/logo.fb"

if [ ! -f "$LOGO_FB" ]; then
    echo "WARNING: $LOGO_FB not found, skipping display setup"
    exit 0
fi

# Unbind fbcon so console text doesn't overwrite the logo
if [ -f /sys/class/vtconsole/vtcon1/bind ]; then
    echo 0 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null
fi

# Hide cursor
echo -e '\033[?25l' > /dev/tty1 2>/dev/null

# Write logo to framebuffer
cat "$LOGO_FB" > /dev/fb0
echo "Display: Spotifone logo written to /dev/fb0"
