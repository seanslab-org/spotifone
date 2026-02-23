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

# Unbind all fbcon vtconsoles so console text doesn't overwrite the logo
for vt in /sys/class/vtconsole/vtcon*/bind; do
    [ -f "$vt" ] && echo 0 > "$vt" 2>/dev/null
done

# Hide cursor
echo -e '\033[?25l' > /dev/tty1 2>/dev/null

# Clear framebuffer to black, then write logo
dd if=/dev/zero of=/dev/fb0 bs=1440 count=800 2>/dev/null
cat "$LOGO_FB" > /dev/fb0
echo "Display: Spotifone logo written to /dev/fb0"
