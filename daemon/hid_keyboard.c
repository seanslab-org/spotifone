/*
 * Spotifone Classic BT HID Keyboard Server (C)
 *
 * Replaces src/hid_keyboard.py with a native C daemon.
 *
 * Responsibilities:
 *   - Register Classic BT HID profile (UUID 0x1124) with BlueZ ProfileManager1
 *   - Receive key events via Unix datagram socket: /tmp/spotifone_hid.sock
 *   - Send HID keyboard reports on HID Interrupt channel
 *
 * Pairing is handled by mic_bridge's agent (registered first at boot).
 *
 * IPC payload format (compatible with old Python button listener):
 *   [0] keycode (USB HID)
 *   [1] pressed (0 or non-zero)
 */

#include <dbus/dbus.h>
#include <bluetooth/bluetooth.h>
#include <bluetooth/l2cap.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define BLUEZ_SERVICE "org.bluez"
#define BLUEZ_ROOT_PATH "/org/bluez"
#define BLUEZ_ADAPTER_PATH "/org/bluez/hci0"

#define PROFILE_IFACE "org.bluez.Profile1"
#define INTROSPECT_IFACE "org.freedesktop.DBus.Introspectable"

#define PROFILE_PATH "/org/spotifone/hid_profile"
#define HID_UUID "00001124-0000-1000-8000-00805f9b34fb"

#define IPC_SOCK_PATH "/tmp/spotifone_hid.sock"

static const char *PROFILE_INTROSPECT_XML =
    "<!DOCTYPE node PUBLIC '-//freedesktop//DTD D-BUS Object Introspection 1.0//EN' "
    "'http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd'>"
    "<node>"
    " <interface name='org.bluez.Profile1'>"
    "  <method name='Release'/>"
    "  <method name='NewConnection'>"
    "   <arg name='device' type='o' direction='in'/>"
    "   <arg name='fd' type='h' direction='in'/>"
    "   <arg name='fd_properties' type='a{sv}' direction='in'/>"
    "  </method>"
    "  <method name='RequestDisconnection'>"
    "   <arg name='device' type='o' direction='in'/>"
    "  </method>"
    " </interface>"
    " <interface name='org.freedesktop.DBus.Introspectable'>"
    "  <method name='Introspect'>"
    "   <arg name='data' type='s' direction='out'/>"
    "  </method>"
    " </interface>"
    "</node>";

static const char *HID_SDP_RECORD_XML =
    "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>"
    "<record>"
    "  <attribute id=\"0x0001\">"
    "    <sequence><uuid value=\"0x1124\"/></sequence>"
    "  </attribute>"
    "  <attribute id=\"0x0004\">"
    "    <sequence>"
    "      <sequence><uuid value=\"0x0100\"/><uint16 value=\"0x0011\"/></sequence>"
    "      <sequence><uuid value=\"0x0011\"/></sequence>"
    "    </sequence>"
    "  </attribute>"
    "  <attribute id=\"0x0005\">"
    "    <sequence><uuid value=\"0x1002\"/></sequence>"
    "  </attribute>"
    "  <attribute id=\"0x0006\">"
    "    <sequence><uint16 value=\"0x656e\"/><uint16 value=\"0x006a\"/><uint16 value=\"0x0100\"/></sequence>"
    "  </attribute>"
    "  <attribute id=\"0x0009\">"
    "    <sequence><sequence><uuid value=\"0x1124\"/><uint16 value=\"0x0101\"/></sequence></sequence>"
    "  </attribute>"
    "  <attribute id=\"0x000d\">"
    "    <sequence>"
    "      <sequence>"
    "        <sequence><uuid value=\"0x0100\"/><uint16 value=\"0x0013\"/></sequence>"
    "        <sequence><uuid value=\"0x0011\"/></sequence>"
    "      </sequence>"
    "    </sequence>"
    "  </attribute>"
    "  <attribute id=\"0x0100\"><text value=\"Spotifone Keyboard\"/></attribute>"
    "  <attribute id=\"0x0101\"><text value=\"Bluetooth HID Keyboard\"/></attribute>"
    "  <attribute id=\"0x0102\"><text value=\"Spotifone\"/></attribute>"
    "  <attribute id=\"0x0200\"><uint16 value=\"0x0100\"/></attribute>"
    "  <attribute id=\"0x0201\"><uint16 value=\"0x0111\"/></attribute>"
    "  <attribute id=\"0x0202\"><uint8 value=\"0x40\"/></attribute>"
    "  <attribute id=\"0x0203\"><uint8 value=\"0x00\"/></attribute>"
    "  <attribute id=\"0x0204\"><boolean value=\"true\"/></attribute>"
    "  <attribute id=\"0x0205\"><boolean value=\"true\"/></attribute>"
    "  <attribute id=\"0x0206\">"
    "    <sequence>"
    "      <sequence>"
    "        <uint8 value=\"0x22\"/>"
    "        <text encoding=\"hex\" value=\"05010906a1018501050719e029e71500250175019508810295017508810195067508150025650507190029658100c0\"/>"
    "      </sequence>"
    "    </sequence>"
    "  </attribute>"
    "  <attribute id=\"0x0207\">"
    "    <sequence><sequence><uint16 value=\"0x0409\"/><uint16 value=\"0x0100\"/></sequence></sequence>"
    "  </attribute>"
    "  <attribute id=\"0x020b\"><boolean value=\"true\"/></attribute>"
    "  <attribute id=\"0x020c\"><uint16 value=\"0x0c80\"/></attribute>"
    "  <attribute id=\"0x020d\"><boolean value=\"true\"/></attribute>"
    "  <attribute id=\"0x020e\"><uint16 value=\"0x0101\"/></attribute>"
    "</record>";

static DBusConnection *g_conn = NULL;
static volatile int g_running = 1;
static int g_ctrl_fd = -1;
static int g_intr_fd = -1;
static int g_ipc_fd = -1;
static int g_intr_listen_fd = -1;
static uint8_t g_modifiers = 0;
static int g_logged_no_intr = 0;

/* Deferred HID connect: triggered by D-Bus Connected signal */
static uint8_t g_pending_bdaddr[6];
static long long g_pending_connect_ms = 0;
#define HID_CONNECT_DELAY_MS 1500

static long long monotonic_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + (long long)ts.tv_nsec / 1000000LL;
}

/* Parse BD address from D-Bus device path like /org/bluez/hci0/dev_D0_11_E5_70_6E_B5 */
static int parse_bdaddr_from_path(const char *path, uint8_t *out) {
    const char *dev = strstr(path, "/dev_");
    if (!dev) return -1;
    dev += 5; /* skip "/dev_" */
    unsigned int b[6];
    if (sscanf(dev, "%02x_%02x_%02x_%02x_%02x_%02x",
               &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6) {
        return -1;
    }
    for (int i = 0; i < 6; i++) out[i] = (uint8_t)b[i];
    return 0;
}

static void log_info(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stdout, "[hid_keyboard] ");
    vfprintf(stdout, fmt, ap);
    fprintf(stdout, "\n");
    fflush(stdout);
    va_end(ap);
}

static void log_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[hid_keyboard ERROR] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    fflush(stderr);
    va_end(ap);
}

static void on_signal(int sig) {
    (void)sig;
    g_running = 0;
}

static void close_hid_connections(void) {
    if (g_ctrl_fd >= 0) {
        close(g_ctrl_fd);
        g_ctrl_fd = -1;
    }
    if (g_intr_fd >= 0) {
        close(g_intr_fd);
        g_intr_fd = -1;
    }
    g_modifiers = 0;
    g_logged_no_intr = 0;
}

static void close_intr_listener(void) {
    if (g_intr_listen_fd >= 0) {
        close(g_intr_listen_fd);
        g_intr_listen_fd = -1;
    }
}

static void set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return;
    }
    (void)fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static void send_hid_report(uint8_t modifier, uint8_t keycode) {
    uint8_t report[10] = {0xA1, 0x01, modifier, 0x00, keycode, 0, 0, 0, 0, 0};

    if (g_intr_fd < 0) {
        if (!g_logged_no_intr) {
            log_info("Dropping key 0x%02x: HID interrupt channel is not connected", keycode);
            g_logged_no_intr = 1;
        }
        return;
    }

    ssize_t n = send(g_intr_fd, report, sizeof(report), 0);
    if (n < 0) {
        log_error("Failed to send HID report: %s", strerror(errno));
        close_hid_connections();
        return;
    }
}

/* Outbound L2CAP connect to host's HID PSMs (device-initiated reconnect).
 * bdaddr_bytes: 6 bytes, network order (AA:BB:CC:DD:EE:FF → [AA,BB,CC,DD,EE,FF]).
 * bdaddr_t is little-endian (reversed), so we swap. */
static int connect_hid_to_host(const uint8_t *bdaddr_bytes) {
    struct sockaddr_l2 addr;
    bdaddr_t remote;
    int ctrl_fd, intr_fd;

    for (int i = 0; i < 6; i++) {
        remote.b[5 - i] = bdaddr_bytes[i];
    }

    log_info("Outbound HID connect to %02X:%02X:%02X:%02X:%02X:%02X",
             bdaddr_bytes[0], bdaddr_bytes[1], bdaddr_bytes[2],
             bdaddr_bytes[3], bdaddr_bytes[4], bdaddr_bytes[5]);

    close_hid_connections();

    /* Control channel (PSM 17) */
    ctrl_fd = socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP);
    if (ctrl_fd < 0) {
        log_error("Outbound PSM17 socket: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.l2_family = AF_BLUETOOTH;
    addr.l2_psm = htobs(0x0011);
    bacpy(&addr.l2_bdaddr, &remote);

    if (connect(ctrl_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        log_error("Outbound PSM17 connect: %s", strerror(errno));
        close(ctrl_fd);
        return -1;
    }

    set_nonblocking(ctrl_fd);
    g_ctrl_fd = ctrl_fd;
    log_info("Control channel connected (PSM 17, outbound)");

    /* Interrupt channel (PSM 19) */
    intr_fd = socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP);
    if (intr_fd < 0) {
        log_error("Outbound PSM19 socket: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.l2_family = AF_BLUETOOTH;
    addr.l2_psm = htobs(0x0013);
    bacpy(&addr.l2_bdaddr, &remote);

    if (connect(intr_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        log_error("Outbound PSM19 connect: %s", strerror(errno));
        close(intr_fd);
        /* Also clean up ctrl channel — leave no partial state for NewConnection. */
        close(g_ctrl_fd);
        g_ctrl_fd = -1;
        return -1;
    }

    set_nonblocking(intr_fd);
    g_intr_fd = intr_fd;
    g_logged_no_intr = 0;
    log_info("Interrupt channel connected (PSM 19, outbound)");

    return 0;
}

static void handle_ipc_event(void) {
    uint8_t msg[16];
    ssize_t n = recv(g_ipc_fd, msg, sizeof(msg), 0);

    /* 7 bytes starting with 0xFF = outbound connect command [0xFF, bd_addr[6]] */
    if (n == 7 && msg[0] == 0xFF) {
        connect_hid_to_host(&msg[1]);
        return;
    }

    if (n < 2) {
        return;
    }

    uint8_t keycode = msg[0];
    int pressed = msg[1] != 0;

    if (keycode >= 0xE0 && keycode <= 0xE7) {
        uint8_t bit = (uint8_t)(1U << (keycode - 0xE0));
        if (pressed) {
            g_modifiers |= bit;
        } else {
            g_modifiers &= (uint8_t)~bit;
        }
        send_hid_report(g_modifiers, 0x00);
        return;
    }

    if (pressed) {
        send_hid_report(g_modifiers, keycode);
    } else {
        send_hid_report(g_modifiers, 0x00);
    }
}

static void handle_ctrl_event(void) {
    uint8_t buf[64];
    ssize_t n = recv(g_ctrl_fd, buf, sizeof(buf), 0);
    if (n <= 0) {
        log_info("Control channel disconnected");
        close_hid_connections();
        return;
    }

    if ((buf[0] & 0xF0) == 0x70) {
        uint8_t handshake_ok = 0x00;
        (void)send(g_ctrl_fd, &handshake_ok, 1, 0);
        log_info("SET_PROTOCOL handled");
    }
}

static int setup_intr_listener(void) {
    int fd;
    struct sockaddr_l2 addr;
    bdaddr_t any = {{0, 0, 0, 0, 0, 0}};

    fd = socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP);
    if (fd < 0) {
        log_error("PSM19 socket create failed: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.l2_family = AF_BLUETOOTH;
    addr.l2_psm = htobs(0x0013); /* HID interrupt PSM */
    bacpy(&addr.l2_bdaddr, &any);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        log_error("PSM19 bind failed: %s", strerror(errno));
        close(fd);
        return -1;
    }

    if (listen(fd, 4) < 0) {
        log_error("PSM19 listen failed: %s", strerror(errno));
        close(fd);
        return -1;
    }

    set_nonblocking(fd);
    log_info("Listening for HID interrupt on PSM 19");
    return fd;
}

static void handle_intr_accept(void) {
    int fd = accept(g_intr_listen_fd, NULL, NULL);
    if (fd < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
            log_error("PSM19 accept failed: %s", strerror(errno));
        }
        return;
    }

    set_nonblocking(fd);

    if (g_intr_fd >= 0) {
        close(g_intr_fd);
    }
    g_intr_fd = fd;
    g_logged_no_intr = 0;
    log_info("Interrupt channel connected on PSM 19");
}

static DBusHandlerResult reply_with_introspection(DBusConnection *connection, DBusMessage *message, const char *xml) {
    DBusMessage *reply = dbus_message_new_method_return(message);
    if (!reply) {
        return DBUS_HANDLER_RESULT_NEED_MEMORY;
    }
    dbus_message_append_args(reply, DBUS_TYPE_STRING, &xml, DBUS_TYPE_INVALID);
    dbus_connection_send(connection, reply, NULL);
    dbus_message_unref(reply);
    return DBUS_HANDLER_RESULT_HANDLED;
}

static DBusHandlerResult profile_message_handler(DBusConnection *connection, DBusMessage *message, void *user_data) {
    (void)user_data;

    const char *iface = dbus_message_get_interface(message);
    const char *member = dbus_message_get_member(message);
    DBusMessage *reply = NULL;

    if (!iface || !member) {
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }

    if (strcmp(iface, INTROSPECT_IFACE) == 0 && strcmp(member, "Introspect") == 0) {
        return reply_with_introspection(connection, message, PROFILE_INTROSPECT_XML);
    }

    if (strcmp(iface, PROFILE_IFACE) != 0) {
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }

    if (strcmp(member, "NewConnection") == 0) {
        const char *device = NULL;
        int fd = -1;
        DBusMessageIter it;

        if (dbus_message_iter_init(message, &it)) {
            if (dbus_message_iter_get_arg_type(&it) == DBUS_TYPE_OBJECT_PATH) {
                dbus_message_iter_get_basic(&it, &device);
            }
            if (dbus_message_iter_next(&it) && dbus_message_iter_get_arg_type(&it) == DBUS_TYPE_UNIX_FD) {
                dbus_message_iter_get_basic(&it, &fd);
            }
        }

        if (fd >= 0) {
            int local_fd = dup(fd);
            if (local_fd >= 0) {
                set_nonblocking(local_fd);
                if (g_ctrl_fd < 0) {
                    g_ctrl_fd = local_fd;
                    log_info("Control channel connected from %s", device ? device : "unknown");
                } else if (g_intr_fd < 0) {
                    g_intr_fd = local_fd;
                    log_info("Interrupt channel connected from %s", device ? device : "unknown");
                } else {
                    log_info("Extra HID channel received, closing");
                    close(local_fd);
                }
            }
        }

        reply = dbus_message_new_method_return(message);
        dbus_connection_send(connection, reply, NULL);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    if (strcmp(member, "RequestDisconnection") == 0) {
        log_info("RequestDisconnection received");
        close_hid_connections();

        reply = dbus_message_new_method_return(message);
        dbus_connection_send(connection, reply, NULL);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    if (strcmp(member, "Release") == 0) {
        log_info("Profile released by BlueZ");

        reply = dbus_message_new_method_return(message);
        dbus_connection_send(connection, reply, NULL);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
}

static int append_dict_entry_string(DBusMessageIter *dict, const char *key, const char *value) {
    DBusMessageIter entry, variant;

    if (!dbus_message_iter_open_container(dict, DBUS_TYPE_DICT_ENTRY, NULL, &entry)) {
        return -1;
    }
    if (!dbus_message_iter_append_basic(&entry, DBUS_TYPE_STRING, &key)) {
        return -1;
    }
    if (!dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, "s", &variant)) {
        return -1;
    }
    if (!dbus_message_iter_append_basic(&variant, DBUS_TYPE_STRING, &value)) {
        return -1;
    }
    dbus_message_iter_close_container(&entry, &variant);
    dbus_message_iter_close_container(dict, &entry);
    return 0;
}

static int append_dict_entry_bool(DBusMessageIter *dict, const char *key, dbus_bool_t value) {
    DBusMessageIter entry, variant;

    if (!dbus_message_iter_open_container(dict, DBUS_TYPE_DICT_ENTRY, NULL, &entry)) {
        return -1;
    }
    if (!dbus_message_iter_append_basic(&entry, DBUS_TYPE_STRING, &key)) {
        return -1;
    }
    if (!dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, "b", &variant)) {
        return -1;
    }
    if (!dbus_message_iter_append_basic(&variant, DBUS_TYPE_BOOLEAN, &value)) {
        return -1;
    }
    dbus_message_iter_close_container(&entry, &variant);
    dbus_message_iter_close_container(dict, &entry);
    return 0;
}

static int append_dict_entry_uint16(DBusMessageIter *dict, const char *key, dbus_uint16_t value) {
    DBusMessageIter entry, variant;

    if (!dbus_message_iter_open_container(dict, DBUS_TYPE_DICT_ENTRY, NULL, &entry)) {
        return -1;
    }
    if (!dbus_message_iter_append_basic(&entry, DBUS_TYPE_STRING, &key)) {
        return -1;
    }
    if (!dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, "q", &variant)) {
        return -1;
    }
    if (!dbus_message_iter_append_basic(&variant, DBUS_TYPE_UINT16, &value)) {
        return -1;
    }
    dbus_message_iter_close_container(&entry, &variant);
    dbus_message_iter_close_container(dict, &entry);
    return 0;
}

static int register_hid_profile(void) {
    DBusMessage *msg = NULL;
    DBusMessage *reply = NULL;
    DBusMessageIter it, dict;
    DBusPendingCall *pending = NULL;

    msg = dbus_message_new_method_call(BLUEZ_SERVICE, BLUEZ_ROOT_PATH, "org.bluez.ProfileManager1", "RegisterProfile");
    if (!msg) {
        log_error("Failed to create RegisterProfile call");
        return -1;
    }

    dbus_message_iter_init_append(msg, &it);
    {
        const char *path = PROFILE_PATH;
        const char *uuid = HID_UUID;
        dbus_message_iter_append_basic(&it, DBUS_TYPE_OBJECT_PATH, &path);
        dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &uuid);
    }

    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &dict);
    append_dict_entry_string(&dict, "ServiceRecord", HID_SDP_RECORD_XML);
    append_dict_entry_uint16(&dict, "PSM", 0x0011);
    append_dict_entry_bool(&dict, "RequireAuthentication", FALSE);
    append_dict_entry_bool(&dict, "RequireAuthorization", FALSE);
    append_dict_entry_bool(&dict, "AutoConnect", TRUE);
    dbus_message_iter_close_container(&it, &dict);

    if (!dbus_connection_send_with_reply(g_conn, msg, &pending, 5000) || !pending) {
        dbus_message_unref(msg);
        log_error("RegisterProfile send failed");
        return -1;
    }
    dbus_connection_flush(g_conn);
    dbus_message_unref(msg);

    while (!dbus_pending_call_get_completed(pending)) {
        dbus_connection_read_write_dispatch(g_conn, 100);
    }

    reply = dbus_pending_call_steal_reply(pending);
    dbus_pending_call_unref(pending);

    if (!reply) {
        log_error("RegisterProfile returned no reply");
        return -1;
    }

    if (dbus_message_get_type(reply) == DBUS_MESSAGE_TYPE_ERROR) {
        DBusError err;
        const char *err_name = dbus_message_get_error_name(reply);
        dbus_error_init(&err);
        if (dbus_set_error_from_message(&err, reply)) {
            log_error(
                "RegisterProfile failed: %s (%s)",
                err_name ? err_name : "unknown",
                err.message ? err.message : "no message");
            dbus_error_free(&err);
        } else {
            log_error("RegisterProfile failed: %s", err_name ? err_name : "unknown");
        }
        dbus_message_unref(reply);
        return -1;
    }

    dbus_message_unref(reply);
    log_info("HID profile registered");
    return 0;
}

static int setup_ipc_socket(void) {
    int fd = socket(AF_UNIX, SOCK_DGRAM, 0);
    struct sockaddr_un addr;

    if (fd < 0) {
        log_error("IPC socket create failed: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, IPC_SOCK_PATH, sizeof(addr.sun_path) - 1);

    unlink(IPC_SOCK_PATH);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        log_error("IPC bind failed: %s", strerror(errno));
        close(fd);
        return -1;
    }

    chmod(IPC_SOCK_PATH, 0666);
    set_nonblocking(fd);
    return fd;
}

/* D-Bus signal filter: watch for Device1.Connected=true to auto-trigger HID connect.
 * When a paired device connects (e.g. macOS reconnects HFP), BlueZ emits
 * PropertiesChanged with Connected=true. If we don't have HID channels up,
 * schedule a deferred outbound HID connect to that device. */
static DBusHandlerResult signal_filter(DBusConnection *connection, DBusMessage *message, void *user_data) {
    (void)connection;
    (void)user_data;

    if (!dbus_message_is_signal(message, "org.freedesktop.DBus.Properties", "PropertiesChanged"))
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    const char *path = dbus_message_get_path(message);
    if (!path || !strstr(path, "/dev_"))
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    DBusMessageIter args;
    if (!dbus_message_iter_init(message, &args))
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    /* First arg: interface name (string) */
    if (dbus_message_iter_get_arg_type(&args) != DBUS_TYPE_STRING)
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    const char *iface;
    dbus_message_iter_get_basic(&args, &iface);
    if (strcmp(iface, "org.bluez.Device1") != 0)
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    /* Second arg: changed properties dict a{sv} */
    if (!dbus_message_iter_next(&args))
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    if (dbus_message_iter_get_arg_type(&args) != DBUS_TYPE_ARRAY)
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    DBusMessageIter dict;
    dbus_message_iter_recurse(&args, &dict);

    while (dbus_message_iter_get_arg_type(&dict) == DBUS_TYPE_DICT_ENTRY) {
        DBusMessageIter entry, variant;
        dbus_message_iter_recurse(&dict, &entry);

        if (dbus_message_iter_get_arg_type(&entry) != DBUS_TYPE_STRING) {
            dbus_message_iter_next(&dict);
            continue;
        }

        const char *key;
        dbus_message_iter_get_basic(&entry, &key);

        if (strcmp(key, "Connected") == 0 && dbus_message_iter_next(&entry)) {
            dbus_message_iter_recurse(&entry, &variant);
            if (dbus_message_iter_get_arg_type(&variant) == DBUS_TYPE_BOOLEAN) {
                dbus_bool_t connected;
                dbus_message_iter_get_basic(&variant, &connected);

                if (connected && g_intr_fd < 0) {
                    uint8_t bdaddr[6];
                    if (parse_bdaddr_from_path(path, bdaddr) == 0) {
                        log_info("Device connected (D-Bus): %02X:%02X:%02X:%02X:%02X:%02X — scheduling HID connect",
                                 bdaddr[0], bdaddr[1], bdaddr[2], bdaddr[3], bdaddr[4], bdaddr[5]);
                        memcpy(g_pending_bdaddr, bdaddr, 6);
                        g_pending_connect_ms = monotonic_ms() + HID_CONNECT_DELAY_MS;
                    }
                }
            }
        }

        dbus_message_iter_next(&dict);
    }

    return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
}

int main(void) {
    DBusError err;
    DBusObjectPathVTable profile_vtable = {.message_function = profile_message_handler};

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    dbus_error_init(&err);
    g_conn = dbus_bus_get(DBUS_BUS_SYSTEM, &err);
    if (!g_conn) {
        log_error("Failed to connect to system bus: %s", err.message ? err.message : "unknown");
        dbus_error_free(&err);
        return 1;
    }

    if (!dbus_connection_register_object_path(g_conn, PROFILE_PATH, &profile_vtable, NULL)) {
        log_error("Failed to register Profile object path");
        return 1;
    }

    if (register_hid_profile() < 0) {
        return 1;
    }

    /* Watch for Device1.Connected property changes to auto-trigger HID connect */
    dbus_bus_add_match(g_conn,
        "type='signal',interface='org.freedesktop.DBus.Properties',"
        "member='PropertiesChanged',arg0='org.bluez.Device1'",
        &err);
    if (dbus_error_is_set(&err)) {
        log_error("D-Bus match add failed: %s", err.message);
        dbus_error_free(&err);
        /* Non-fatal: auto-connect won't work but manual IPC still does */
    }
    dbus_connection_add_filter(g_conn, signal_filter, NULL, NULL);

    g_ipc_fd = setup_ipc_socket();
    if (g_ipc_fd < 0) {
        return 1;
    }
    g_intr_listen_fd = setup_intr_listener();

    log_info("HID keyboard daemon running");
    log_info("IPC socket: %s", IPC_SOCK_PATH);

    while (g_running) {
        fd_set rfds;
        struct timeval tv;
        int maxfd = -1;
        int rc;

        FD_ZERO(&rfds);
        if (g_ipc_fd >= 0) {
            FD_SET(g_ipc_fd, &rfds);
            if (g_ipc_fd > maxfd) {
                maxfd = g_ipc_fd;
            }
        }
        if (g_ctrl_fd >= 0) {
            FD_SET(g_ctrl_fd, &rfds);
            if (g_ctrl_fd > maxfd) {
                maxfd = g_ctrl_fd;
            }
        }
        if (g_intr_listen_fd >= 0) {
            FD_SET(g_intr_listen_fd, &rfds);
            if (g_intr_listen_fd > maxfd) {
                maxfd = g_intr_listen_fd;
            }
        }

        tv.tv_sec = 0;
        tv.tv_usec = 100000;
        rc = select(maxfd + 1, &rfds, NULL, NULL, &tv);
        if (rc < 0) {
            if (errno == EINTR) {
                continue;
            }
            log_error("select failed: %s", strerror(errno));
            break;
        }

        if (rc > 0) {
            if (g_ipc_fd >= 0 && FD_ISSET(g_ipc_fd, &rfds)) {
                handle_ipc_event();
            }
            if (g_ctrl_fd >= 0 && FD_ISSET(g_ctrl_fd, &rfds)) {
                handle_ctrl_event();
            }
            if (g_intr_listen_fd >= 0 && FD_ISSET(g_intr_listen_fd, &rfds)) {
                handle_intr_accept();
            }
        }

        dbus_connection_read_write(g_conn, 0);
        while (dbus_connection_dispatch(g_conn) == DBUS_DISPATCH_DATA_REMAINS) {
            /* drain queue */
        }

        /* Deferred HID connect: triggered by D-Bus Connected signal */
        if (g_pending_connect_ms > 0 && g_intr_fd < 0) {
            long long now = monotonic_ms();
            if (now >= g_pending_connect_ms) {
                g_pending_connect_ms = 0;
                connect_hid_to_host(g_pending_bdaddr);
            }
        } else if (g_pending_connect_ms > 0 && g_intr_fd >= 0) {
            /* HID already connected (e.g. via IPC or NewConnection), cancel pending */
            g_pending_connect_ms = 0;
        }
    }

    log_info("Shutting down");
    close_hid_connections();
    close_intr_listener();

    if (g_ipc_fd >= 0) {
        close(g_ipc_fd);
        g_ipc_fd = -1;
    }
    unlink(IPC_SOCK_PATH);

    if (g_conn) {
        dbus_connection_unregister_object_path(g_conn, PROFILE_PATH);
        dbus_connection_unref(g_conn);
        g_conn = NULL;
    }

    log_info("Stopped");
    return 0;
}
