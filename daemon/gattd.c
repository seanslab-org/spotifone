/*
 * Spotifone BLE GATT HID Server (gattd)
 *
 * Registers a BLE HID keyboard service via BlueZ D-Bus API.
 * Receives HID reports from the Python service via Unix socket
 * and sends them as GATT notifications to the connected host.
 *
 * Build: see Makefile
 * Usage: ./gattd [--verbose]
 *
 * TODO: Full implementation — this is a structural stub.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>

#include "protocol.h"

static volatile int running = 1;

static void on_signal(int sig) {
    (void)sig;
    running = 0;
}

/*
 * TODO: Implement GATT service registration via BlueZ D-Bus:
 *   - Register HID Service (UUID 0x1812)
 *   - Register HID Information characteristic
 *   - Register Report Map characteristic (keyboard descriptor)
 *   - Register HID Input Report characteristic (notify)
 *   - Start BLE advertising
 */

static int setup_ipc(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket");
        return -1;
    }

    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SPOTIFONE_SOCK_PATH, sizeof(addr.sun_path) - 1);

    unlink(SPOTIFONE_SOCK_PATH);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(fd);
        return -1;
    }

    if (listen(fd, 1) < 0) {
        perror("listen");
        close(fd);
        return -1;
    }

    return fd;
}

int main(int argc, char *argv[]) {
    int verbose = 0;
    if (argc > 1 && strcmp(argv[1], "--verbose") == 0)
        verbose = 1;

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    printf("gattd: starting BLE GATT HID server\n");

    int ipc_fd = setup_ipc();
    if (ipc_fd < 0)
        return 1;

    if (verbose)
        printf("gattd: listening on %s\n", SPOTIFONE_SOCK_PATH);

    /* TODO: Initialize BlueZ D-Bus connection and register GATT services */

    while (running) {
        /* TODO: Accept IPC connections, receive HID reports,
         * send as GATT notifications */
        sleep(1);
    }

    close(ipc_fd);
    unlink(SPOTIFONE_SOCK_PATH);
    printf("gattd: stopped\n");
    return 0;
}
