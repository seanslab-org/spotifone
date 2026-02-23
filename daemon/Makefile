# Spotifone C Daemons
# Build on Car Thing (aarch64 Debian Bullseye):
#   make
# Cross-compile from host (if toolchain available):
#   make CC=aarch64-linux-gnu-gcc

CC      ?= gcc
CFLAGS  ?= -Wall -Wextra -O2

# D-Bus flags (required for mic_bridge)
DBUS_CFLAGS := $(shell pkg-config --cflags dbus-1 2>/dev/null || echo "-I/usr/include/dbus-1.0 -I/usr/lib/dbus-1.0/include")
DBUS_LIBS   := $(shell pkg-config --libs dbus-1 2>/dev/null || echo "-ldbus-1")

# Bluetooth + threading
BT_LIBS     = -lbluetooth
THREAD_LIBS = -lpthread

TARGETS = gattd mic_bridge

all: $(TARGETS)

gattd: gattd.c protocol.h
	$(CC) $(CFLAGS) -o $@ gattd.c

SBC_LIBS    = -lsbc

mic_bridge: mic_bridge.c protocol.h
	$(CC) $(CFLAGS) $(DBUS_CFLAGS) -o $@ mic_bridge.c $(DBUS_LIBS) $(BT_LIBS) $(SBC_LIBS) $(THREAD_LIBS)

clean:
	rm -f $(TARGETS)

.PHONY: all clean
