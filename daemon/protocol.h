/*
 * Spotifone IPC Protocol
 *
 * Shared definitions between gattd (BLE GATT HID server) and
 * mic_bridge (HSP-AG audio bridge). Communication via Unix domain socket.
 */

#ifndef SPOTIFONE_PROTOCOL_H
#define SPOTIFONE_PROTOCOL_H

#include <stdint.h>

/* Unix socket path for IPC */
#define SPOTIFONE_SOCK_PATH  "/var/run/spotifone.sock"

/* Message types */
enum sf_msg_type {
    SF_MSG_HID_REPORT    = 0x01,  /* HID keyboard report (8 bytes) */
    SF_MSG_MIC_MUTE      = 0x02,  /* Mute microphone */
    SF_MSG_MIC_UNMUTE    = 0x03,  /* Unmute microphone */
    SF_MSG_STATUS_REQ    = 0x10,  /* Request status */
    SF_MSG_STATUS_RESP   = 0x11,  /* Status response */
    SF_MSG_SHUTDOWN      = 0xFF,  /* Shutdown daemon */
};

/* IPC message header */
struct sf_msg {
    uint8_t  type;       /* sf_msg_type */
    uint8_t  len;        /* payload length */
    uint8_t  payload[];  /* flexible array */
} __attribute__((packed));

/* HID keyboard report (USB HID standard) */
struct sf_hid_report {
    uint8_t modifier;    /* modifier key bits (0xE0-0xE7) */
    uint8_t reserved;    /* always 0 */
    uint8_t keys[6];     /* up to 6 simultaneous keys */
} __attribute__((packed));

/* Status response */
struct sf_status {
    uint8_t  hid_connected;
    uint8_t  audio_connected;
    uint8_t  mic_muted;
    uint8_t  reserved;
} __attribute__((packed));

#endif /* SPOTIFONE_PROTOCOL_H */
