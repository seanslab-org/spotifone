/*
 * Spotifone HSP-AG Audio Bridge (mic_bridge)
 *
 * Bridges the Car Thing microphone to Bluetooth via HSP/HFP Audio Gateway.
 * Uses BlueALSA for Bluetooth audio routing.
 * Receives mute/unmute commands from the Python service via Unix socket.
 *
 * Build: see Makefile
 * Usage: ./mic_bridge [--verbose]
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

#define MIC_SOCK_PATH  "/var/run/spotifone_mic.sock"

static volatile int running = 1;
static int mic_muted = 1;  /* start muted */

static void on_signal(int sig) {
    (void)sig;
    running = 0;
}

/*
 * TODO: Implement audio pipeline:
 *   - Open ALSA capture device (hw:0,0 or similar)
 *   - Open BlueALSA SCO sink for HSP/HFP
 *   - Stream PCM data from mic to Bluetooth when unmuted
 *   - Zero-fill or pause stream when muted
 */

static int setup_ipc(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket");
        return -1;
    }

    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, MIC_SOCK_PATH, sizeof(addr.sun_path) - 1);

    unlink(MIC_SOCK_PATH);

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

static void handle_message(const struct sf_msg *msg) {
    switch (msg->type) {
    case SF_MSG_MIC_MUTE:
        mic_muted = 1;
        printf("mic_bridge: muted\n");
        break;
    case SF_MSG_MIC_UNMUTE:
        mic_muted = 0;
        printf("mic_bridge: unmuted\n");
        break;
    case SF_MSG_SHUTDOWN:
        running = 0;
        break;
    default:
        break;
    }
}

int main(int argc, char *argv[]) {
    int verbose = 0;
    if (argc > 1 && strcmp(argv[1], "--verbose") == 0)
        verbose = 1;

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    printf("mic_bridge: starting HSP-AG audio bridge\n");

    int ipc_fd = setup_ipc();
    if (ipc_fd < 0)
        return 1;

    if (verbose)
        printf("mic_bridge: listening on %s\n", MIC_SOCK_PATH);

    /* TODO: Initialize ALSA capture and BlueALSA SCO */

    while (running) {
        /* TODO: Accept IPC connections, handle mute/unmute,
         * stream audio when unmuted */
        sleep(1);
    }

    close(ipc_fd);
    unlink(MIC_SOCK_PATH);
    printf("mic_bridge: stopped\n");
    return 0;
}
