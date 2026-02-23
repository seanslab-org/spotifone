/*
 * Spotifone Button Listener (C)
 *
 * Reads Car Thing key events from /dev/input/event0 and forwards
 * mapped key taps to HID daemon over Unix datagram socket.
 *
 * Mapping:
 *   - Function button (code 50) -> keyboard key "1" (HID 0x1E)
 *   - Preset #1 button (code 2) -> keyboard key "9" (HID 0x26)
 */

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

#define EVENT_DEV "/dev/input/event0"
#define HID_SOCK_PATH "/tmp/spotifone_hid.sock"

#define FUNC_BUTTON_CODE 50
#define PRESET_1_CODE 2

#define HID_KEY_1 0x1E
#define HID_KEY_9 0x26

static volatile int g_running = 1;

static void on_signal(int sig) {
    (void)sig;
    g_running = 0;
}

static void log_info(const char *msg) {
    fprintf(stdout, "[button_listener] %s\n", msg);
    fflush(stdout);
}

static int send_hid_event(int sock, const struct sockaddr_un *addr, uint8_t keycode, int pressed) {
    uint8_t payload[2];
    payload[0] = keycode;
    payload[1] = pressed ? 1 : 0;

    ssize_t n = sendto(sock, payload, sizeof(payload), 0, (const struct sockaddr *)addr, sizeof(*addr));
    if (n != (ssize_t)sizeof(payload)) {
        return -1;
    }
    return 0;
}

static void send_tap(int sock, const struct sockaddr_un *addr, uint8_t keycode) {
    (void)send_hid_event(sock, addr, keycode, 1);
    usleep(15000);
    (void)send_hid_event(sock, addr, keycode, 0);
}

int main(void) {
    int event_fd = -1;
    int hid_sock = -1;
    struct sockaddr_un hid_addr;
    struct input_event ev;

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    event_fd = open(EVENT_DEV, O_RDONLY);
    if (event_fd < 0) {
        fprintf(stderr, "[button_listener ERROR] Failed to open %s: %s\n", EVENT_DEV, strerror(errno));
        return 1;
    }

    hid_sock = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (hid_sock < 0) {
        fprintf(stderr, "[button_listener ERROR] Failed to create socket: %s\n", strerror(errno));
        close(event_fd);
        return 1;
    }

    memset(&hid_addr, 0, sizeof(hid_addr));
    hid_addr.sun_family = AF_UNIX;
    strncpy(hid_addr.sun_path, HID_SOCK_PATH, sizeof(hid_addr.sun_path) - 1);

    log_info("Listening on /dev/input/event0");
    log_info("Function button -> key '1'");
    log_info("Preset #1 -> key '9'");

    while (g_running) {
        ssize_t n = read(event_fd, &ev, sizeof(ev));
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "[button_listener ERROR] read() failed: %s\n", strerror(errno));
            break;
        }
        if (n != (ssize_t)sizeof(ev)) {
            continue;
        }

        if (ev.type != EV_KEY) {
            continue;
        }
        if (ev.value != 1) {
            continue;
        }

        if (ev.code == FUNC_BUTTON_CODE) {
            send_tap(hid_sock, &hid_addr, HID_KEY_1);
            log_info("Function press -> sent key '1'");
        } else if (ev.code == PRESET_1_CODE) {
            send_tap(hid_sock, &hid_addr, HID_KEY_9);
            log_info("Preset #1 press -> sent key '9'");
        }
    }

    close(hid_sock);
    close(event_fd);
    log_info("Stopped");
    return 0;
}
