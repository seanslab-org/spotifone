# Spotifone C Daemons
# Build on Car Thing (aarch64 Debian Bullseye):
#   make
# Cross-compile from host (if toolchain available):
#   make CC=aarch64-linux-gnu-gcc

CC      ?= gcc
CFLAGS  ?= -Wall -Wextra -O2
LDFLAGS ?=

TARGETS = gattd mic_bridge

all: $(TARGETS)

gattd: gattd.c protocol.h
	$(CC) $(CFLAGS) -o $@ gattd.c $(LDFLAGS)

mic_bridge: mic_bridge.c protocol.h
	$(CC) $(CFLAGS) -o $@ mic_bridge.c $(LDFLAGS)

clean:
	rm -f $(TARGETS)

.PHONY: all clean
