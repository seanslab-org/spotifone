/*
 * Spotifone Button Listener (C)
 *
 * Reads Car Thing input events and forwards:
 *   - Button presses (EV_KEY) -> HID keyboard daemon + mic_bridge (PTT)
 *   - Knob rotation (EV_REL)  -> HID keyboard daemon (macOS app switcher)
 *
 * Round button (code 1):
 *   - Press:   send Right Alt (0xE6) press to HID + PTT START to mic
 *   - Release: send Right Alt (0xE6) release to HID + PTT STOP to mic
 *
 * Preset #1 button (code 2):
 *   - Press: send key "9" tap to HID
 *
 * Knob click (KEY_ENTER, code 28):
 *   - First click:  hold Cmd (Left GUI) + tap Tab (Cmd+Tab) to open app switcher
 *   - Rotate wheel: tap Tab to move forward; rotate opposite direction -> Shift+Tab
 *   - Second click: release Cmd to switch/focus the selected app
 */

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define EVENT_DEV_BUTTONS "/dev/input/event0"
#define EVENT_DEV_KNOB "/dev/input/event1"
#define HID_SOCK_PATH "/tmp/spotifone_hid.sock"
#define MIC_SOCK_PATH "/tmp/spotifone_mic.sock"
#define MENU_SOCK_PATH "/tmp/spotifone_menu.sock"

#define ROUND_BUTTON_CODE 1
#define PRESET_1_CODE 2
#define PRESET_2_CODE 3
#define PRESET_3_CODE 4
#define PRESET_4_CODE 5
#define MUTE_BUTTON_CODE 50
#define KNOB_CLICK_CODE 28

#define HID_RIGHT_ALT 0xE6
#define HID_LEFT_ARROW 0x50
#define HID_RIGHT_ARROW 0x4F
#define HID_ENTER 0x28
/* macOS "Delete" key is Backspace (delete backwards). */
#define HID_BACKSPACE 0x2A
#define HID_LEFT_SHIFT 0xE1
#define HID_LEFT_GUI 0xE3 /* macOS Command key */
#define HID_TAB 0x2B

#define MIC_CMD_STOP  0x00
#define MIC_CMD_START 0x01

/* Knob rotation: EV_REL code 6 on Car Thing (see src/hardware.py) */
#define KNOB_REL_CODE 6

/* Safety: if user forgets to exit app switcher, auto-release Cmd */
#define APP_SWITCH_TIMEOUT_MS 3000

/* Wheel smoothing:
 * The rotary driver can be a bit jittery (extra ticks, occasional opposite tick).
 * Filter rules:
 *  - Debounce duplicate same-direction ticks within a few ms
 *  - Integrate deltas and emit at a steady rate
 *  - Hysteresis on direction reversal: require sustained opposite movement
 */
#define WHEEL_EVENT_DEBOUNCE_MS 12
#define WHEEL_EMIT_INTERVAL_MS 30
#define WHEEL_IDLE_RESET_MS 160
#define WHEEL_STEP_THRESHOLD 1
#define WHEEL_REVERSE_THRESHOLD 3
#define WHEEL_ACCUM_MAX 48

/* Some kernels/drivers may surface knob click on multiple event devices.
 * Debounce so we don't toggle ENTER+EXIT on a single physical click. */
#define KNOB_CLICK_DEBOUNCE_MS 200

static volatile int g_running = 1;

static void on_signal(int sig) {
    (void)sig;
    g_running = 0;
}

static void log_info(const char *msg) {
    fprintf(stdout, "[button_listener] %s\n", msg);
    fflush(stdout);
}

static long long monotonic_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + (long long)ts.tv_nsec / 1000000LL;
}

static int sign_i32(int v) {
    return (v > 0) ? 1 : (v < 0) ? -1 : 0;
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

static int send_mic_cmd(int sock, const struct sockaddr_un *addr, uint8_t cmd) {
    ssize_t n = sendto(sock, &cmd, 1, 0, (const struct sockaddr *)addr, sizeof(*addr));
    if (n != 1) {
        return -1;
    }
    return 0;
}

static int send_menu_cmd(int sock, const struct sockaddr_un *addr, uint8_t cmd) {
    ssize_t n = sendto(sock, &cmd, 1, 0, (const struct sockaddr *)addr, sizeof(*addr));
    if (n != 1) {
        return -1;
    }
    return 0;
}

static void send_tap(int sock, const struct sockaddr_un *addr, uint8_t keycode) {
    (void)send_hid_event(sock, addr, keycode, 1);
    usleep(15000);
    (void)send_hid_event(sock, addr, keycode, 0);
}

static void app_switch_enter(int hid_sock, const struct sockaddr_un *hid_addr) {
    /* Cmd down, then Tab tap to open the app switcher. */
    (void)send_hid_event(hid_sock, hid_addr, HID_LEFT_GUI, 1);
    usleep(10000);
    send_tap(hid_sock, hid_addr, HID_TAB);
}

static void app_switch_exit(int hid_sock, const struct sockaddr_un *hid_addr) {
    /* Be defensive: ensure Tab/Shift are not left "down" before releasing Cmd. */
    (void)send_hid_event(hid_sock, hid_addr, HID_TAB, 0);
    (void)send_hid_event(hid_sock, hid_addr, HID_LEFT_SHIFT, 0);
    (void)send_hid_event(hid_sock, hid_addr, HID_LEFT_GUI, 0);
}

static void app_switch_step(int hid_sock, const struct sockaddr_un *hid_addr, int direction) {
    /* direction: >0 = forward, <0 = backward */
    if (direction < 0) {
        (void)send_hid_event(hid_sock, hid_addr, HID_LEFT_SHIFT, 1);
        usleep(5000);
        send_tap(hid_sock, hid_addr, HID_TAB);
        usleep(5000);
        (void)send_hid_event(hid_sock, hid_addr, HID_LEFT_SHIFT, 0);
    } else {
        send_tap(hid_sock, hid_addr, HID_TAB);
    }
}

struct wheel_filter_state {
    int accum;                  /* queued steps to emit (+/-), integrated */
    int output_dir;             /* last emitted direction (+/-), 0=none */
    int last_raw_dir;           /* last raw tick direction (+/-) */
    long long last_raw_ms;      /* last raw tick time */
    long long last_emit_ms;     /* last time we emitted a step */
};

static void wheel_reset(struct wheel_filter_state *w) {
    memset(w, 0, sizeof(*w));
}

static void wheel_on_delta(struct wheel_filter_state *w, int delta, long long now_ms) {
    int raw_dir = sign_i32(delta);
    if (raw_dir == 0) {
        return;
    }

    /* Debounce: ignore duplicate same-direction ticks arriving too quickly. */
    if (w->last_raw_dir == raw_dir && (now_ms - w->last_raw_ms) < WHEEL_EVENT_DEBOUNCE_MS) {
        w->last_raw_ms = now_ms;
        return;
    }
    w->last_raw_dir = raw_dir;
    w->last_raw_ms = now_ms;

    /* Integrate delta magnitude (some drivers may coalesce multiple detents). */
    int mag = delta < 0 ? -delta : delta;
    if (mag > 8) {
        mag = 8;
    }
    int add = raw_dir * mag;
    w->accum += add;
    if (w->accum > WHEEL_ACCUM_MAX) w->accum = WHEEL_ACCUM_MAX;
    if (w->accum < -WHEEL_ACCUM_MAX) w->accum = -WHEEL_ACCUM_MAX;
}

static int wheel_try_emit(struct wheel_filter_state *w, int hid_sock, const struct sockaddr_un *hid_addr) {
    long long now_ms = monotonic_ms();

    /* If we're idle and no backlog remains, allow immediate direction on next gesture. */
    if (w->accum == 0) {
        if (w->output_dir != 0 && w->last_raw_ms != 0 && (now_ms - w->last_raw_ms) > WHEEL_IDLE_RESET_MS) {
            w->output_dir = 0;
        }
        return 0;
    }

    if (w->last_emit_ms != 0 && (now_ms - w->last_emit_ms) < WHEEL_EMIT_INTERVAL_MS) {
        return 0;
    }

    int desired = sign_i32(w->accum);
    if (desired == 0) {
        w->accum = 0;
        return 0;
    }

    int threshold = WHEEL_STEP_THRESHOLD;
    if (w->output_dir != 0 && desired != w->output_dir) {
        threshold = WHEEL_REVERSE_THRESHOLD;
    }

    int abs_accum = w->accum < 0 ? -w->accum : w->accum;
    if (abs_accum < threshold) {
        return 0;
    }

    app_switch_step(hid_sock, hid_addr, desired);
    w->accum -= desired;
    w->last_emit_ms = now_ms;
    w->output_dir = desired;
    return 1;
}

static void handle_knob_click(
    int hid_sock,
    const struct sockaddr_un *hid_addr,
    int *app_switch_active,
    long long *app_switch_last_ms)
{
    /* Toggle macOS app switcher mode on knob click. */
    if (!(*app_switch_active)) {
        app_switch_enter(hid_sock, hid_addr);
        *app_switch_active = 1;
        *app_switch_last_ms = monotonic_ms();
        log_info("Knob click -> app switcher ENTER (Cmd down + Tab)");
    } else {
        app_switch_exit(hid_sock, hid_addr);
        *app_switch_active = 0;
        log_info("Knob click -> app switcher EXIT (Cmd up)");
    }
}

int main(void) {
    int event_fd_buttons = -1;
    int event_fd_knob = -1;
    int hid_sock = -1;
    int mic_sock = -1;
    int menu_sock = -1;
    struct sockaddr_un hid_addr;
    struct sockaddr_un mic_addr;
    struct sockaddr_un menu_addr;
    struct input_event ev;

    int app_switch_active = 0;
    long long app_switch_last_ms = 0;
    long long knob_click_last_ms = 0;
    struct wheel_filter_state wheel = {0};

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    event_fd_buttons = open(EVENT_DEV_BUTTONS, O_RDONLY);
    if (event_fd_buttons < 0) {
        fprintf(stderr, "[button_listener ERROR] Failed to open %s: %s\n", EVENT_DEV_BUTTONS, strerror(errno));
        return 1;
    }

    event_fd_knob = open(EVENT_DEV_KNOB, O_RDONLY);
    if (event_fd_knob < 0) {
        /* Non-fatal: allow running without knob device present. */
        fprintf(stderr, "[button_listener WARN] Failed to open %s: %s\n", EVENT_DEV_KNOB, strerror(errno));
    }

    hid_sock = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (hid_sock < 0) {
        fprintf(stderr, "[button_listener ERROR] Failed to create HID socket: %s\n", strerror(errno));
        close(event_fd_buttons);
        return 1;
    }

    mic_sock = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (mic_sock < 0) {
        fprintf(stderr, "[button_listener ERROR] Failed to create mic socket: %s\n", strerror(errno));
        close(hid_sock);
        close(event_fd_buttons);
        return 1;
    }

    menu_sock = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (menu_sock < 0) {
        /* Non-fatal: menu UI is optional. */
        fprintf(stderr, "[button_listener WARN] Failed to create menu socket: %s\n", strerror(errno));
    }

    memset(&hid_addr, 0, sizeof(hid_addr));
    hid_addr.sun_family = AF_UNIX;
    strncpy(hid_addr.sun_path, HID_SOCK_PATH, sizeof(hid_addr.sun_path) - 1);

    memset(&mic_addr, 0, sizeof(mic_addr));
    mic_addr.sun_family = AF_UNIX;
    strncpy(mic_addr.sun_path, MIC_SOCK_PATH, sizeof(mic_addr.sun_path) - 1);

    memset(&menu_addr, 0, sizeof(menu_addr));
    menu_addr.sun_family = AF_UNIX;
    strncpy(menu_addr.sun_path, MENU_SOCK_PATH, sizeof(menu_addr.sun_path) - 1);

    log_info("Listening on /dev/input/event0 (buttons)");
    if (event_fd_knob >= 0) {
        log_info("Listening on /dev/input/event1 (knob)");
    }
    log_info("Round button -> Right Alt (0xE6) + PTT");
    log_info("Preset #1-4 -> Left / Right / Enter / Delete");
    log_info("Mute button -> Menu toggle");
    log_info("Knob click -> macOS app switcher (Cmd+Tab hold, rotate=Tab, click=release)");

    while (g_running) {
        fd_set rfds;
        struct timeval tv;
        int maxfd = -1;

        FD_ZERO(&rfds);
        FD_SET(event_fd_buttons, &rfds);
        maxfd = event_fd_buttons;
        if (event_fd_knob >= 0) {
            FD_SET(event_fd_knob, &rfds);
            if (event_fd_knob > maxfd) {
                maxfd = event_fd_knob;
            }
        }

        /* Short timeout so we can enforce app-switcher safety release. */
        tv.tv_sec = 0;
        tv.tv_usec = app_switch_active ? 20000 : 100000;

        int rc = select(maxfd + 1, &rfds, NULL, NULL, &tv);
        if (rc < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "[button_listener ERROR] select() failed: %s\n", strerror(errno));
            break;
        }

        long long now = monotonic_ms();

        /* While app switcher is active, drain queued wheel steps smoothly. */
        if (app_switch_active && wheel_try_emit(&wheel, hid_sock, &hid_addr)) {
            app_switch_last_ms = wheel.last_emit_ms;
            now = app_switch_last_ms;
        }

        /* Auto-release Cmd if user left app switcher open. */
        if (app_switch_active && (now - app_switch_last_ms >= APP_SWITCH_TIMEOUT_MS)) {
            app_switch_exit(hid_sock, &hid_addr);
            app_switch_active = 0;
            wheel_reset(&wheel);
            log_info("App switcher timeout -> released Cmd");
        }

        if (rc == 0) {
            continue;
        }

        /* Buttons device: EV_KEY */
        if (FD_ISSET(event_fd_buttons, &rfds)) {
            ssize_t n = read(event_fd_buttons, &ev, sizeof(ev));
            if (n < 0) {
                if (errno != EINTR) {
                    fprintf(stderr, "[button_listener ERROR] read(buttons) failed: %s\n", strerror(errno));
                }
            } else if (n == (ssize_t)sizeof(ev)) {
                if (ev.type == EV_KEY) {
                    if (ev.code == ROUND_BUTTON_CODE) {
                        if (ev.value == 1) {
                            /* Press: Right Alt down + PTT start */
                            send_hid_event(hid_sock, &hid_addr, HID_RIGHT_ALT, 1);
                            send_mic_cmd(mic_sock, &mic_addr, MIC_CMD_START);
                            log_info("Round button press -> Right Alt DOWN + PTT START");
                        } else if (ev.value == 0) {
                            /* Release: Right Alt up + PTT stop */
                            send_hid_event(hid_sock, &hid_addr, HID_RIGHT_ALT, 0);
                            send_mic_cmd(mic_sock, &mic_addr, MIC_CMD_STOP);
                            log_info("Round button release -> Right Alt UP + PTT STOP");
                        }
                    } else if (ev.code == PRESET_1_CODE) {
                        if (ev.value == 1) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_LEFT_ARROW, 1);
                        } else if (ev.value == 0) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_LEFT_ARROW, 0);
                        }
                    } else if (ev.code == PRESET_2_CODE) {
                        if (ev.value == 1) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_RIGHT_ARROW, 1);
                        } else if (ev.value == 0) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_RIGHT_ARROW, 0);
                        }
                    } else if (ev.code == PRESET_3_CODE) {
                        if (ev.value == 1) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_ENTER, 1);
                        } else if (ev.value == 0) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_ENTER, 0);
                        }
                    } else if (ev.code == PRESET_4_CODE) {
                        if (ev.value == 1) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_BACKSPACE, 1);
                        } else if (ev.value == 0) {
                            (void)send_hid_event(hid_sock, &hid_addr, HID_BACKSPACE, 0);
                        }
                    } else if (ev.code == MUTE_BUTTON_CODE && ev.value == 1) {
                        /* Phase 2: menu toggle is the ONLY new button behavior. */
                        if (menu_sock >= 0) {
                            (void)send_menu_cmd(menu_sock, &menu_addr, 0x01);
                        }
                    } else if (ev.code == KNOB_CLICK_CODE && ev.value == 1) {
                        long long now = monotonic_ms();
                        if (now - knob_click_last_ms >= KNOB_CLICK_DEBOUNCE_MS) {
                            knob_click_last_ms = now;
                            int was_active = app_switch_active;
                            handle_knob_click(hid_sock, &hid_addr, &app_switch_active, &app_switch_last_ms);
                            if (!was_active && app_switch_active) {
                                wheel_reset(&wheel);
                            } else if (was_active && !app_switch_active) {
                                wheel_reset(&wheel);
                            }
                        }
                    }
                }
            }
        }

        /* Knob device: EV_REL */
        if (event_fd_knob >= 0 && FD_ISSET(event_fd_knob, &rfds)) {
            ssize_t n = read(event_fd_knob, &ev, sizeof(ev));
            if (n < 0) {
                if (errno != EINTR) {
                    fprintf(stderr, "[button_listener ERROR] read(knob) failed: %s\n", strerror(errno));
                }
            } else if (n == (ssize_t)sizeof(ev)) {
                if (ev.type == EV_KEY && ev.code == KNOB_CLICK_CODE && ev.value == 1) {
                    /* Some systems report knob click on the rotary device (event1). */
                    long long now = monotonic_ms();
                    if (now - knob_click_last_ms >= KNOB_CLICK_DEBOUNCE_MS) {
                        knob_click_last_ms = now;
                        int was_active = app_switch_active;
                        handle_knob_click(hid_sock, &hid_addr, &app_switch_active, &app_switch_last_ms);
                        if (!was_active && app_switch_active) {
                            wheel_reset(&wheel);
                        } else if (was_active && !app_switch_active) {
                            wheel_reset(&wheel);
                        }
                    }
                } else if (ev.type == EV_REL && ev.code == KNOB_REL_CODE && app_switch_active) {
                    long long now = monotonic_ms();
                    wheel_on_delta(&wheel, ev.value, now);
                    /* Any wheel activity (even filtered) keeps app switcher alive. */
                    app_switch_last_ms = now;
                }
            }
        }
    }

    /* Best-effort release of any held modifiers to avoid sticky keys on host. */
    if (app_switch_active) {
        app_switch_exit(hid_sock, &hid_addr);
    }
    (void)send_hid_event(hid_sock, &hid_addr, HID_RIGHT_ALT, 0);
    (void)send_hid_event(hid_sock, &hid_addr, HID_LEFT_ARROW, 0);
    (void)send_hid_event(hid_sock, &hid_addr, HID_RIGHT_ARROW, 0);
    (void)send_hid_event(hid_sock, &hid_addr, HID_ENTER, 0);
    (void)send_hid_event(hid_sock, &hid_addr, HID_BACKSPACE, 0);

    close(mic_sock);
    if (menu_sock >= 0) {
        close(menu_sock);
    }
    close(hid_sock);
    if (event_fd_knob >= 0) {
        close(event_fd_knob);
    }
    close(event_fd_buttons);
    log_info("Stopped");
    return 0;
}
