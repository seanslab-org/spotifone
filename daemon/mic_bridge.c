/*
 * Spotifone Bluetooth Mic Bridge (HFP Hands-Free)
 *
 * Registers HFP-HF profile via BlueZ D-Bus, accepts SCO connections from
 * paired Audio Gateway devices (Mac/PC), and bridges ALSA microphone audio
 * over SCO. Controlled via a UNIX datagram socket from button_listener.py.
 *
 * Ported from VibeThing bt_mic_bridge.c — proven working with macOS.
 *
 * Architecture:
 *   - D-Bus: Register HFP-HF profile (UUID 0x111E) via ProfileManager1
 *   - RFCOMM: AT command handler for HFP SLC handshake
 *   - SCO: Listen for incoming SCO connections, bridge ALSA → SCO
 *   - Control: Unix DGRAM socket for PTT start/stop from Python
 *
 * Usage:
 *   mic_bridge [--socket /tmp/spotifone_mic.sock]
 */

#include <dbus/dbus.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* SBC codec for mSBC (Wideband Speech) encoding */
#include <sbc/sbc.h>

/* Bluetooth headers — available on Linux with libbluetooth-dev */
#ifdef __linux__
#include <bluetooth/bluetooth.h>
#include <bluetooth/sco.h>
#include <bluetooth/rfcomm.h>
#define HAS_BLUETOOTH 1
#else
/* Stubs for compilation on non-Linux (e.g. macOS syntax check) */
#define HAS_BLUETOOTH 0
#define BTPROTO_SCO 2
#define BTPROTO_RFCOMM 3
#define SOL_SCO 17
#define SOL_BLUETOOTH 274
#define BT_VOICE 11
typedef struct { unsigned char b[6]; } bdaddr_t;
struct sockaddr_sco { unsigned short sco_family; bdaddr_t sco_bdaddr; };
struct sco_options { unsigned short mtu; };
#define SCO_OPTIONS 1
#endif

/* ---- Spotifone-specific constants ---- */

#define PROFILE_PATH "/org/bluez/spotifone/hfp_hf"
#define AGENT_PATH   "/org/bluez/spotifone/agent"
#define HFP_HF_UUID  "0000111e-0000-1000-8000-00805f9b34fb"

#define CONTROL_SOCKET_DEFAULT "/tmp/spotifone_mic.sock"
#define SCO_MTU_DEFAULT 48

/* PDM mic digital gain — MEMS PDM mics have low sensitivity (~-26 dBFS @94dB SPL).
 * Without amplification, the audio reaching the Mac is near-silent.
 * Gain of 32 = ~30dB boost, bringing normal speech to audible levels. */
#define MIC_GAIN 32

/* Control commands from Python (button_listener.py) */
#define CMD_STOP_STREAMING  0x00
#define CMD_START_STREAMING 0x01
#define CMD_STATUS_QUERY    0x02
#define CMD_CONNECT_AG      0x03  /* Outbound RFCOMM to AG: [0x03, bd[6], channel] */

/* Connection states */
enum bridge_state {
    STATE_IDLE = 0,
    STATE_RFCOMM_CONNECTED,
    STATE_SLC_ESTABLISHED,
    STATE_AUDIO_READY,
    STATE_STREAMING,
};

/* HFP SLC (Service Level Connection) handshake steps.
 * As HF, WE initiate by sending AT commands to the AG (Mac). */
enum slc_step {
    SLC_NONE = 0,
    SLC_BRSF_SENT,
    SLC_BAC_SENT,
    SLC_CIND_TEST_SENT,
    SLC_CIND_READ_SENT,
    SLC_CMER_SENT,
    SLC_CHLD_SENT,
    SLC_COMPLETE,
};

static const char *state_names[] = {
    "IDLE", "RFCOMM_CONNECTED", "SLC_ESTABLISHED", "AUDIO_READY", "STREAMING"
};

/* Global state */
static DBusConnection *dbus_conn = NULL;
static volatile int running = 1;
static volatile enum bridge_state state = STATE_IDLE;
static volatile int streaming = 0;

static int rfcomm_fd = -1;
static int sco_listen_fd = -1;
static int sco_fd = -1;
static int ctrl_sock_fd = -1;
static char ctrl_socket_path[108] = CONTROL_SOCKET_DEFAULT;

static pthread_t audio_thread_id;
static volatile int audio_thread_running = 0;
static pthread_mutex_t state_mutex = PTHREAD_MUTEX_INITIALIZER;

static int sco_mtu = SCO_MTU_DEFAULT;

/* mSBC (Wideband Speech / HFP 1.6+) state.
 * mSBC = modified SBC: 16kHz, mono, 8 subbands, 15 blocks, bitpool 26.
 * Produces 57-byte frames from 120 samples (7.5ms at 16kHz).
 * SCO packet: 2 bytes H2 header + 57 bytes mSBC + 1 byte padding = 60 bytes. */
static int codec_id = 1;           /* 1=CVSD, 2=mSBC */
static sbc_t sbc_encoder;
static int sbc_initialized = 0;
static int h2_seq = 0;
static const unsigned char h2_seq_table[4] = {0x08, 0x38, 0xC8, 0xF8};

/* Connected device path (from NewConnection) */
static char connected_device[256] = {0};

/* SLC handshake state */
static volatile int slc_state = SLC_NONE;
static int ag_features = 0;  /* AG features from AT+BRSF response */

/* Forward declarations */
static void init_msbc_encoder(void);
static int setup_sco_listener_with_voice(unsigned short voice);

/* ---------- Signal handler ---------- */

static void signal_handler(int sig) {
    (void)sig;
    running = 0;
}

/* ---------- Logging ---------- */

static void log_info(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stdout, "[mic_bridge] ");
    vfprintf(stdout, fmt, ap);
    fprintf(stdout, "\n");
    fflush(stdout);
    va_end(ap);
}

static void log_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[mic_bridge ERROR] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    fflush(stderr);
    va_end(ap);
}

/* ---------- State management ---------- */

static void set_state(enum bridge_state new_state) {
    pthread_mutex_lock(&state_mutex);
    enum bridge_state old = state;
    state = new_state;
    pthread_mutex_unlock(&state_mutex);
    log_info("State: %s -> %s", state_names[old], state_names[new_state]);
}

/* ---------- RFCOMM AT command handler (HFP-HF role) ---------- */
/*
 * In HFP, the HF (us) INITIATES the SLC by sending AT commands to the AG (Mac).
 * After SLC is established, we receive unsolicited result codes from the AG.
 */

static void rfcomm_send_at(const char *at_cmd) {
    if (rfcomm_fd < 0) return;
    char buf[256];
    int n = snprintf(buf, sizeof(buf), "%s\r", at_cmd);
    if (n > 0) {
        ssize_t written = write(rfcomm_fd, buf, (size_t)n);
        if (written < 0) {
            log_error("RFCOMM write failed: %s", strerror(errno));
        } else {
            log_info("SLC TX: %s", at_cmd);
        }
    }
}

static void initiate_slc(void) {
    /* Step 1: Send our supported features to the AG.
     * Note: mSBC (bit 7) disabled — kernel 4.9 can't accept Transparent eSCO. */
    slc_state = SLC_BRSF_SENT;
    rfcomm_send_at("AT+BRSF=0");
}

static void advance_slc(const char *response) {
    /* Called when we receive a complete response (containing "OK") from AG */
    switch (slc_state) {
    case SLC_BRSF_SENT:
        /* Parse AG features from +BRSF response */
        {
            const char *p = strstr(response, "+BRSF:");
            if (p) ag_features = atoi(p + 6);
        }
        log_info("SLC: AG features=0x%x", ag_features);
        slc_state = SLC_CIND_TEST_SENT;
        rfcomm_send_at("AT+CIND=?");
        break;

    case SLC_CIND_TEST_SENT:
        slc_state = SLC_CIND_READ_SENT;
        rfcomm_send_at("AT+CIND?");
        break;

    case SLC_CIND_READ_SENT:
        slc_state = SLC_CMER_SENT;
        rfcomm_send_at("AT+CMER=3,0,0,1");
        break;

    case SLC_CMER_SENT:
        /* Check if AG supports 3-way calling (bit 0 of ag_features) */
        if (ag_features & 0x01) {
            slc_state = SLC_CHLD_SENT;
            rfcomm_send_at("AT+CHLD=?");
        } else {
            /* SLC complete without CHLD */
            slc_state = SLC_COMPLETE;
            log_info("SLC established (no CHLD)");
            set_state(STATE_SLC_ESTABLISHED);
            /* Set mic gain to max (0-15) so Mac doesn't default to 0 */
            rfcomm_send_at("AT+VGM=15");
            rfcomm_send_at("AT+VGS=15");
            /* Try Apple extension */
            rfcomm_send_at("AT+XAPL=Spotifone-0001-0001-0100,2");
        }
        break;

    case SLC_CHLD_SENT:
        slc_state = SLC_COMPLETE;
        log_info("SLC established");
        set_state(STATE_SLC_ESTABLISHED);
        /* Set mic gain to max (0-15) so Mac doesn't default to 0 */
        rfcomm_send_at("AT+VGM=15");
        rfcomm_send_at("AT+VGS=15");
        /* Try Apple extension */
        rfcomm_send_at("AT+XAPL=Spotifone-0001-0001-0100,2");
        break;

    default:
        break;
    }
}

/* Accumulation buffer for partial AT responses */
static char rfcomm_buf[1024];
static int rfcomm_buf_len = 0;

static void handle_rfcomm_data(void) {
    char buf[256];
    ssize_t n = read(rfcomm_fd, buf, sizeof(buf) - 1);
    if (n <= 0) {
        log_info("RFCOMM disconnected");
        close(rfcomm_fd);
        rfcomm_fd = -1;
        if (sco_fd >= 0) {
            close(sco_fd);
            sco_fd = -1;
        }
        streaming = 0;
        slc_state = SLC_NONE;
        rfcomm_buf_len = 0;
        set_state(STATE_IDLE);
        return;
    }

    /* Accumulate in buffer */
    if (rfcomm_buf_len + n < (int)sizeof(rfcomm_buf) - 1) {
        memcpy(rfcomm_buf + rfcomm_buf_len, buf, (size_t)n);
        rfcomm_buf_len += (int)n;
        rfcomm_buf[rfcomm_buf_len] = '\0';
    } else {
        /* Buffer overflow — reset */
        rfcomm_buf_len = 0;
        return;
    }

    /* Log raw received data (trimmed) */
    {
        char logbuf[256];
        int len = rfcomm_buf_len < 250 ? rfcomm_buf_len : 250;
        int j = 0;
        for (int i = 0; i < len && j < 250; i++) {
            if (rfcomm_buf[i] == '\r') { logbuf[j++] = '<'; logbuf[j++] = 'C'; logbuf[j++] = 'R'; logbuf[j++] = '>'; }
            else if (rfcomm_buf[i] == '\n') { logbuf[j++] = '<'; logbuf[j++] = 'L'; logbuf[j++] = 'F'; logbuf[j++] = '>'; }
            else logbuf[j++] = rfcomm_buf[i];
        }
        logbuf[j] = '\0';
        log_info("SLC RX: %s", logbuf);
    }

    /* During SLC setup: look for "OK" or "ERROR" to advance */
    if (slc_state != SLC_NONE && slc_state != SLC_COMPLETE) {
        if (strstr(rfcomm_buf, "OK") || strstr(rfcomm_buf, "ERROR")) {
            char response_copy[1024];
            strncpy(response_copy, rfcomm_buf, sizeof(response_copy) - 1);
            response_copy[sizeof(response_copy) - 1] = '\0';
            rfcomm_buf_len = 0;
            rfcomm_buf[0] = '\0';
            advance_slc(response_copy);
        }
        return;
    }

    /* After SLC: handle unsolicited result codes from AG */
    /* Process complete lines (ending with \r or \n) */
    char *line_end;
    while ((line_end = strpbrk(rfcomm_buf, "\r\n")) != NULL) {
        *line_end = '\0';
        char *line = rfcomm_buf;
        /* Skip leading whitespace */
        while (*line == ' ') line++;

        if (strlen(line) > 0) {
            log_info("AG cmd: %s", line);

            /* Handle AG requests after SLC */
            if (strncmp(line, "+BCS:", 5) == 0) {
                /* Codec Selection from AG (HFP 1.6+).
                 * AG tells us which codec to use for SCO audio.
                 * 1=CVSD (8kHz), 2=mSBC (16kHz wideband). */
                int bcs_codec = atoi(line + 5);
                log_info("AG codec selection: +BCS:%d (%s)",
                         bcs_codec, bcs_codec == 2 ? "mSBC" : "CVSD");
                codec_id = bcs_codec;
                if (bcs_codec == 2) {
                    /* mSBC: recreate SCO listener with Transparent voice
                     * BEFORE confirming, so it's ready for the AG's SCO open */
                    setup_sco_listener_with_voice(0x0003);
                    init_msbc_encoder();
                } else {
                    setup_sco_listener_with_voice(0x0060);
                }
                /* Confirm codec selection */
                char at_bcs[32];
                snprintf(at_bcs, sizeof(at_bcs), "AT+BCS=%d", bcs_codec);
                rfcomm_send_at(at_bcs);
            } else if (strncmp(line, "+VGS:", 5) == 0 || strncmp(line, "+VGM:", 5) == 0) {
                /* Volume/gain change — acknowledge */
            } else if (strncmp(line, "+CIEV:", 6) == 0) {
                /* Indicator event — just log */
                log_info("AG indicator: %s", line + 6);
            }
        }

        /* Shift remaining data */
        size_t consumed = (size_t)(line_end - rfcomm_buf) + 1;
        /* Skip consecutive \r\n */
        while (consumed < (size_t)rfcomm_buf_len &&
               (rfcomm_buf[consumed] == '\r' || rfcomm_buf[consumed] == '\n')) {
            consumed++;
        }
        rfcomm_buf_len -= (int)consumed;
        if (rfcomm_buf_len > 0) {
            memmove(rfcomm_buf, rfcomm_buf + consumed, (size_t)rfcomm_buf_len);
        }
        rfcomm_buf[rfcomm_buf_len] = '\0';
    }
}

/* ---------- mSBC encoder ---------- */

static void init_msbc_encoder(void) {
    if (sbc_initialized) {
        sbc_finish(&sbc_encoder);
    }
    /* sbc_init_msbc sets all mSBC parameters automatically:
     * 16kHz, mono, 8 subbands, 15 blocks, loudness, bitpool 26 */
    sbc_init_msbc(&sbc_encoder, 0);
    sbc_encoder.endian = SBC_LE;
    sbc_initialized = 1;
    h2_seq = 0;
    log_info("mSBC encoder initialized (codesize=%zu, frame_len=%zu)",
             sbc_get_codesize(&sbc_encoder), sbc_get_frame_length(&sbc_encoder));
}

/* ---------- SCO socket setup ---------- */

static int setup_sco_listener_with_voice(unsigned short voice) {
#if HAS_BLUETOOTH
    struct sockaddr_sco addr;

    if (sco_listen_fd >= 0) {
        close(sco_listen_fd);
        sco_listen_fd = -1;
    }

    sco_listen_fd = socket(PF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_SCO);
    if (sco_listen_fd < 0) {
        log_error("SCO socket failed: %s", strerror(errno));
        return -1;
    }

    /* Set voice encoding:
     * 0x0060 = CVSD (8kHz narrowband)
     * 0x0003 = Transparent (mSBC 16kHz wideband — raw codec data, no kernel processing) */
    if (setsockopt(sco_listen_fd, SOL_BLUETOOTH, BT_VOICE, &voice, sizeof(voice)) < 0) {
        /* Non-fatal — some kernels don't support BT_VOICE */
        log_info("BT_VOICE setsockopt not supported (non-fatal)");
    }
    log_info("SCO voice set to 0x%04x (%s)", voice,
             voice == 0x0060 ? "CVSD" : voice == 0x0003 ? "Transparent/mSBC" : "unknown");

    memset(&addr, 0, sizeof(addr));
    addr.sco_family = AF_BLUETOOTH;
    /* BDADDR_ANY = all zeros */
    memset(&addr.sco_bdaddr, 0, sizeof(bdaddr_t));

    if (bind(sco_listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        log_error("SCO bind failed: %s", strerror(errno));
        close(sco_listen_fd);
        sco_listen_fd = -1;
        return -1;
    }

    if (listen(sco_listen_fd, 1) < 0) {
        log_error("SCO listen failed: %s", strerror(errno));
        close(sco_listen_fd);
        sco_listen_fd = -1;
        return -1;
    }

    log_info("SCO listener ready (voice=0x%04x)", voice);
    return 0;
#else
    log_info("SCO not available (non-Linux build)");
    return -1;
#endif
}

static int setup_sco_listener(void) {
    /* Default to CVSD; will be recreated with Transparent if mSBC negotiated */
    return setup_sco_listener_with_voice(0x0060);
}

static void accept_sco_connection(void) {
#if HAS_BLUETOOTH
    struct sockaddr_sco addr;
    socklen_t addrlen = sizeof(addr);

    int fd = accept(sco_listen_fd, (struct sockaddr *)&addr, &addrlen);
    if (fd < 0) {
        log_error("SCO accept failed: %s", strerror(errno));
        return;
    }

    /* Close any existing SCO connection */
    if (sco_fd >= 0) {
        close(sco_fd);
    }
    sco_fd = fd;

    /* Keep socket BLOCKING for reliable writes.
     * The BCM4345C0 CVSD encoder consumes 16-bit S16_LE samples at 8kHz,
     * draining 16,000 bytes/sec from HCI. Increase SO_SNDBUF to allow
     * sustained 16,000 bytes/sec throughput (default SCO buffer is small
     * and throttles to ~8,000 bytes/sec, causing half-speed audio).
     *
     * NOTE: With timer-based write pacing in audio_thread_func(), this
     * large buffer may no longer be needed. If pacing alone fixes the
     * distortion, try removing this override to let the kernel's
     * natural flow control work. */
    int sndbuf = 32768;
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    /* Get MTU */
    struct sco_options opts;
    socklen_t optlen = sizeof(opts);
    if (getsockopt(sco_fd, SOL_SCO, SCO_OPTIONS, &opts, &optlen) == 0) {
        sco_mtu = opts.mtu;
    } else {
        sco_mtu = SCO_MTU_DEFAULT;
    }

    /* Read actual voice setting to determine codec (CVSD=0x0060, Transparent/mSBC=0x0003) */
    unsigned short voice_setting = 0;
    socklen_t vslen = sizeof(voice_setting);
    if (getsockopt(sco_fd, SOL_BLUETOOTH, BT_VOICE, &voice_setting, &vslen) == 0) {
        log_info("SCO connected (MTU=%d, voice=0x%04x%s)",
                 sco_mtu, voice_setting,
                 voice_setting == 0x0060 ? " CVSD/8kHz" :
                 voice_setting == 0x0003 ? " Transparent/mSBC/16kHz" : " unknown");
    } else {
        log_info("SCO connected (MTU=%d, voice=unknown)", sco_mtu);
    }

    /* Auto-start streaming when SCO connects.
     * A headset should always pass through audio.
     * PTT button toggles streaming on/off (mute/unmute). */
    streaming = 1;
    set_state(STATE_STREAMING);
#endif
}

/* ---------- Audio thread (ALSA → SCO) ---------- */

static void *audio_thread_func(void *arg) {
    (void)arg;
    audio_thread_running = 1;
    log_info("Audio thread started");

    /*
     * Use arecord subprocess for mic capture (avoids ALSA library issues).
     * arecord produces 8kHz S16_LE, but delivers in ALSA period-sized bursts.
     * SCO writes are paced by clock_nanosleep() to one packet per period,
     * smoothing out arecord's bursty delivery to the BCM4345C0 CVSD FIFO.
     * When not streaming, we write silence with usleep()-based pacing.
     *
     * IMPORTANT: Must drain incoming SCO data (Mac→us) to prevent kernel
     * buffer overflow and "Connection reset by peer". SCO is bidirectional.
     */
    FILE *arecord_pipe = NULL;
    unsigned char write_buf[512];  /* 4*MTU: holds 16kHz S16_LE before downsample+convert */
    unsigned char sco_buf[256];   /* MTU-sized: 8-bit 8kHz samples for SCO write */
    unsigned char drain_buf[256];
    unsigned long total_written = 0;
    unsigned long log_counter = 0;
    struct timespec ts_start;
    int ts_started = 0;

    /* Timer-based write pacing: ensures one SCO packet per period,
     * regardless of bursty pipe delivery from arecord.
     * Without pacing, ALSA period-sized bursts cause back-to-back writes
     * followed by gaps, overwhelming/starving the BCM4345C0 CVSD FIFO. */
    struct timespec next_write_time = {0, 0};
    int pacing_active = 0;

    /* Diagnostics: track partial reads (pipe boundary effects) */
    unsigned long partial_reads = 0;
    unsigned long odd_byte_reads = 0;

    while (running && audio_thread_running) {
        int cur_sco = sco_fd;
        int cur_mtu = sco_mtu;

        if (cur_sco < 0) {
            /* No SCO connection — close arecord if open, sleep */
            if (arecord_pipe) {
                pclose(arecord_pipe);
                arecord_pipe = NULL;
                pacing_active = 0;
            }
            usleep(50000);
            continue;
        }

        /* Drain incoming SCO data from Mac (non-blocking recv).
         * Without this, kernel SCO buffer fills → connection drops.
         * Socket is blocking for write() but we use MSG_DONTWAIT here. */
        while (recv(cur_sco, drain_buf, sizeof(drain_buf), MSG_DONTWAIT) > 0) {
            /* discard — we don't use speaker audio */
        }

        if (streaming) {
            /* Start arecord if not running — 8kHz S16_LE for CVSD */
            if (!arecord_pipe) {
                arecord_pipe = popen(
                    "arecord -D plughw:0,0 -f S16_LE -r 8000 -c 1 -t raw 2>/dev/null",
                    "r");
                if (!arecord_pipe) {
                    log_error("arecord failed to start");
                    usleep(50000);
                    continue;
                }
                log_info("arecord capture started (8kHz S16_LE mono)");
            }

            /* ---- CVSD mode: S16_LE direct to SCO with write pacing ----
             *
             * Problem: arecord delivers data in ALSA period-sized bursts
             * (e.g. 1024 bytes every 64ms). Without pacing, we'd drain
             * each burst in back-to-back writes, then have a 60ms gap.
             * The BCM4345C0 CVSD encoder FIFO can't smooth this out —
             * it underflows during gaps and overflows during bursts.
             *
             * Solution: Timer-based pacing. After each fread(), wait
             * until the scheduled write time before calling write().
             * Target: one MTU-sized write every (MTU/16000) seconds
             * (4ms for MTU=64, since S16_LE at 8kHz = 16,000 bytes/sec).
             *
             * If fread() blocked (pipe was empty, waiting for next ALSA
             * burst), the timer will be in the past — we reset it to
             * "now" so the first write after a burst is immediate, then
             * subsequent writes from the same burst are paced evenly.
             */
            size_t to_read = (size_t)cur_mtu;
            if (to_read > sizeof(write_buf)) to_read = sizeof(write_buf);
            size_t n = fread(write_buf, 1, to_read, arecord_pipe);
            if (n > 0) {
                /* Guard: force even byte count for S16_LE alignment.
                 * fread() from pipe may return fewer bytes than requested
                 * (especially at ALSA period boundaries). An odd byte count
                 * would misalign S16_LE samples — one "sample" becomes
                 * composed of bytes from two different real samples. */
                if (n < to_read) partial_reads++;
                if (n & 1) {
                    odd_byte_reads++;
                    n &= ~(size_t)1;
                }
                if (n == 0) continue;

                /* Apply digital gain to S16_LE samples */
                int16_t *pcm = (int16_t *)write_buf;
                size_t num_samples = n / 2;
                for (size_t i = 0; i < num_samples; i++) {
                    int32_t amplified = (int32_t)pcm[i] * MIC_GAIN;
                    if (amplified > 32767) amplified = 32767;
                    if (amplified < -32768) amplified = -32768;
                    pcm[i] = (int16_t)amplified;
                }

                /* --- Write pacing --- */
                if (!pacing_active) {
                    /* First write after arecord start — init timer */
                    clock_gettime(CLOCK_MONOTONIC, &next_write_time);
                    pacing_active = 1;
                } else {
                    /* Check if timer fell behind (fread blocked on empty pipe).
                     * If so, reset to now — don't burst-catch-up. */
                    struct timespec now;
                    clock_gettime(CLOCK_MONOTONIC, &now);
                    long long diff_ns = (long long)(now.tv_sec - next_write_time.tv_sec) * 1000000000LL +
                                       (now.tv_nsec - next_write_time.tv_nsec);
                    long long period_ns = (long long)n * 62500LL; /* n * 1e9 / 16000 */
                    if (diff_ns > period_ns) {
                        next_write_time = now;
                    }
                }

                /* Sleep until scheduled write time */
                clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next_write_time, NULL);

                /* Write S16_LE directly — controller handles CVSD encoding */
                ssize_t written = write(cur_sco, write_buf, n);
                if (written < 0) {
                    if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
                    log_error("SCO write failed: %s", strerror(errno));
                    close(cur_sco);
                    sco_fd = -1;
                    pacing_active = 0;
                    set_state(STATE_RFCOMM_CONNECTED);
                    continue;
                }
                total_written += (unsigned long)written;

                /* Advance timer by exact write duration:
                 * n bytes of S16_LE at 8kHz = n/16000 seconds = n*62500 ns */
                {
                    long ns_advance = (long)n * 62500L;
                    next_write_time.tv_nsec += ns_advance;
                    if (next_write_time.tv_nsec >= 1000000000L) {
                        next_write_time.tv_sec++;
                        next_write_time.tv_nsec -= 1000000000L;
                    }
                }
            } else {
                pclose(arecord_pipe);
                arecord_pipe = NULL;
                pacing_active = 0;
                usleep(10000);
            }
        } else {
            /* Not streaming — close arecord, send silence with pacing */
            if (arecord_pipe) {
                pclose(arecord_pipe);
                arecord_pipe = NULL;
                pacing_active = 0;
                log_info("arecord capture stopped");
            }

            memset(write_buf, 0, (size_t)cur_mtu);
            ssize_t written = write(cur_sco, write_buf, (size_t)cur_mtu);
            if (written < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) { usleep(4000); continue; }
                log_error("SCO write (silence) failed: %s", strerror(errno));
                close(cur_sco);
                sco_fd = -1;
                set_state(STATE_RFCOMM_CONNECTED);
                continue;
            }
            /* Pace silence: MTU bytes of S16_LE at 8kHz = MTU/16000 sec ≈ 4ms */
            usleep(4000);
        }

        /* Periodic status log (~every 5 seconds) */
        log_counter++;
        if (total_written > 0 && !ts_started) {
            clock_gettime(CLOCK_MONOTONIC, &ts_start);
            ts_started = 1;
        }
        if (log_counter % 1250 == 0) {
            struct timespec ts_now;
            clock_gettime(CLOCK_MONOTONIC, &ts_now);
            double elapsed = (ts_now.tv_sec - ts_start.tv_sec) +
                             (ts_now.tv_nsec - ts_start.tv_nsec) / 1e9;
            double rate = elapsed > 0.1 ? total_written / elapsed : 0;
            log_info("Audio: streaming=%d, mtu=%d, written=%lu, %.0f B/s, "
                     "partial=%lu, odd=%lu, pacing=%s",
                     streaming, cur_mtu, total_written, rate,
                     partial_reads, odd_byte_reads,
                     pacing_active ? "on" : "off");
        }
    }

    if (arecord_pipe) pclose(arecord_pipe);

    log_info("Audio thread stopped");
    audio_thread_running = 0;
    return NULL;
}

/* ---------- Outbound RFCOMM connect ---------- */

static int connect_rfcomm_outbound(const unsigned char *bdaddr, uint8_t channel) {
#if HAS_BLUETOOTH
    struct sockaddr_rc local_addr, remote_addr;

    if (state != STATE_IDLE) {
        log_info("Outbound connect skipped: already in state %s", state_names[state]);
        return -1;
    }

    int fd = socket(AF_BLUETOOTH, SOCK_STREAM, BTPROTO_RFCOMM);
    if (fd < 0) {
        log_error("RFCOMM socket failed: %s", strerror(errno));
        return -1;
    }

    /* Bind to local adapter */
    memset(&local_addr, 0, sizeof(local_addr));
    local_addr.rc_family = AF_BLUETOOTH;
    local_addr.rc_channel = 0;  /* Let kernel assign */
    memset(&local_addr.rc_bdaddr, 0, sizeof(bdaddr_t));
    if (bind(fd, (struct sockaddr *)&local_addr, sizeof(local_addr)) < 0) {
        log_error("RFCOMM bind failed: %s", strerror(errno));
        close(fd);
        return -1;
    }

    /* Connect to remote AG */
    memset(&remote_addr, 0, sizeof(remote_addr));
    remote_addr.rc_family = AF_BLUETOOTH;
    remote_addr.rc_channel = channel;
    memcpy(&remote_addr.rc_bdaddr, bdaddr, 6);

    log_info("Outbound RFCOMM connecting to %02X:%02X:%02X:%02X:%02X:%02X ch %d",
             bdaddr[5], bdaddr[4], bdaddr[3], bdaddr[2], bdaddr[1], bdaddr[0],
             channel);

    if (connect(fd, (struct sockaddr *)&remote_addr, sizeof(remote_addr)) < 0) {
        log_error("RFCOMM connect failed: %s", strerror(errno));
        close(fd);
        return -1;
    }

    log_info("Outbound RFCOMM connected");

    /* Close any existing RFCOMM */
    if (rfcomm_fd >= 0) {
        close(rfcomm_fd);
    }
    rfcomm_fd = fd;

    snprintf(connected_device, sizeof(connected_device),
             "/org/bluez/hci0/dev_%02X_%02X_%02X_%02X_%02X_%02X",
             bdaddr[5], bdaddr[4], bdaddr[3], bdaddr[2], bdaddr[1], bdaddr[0]);

    set_state(STATE_RFCOMM_CONNECTED);
    return 0;
#else
    (void)bdaddr; (void)channel;
    log_error("RFCOMM not available (non-Linux build)");
    return -1;
#endif
}

/* ---------- Control socket ---------- */

static int setup_control_socket(const char *path) {
    struct sockaddr_un addr;
    ctrl_sock_fd = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (ctrl_sock_fd < 0) {
        log_error("Control socket failed: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);
    unlink(path);

    if (bind(ctrl_sock_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        log_error("Control socket bind failed: %s", strerror(errno));
        close(ctrl_sock_fd);
        ctrl_sock_fd = -1;
        return -1;
    }

    /* Allow group/other to send commands */
    chmod(path, 0666);

    log_info("Control socket: %s", path);
    return 0;
}

static void handle_control_command(void) {
    struct sockaddr_un from;
    socklen_t fromlen = sizeof(from);
    unsigned char cmd;

    ssize_t n = recvfrom(ctrl_sock_fd, &cmd, 1, 0,
                         (struct sockaddr *)&from, &fromlen);
    if (n != 1) return;

    switch (cmd) {
    case CMD_START_STREAMING:
        log_info("PTT START");
        streaming = 1;
        if (state == STATE_AUDIO_READY || state == STATE_SLC_ESTABLISHED) {
            set_state(STATE_STREAMING);
        }
        break;

    case CMD_STOP_STREAMING:
        log_info("PTT STOP");
        streaming = 0;
        if (state == STATE_STREAMING) {
            set_state(STATE_AUDIO_READY);
        }
        break;

    case CMD_STATUS_QUERY: {
        /* Respond with current state byte */
        unsigned char resp = (unsigned char)state;
        sendto(ctrl_sock_fd, &resp, 1, 0,
               (struct sockaddr *)&from, fromlen);
        break;
    }

    case CMD_CONNECT_AG: {
        /* Outbound RFCOMM connect: read remaining 7 bytes [bd_addr[6], channel] */
        unsigned char payload[7];
        ssize_t r = recvfrom(ctrl_sock_fd, payload, sizeof(payload), 0, NULL, NULL);
        if (r == 7) {
            int rc = connect_rfcomm_outbound(payload, payload[6]);
            unsigned char resp = (rc == 0) ? 0x01 : 0x00;
            sendto(ctrl_sock_fd, &resp, 1, 0,
                   (struct sockaddr *)&from, fromlen);
        } else {
            log_error("CMD_CONNECT_AG: expected 7 bytes, got %zd", r);
            unsigned char resp = 0x00;
            sendto(ctrl_sock_fd, &resp, 1, 0,
                   (struct sockaddr *)&from, fromlen);
        }
        break;
    }

    default:
        log_info("Unknown control command: 0x%02x", cmd);
        break;
    }
}

/* ---------- D-Bus Profile1 interface ---------- */

static const char *introspect_profile =
    "<!DOCTYPE node PUBLIC \"-//freedesktop//DTD D-BUS Object Introspection 1.0//EN\"\n"
    " \"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd\">\n"
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

static DBusHandlerResult profile_message_handler(
    DBusConnection *connection, DBusMessage *message, void *user_data)
{
    (void)user_data;
    const char *iface = dbus_message_get_interface(message);
    const char *member = dbus_message_get_member(message);
    DBusMessage *reply = NULL;

    if (!iface || !member) {
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }

    /* Introspection */
    if (strcmp(iface, "org.freedesktop.DBus.Introspectable") == 0 &&
        strcmp(member, "Introspect") == 0) {
        reply = dbus_message_new_method_return(message);
        dbus_message_append_args(reply,
            DBUS_TYPE_STRING, &introspect_profile,
            DBUS_TYPE_INVALID);
        dbus_connection_send(connection, reply, NULL);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    if (strcmp(iface, "org.bluez.Profile1") != 0) {
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }

    if (strcmp(member, "NewConnection") == 0) {
        /* NewConnection(device, fd, properties) */
        const char *device = NULL;
        int fd = -1;
        DBusMessageIter iter;

        if (!dbus_message_iter_init(message, &iter)) {
            reply = dbus_message_new_error(message,
                "org.freedesktop.DBus.Error.InvalidArgs", "Missing args");
            dbus_connection_send(connection, reply, NULL);
            dbus_message_unref(reply);
            return DBUS_HANDLER_RESULT_HANDLED;
        }

        /* Get device object path */
        if (dbus_message_iter_get_arg_type(&iter) == DBUS_TYPE_OBJECT_PATH) {
            dbus_message_iter_get_basic(&iter, &device);
        }
        dbus_message_iter_next(&iter);

        /* Get file descriptor (Unix FD) */
        if (dbus_message_iter_get_arg_type(&iter) == DBUS_TYPE_UNIX_FD) {
            dbus_message_iter_get_basic(&iter, &fd);
        }

        if (device && fd >= 0) {
            log_info("NewConnection: device=%s fd=%d", device, fd);
            strncpy(connected_device, device, sizeof(connected_device) - 1);

            /* Close any existing RFCOMM connection */
            if (rfcomm_fd >= 0) {
                close(rfcomm_fd);
            }
            rfcomm_fd = fd;
            set_state(STATE_RFCOMM_CONNECTED);
        } else {
            log_error("NewConnection: invalid args (device=%s, fd=%d)",
                      device ? device : "null", fd);
        }

        reply = dbus_message_new_method_return(message);
        dbus_connection_send(connection, reply, NULL);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    if (strcmp(member, "RequestDisconnection") == 0) {
        const char *device = NULL;
        dbus_message_get_args(message, NULL,
            DBUS_TYPE_OBJECT_PATH, &device,
            DBUS_TYPE_INVALID);
        log_info("RequestDisconnection: %s", device ? device : "unknown");

        /* Clean up */
        streaming = 0;
        slc_state = SLC_NONE;
        rfcomm_buf_len = 0;
        if (rfcomm_fd >= 0) {
            close(rfcomm_fd);
            rfcomm_fd = -1;
        }
        if (sco_fd >= 0) {
            close(sco_fd);
            sco_fd = -1;
        }
        connected_device[0] = '\0';
        set_state(STATE_IDLE);

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

/* ---------- D-Bus: Register HFP-HF profile ---------- */

static int register_hfp_profile(void) {
    DBusMessage *msg, *reply;
    DBusMessageIter iter, dict, entry, variant;
    DBusError error;

    dbus_error_init(&error);

    msg = dbus_message_new_method_call(
        "org.bluez",
        "/org/bluez",
        "org.bluez.ProfileManager1",
        "RegisterProfile");

    if (!msg) {
        log_error("Failed to create RegisterProfile message");
        return -1;
    }

    dbus_message_iter_init_append(msg, &iter);

    /* Object path */
    const char *path = PROFILE_PATH;
    dbus_message_iter_append_basic(&iter, DBUS_TYPE_OBJECT_PATH, &path);

    /* UUID */
    const char *uuid = HFP_HF_UUID;
    dbus_message_iter_append_basic(&iter, DBUS_TYPE_STRING, &uuid);

    /* Options dict */
    dbus_message_iter_open_container(&iter, DBUS_TYPE_ARRAY, "{sv}", &dict);

    /* AutoConnect = true */
    {
        const char *key = "AutoConnect";
        dbus_bool_t val = TRUE;
        dbus_message_iter_open_container(&dict, DBUS_TYPE_DICT_ENTRY, NULL, &entry);
        dbus_message_iter_append_basic(&entry, DBUS_TYPE_STRING, &key);
        dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, "b", &variant);
        dbus_message_iter_append_basic(&variant, DBUS_TYPE_BOOLEAN, &val);
        dbus_message_iter_close_container(&entry, &variant);
        dbus_message_iter_close_container(&dict, &entry);
    }

    /* Name = "Spotifone" */
    {
        const char *key = "Name";
        const char *val = "Spotifone";
        dbus_message_iter_open_container(&dict, DBUS_TYPE_DICT_ENTRY, NULL, &entry);
        dbus_message_iter_append_basic(&entry, DBUS_TYPE_STRING, &key);
        dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, "s", &variant);
        dbus_message_iter_append_basic(&variant, DBUS_TYPE_STRING, &val);
        dbus_message_iter_close_container(&entry, &variant);
        dbus_message_iter_close_container(&dict, &entry);
    }

    dbus_message_iter_close_container(&iter, &dict);

    /* Send with reply */
    DBusPendingCall *pending = NULL;
    if (!dbus_connection_send_with_reply(dbus_conn, msg, &pending, 5000) || !pending) {
        dbus_message_unref(msg);
        log_error("RegisterProfile failed to send");
        return -1;
    }
    dbus_connection_flush(dbus_conn);
    dbus_message_unref(msg);

    /* Wait for reply */
    while (!dbus_pending_call_get_completed(pending)) {
        dbus_connection_read_write_dispatch(dbus_conn, 100);
    }

    reply = dbus_pending_call_steal_reply(pending);
    dbus_pending_call_unref(pending);

    if (!reply) {
        log_error("RegisterProfile: no reply");
        return -1;
    }

    if (dbus_message_get_type(reply) == DBUS_MESSAGE_TYPE_ERROR) {
        const char *err_msg = NULL;
        dbus_message_get_args(reply, NULL, DBUS_TYPE_STRING, &err_msg, DBUS_TYPE_INVALID);
        log_error("RegisterProfile failed: %s", err_msg ? err_msg : "unknown error");
        dbus_message_unref(reply);
        return -1;
    }

    dbus_message_unref(reply);
    log_info("HFP-HF profile registered (UUID %s)", HFP_HF_UUID);
    return 0;
}

/* ---------- D-Bus Agent1 interface (NoInputNoOutput auto-pair) ---------- */

static const char *introspect_agent =
    "<!DOCTYPE node PUBLIC \"-//freedesktop//DTD D-BUS Object Introspection 1.0//EN\"\n"
    " \"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd\">\n"
    "<node>"
    " <interface name='org.bluez.Agent1'>"
    "  <method name='Release'/>"
    "  <method name='RequestPinCode'>"
    "   <arg name='device' type='o' direction='in'/>"
    "   <arg name='pincode' type='s' direction='out'/>"
    "  </method>"
    "  <method name='RequestPasskey'>"
    "   <arg name='device' type='o' direction='in'/>"
    "   <arg name='passkey' type='u' direction='out'/>"
    "  </method>"
    "  <method name='DisplayPasskey'>"
    "   <arg name='device' type='o' direction='in'/>"
    "   <arg name='passkey' type='u' direction='in'/>"
    "   <arg name='entered' type='q' direction='in'/>"
    "  </method>"
    "  <method name='RequestConfirmation'>"
    "   <arg name='device' type='o' direction='in'/>"
    "   <arg name='passkey' type='u' direction='in'/>"
    "  </method>"
    "  <method name='RequestAuthorization'>"
    "   <arg name='device' type='o' direction='in'/>"
    "  </method>"
    "  <method name='AuthorizeService'>"
    "   <arg name='device' type='o' direction='in'/>"
    "   <arg name='uuid' type='s' direction='in'/>"
    "  </method>"
    "  <method name='Cancel'/>"
    " </interface>"
    " <interface name='org.freedesktop.DBus.Introspectable'>"
    "  <method name='Introspect'>"
    "   <arg name='data' type='s' direction='out'/>"
    "  </method>"
    " </interface>"
    "</node>";

static DBusHandlerResult agent_message_handler(
    DBusConnection *connection, DBusMessage *message, void *user_data)
{
    (void)user_data;
    const char *iface = dbus_message_get_interface(message);
    const char *member = dbus_message_get_member(message);
    DBusMessage *reply = NULL;

    if (!iface || !member) {
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }

    if (strcmp(iface, "org.freedesktop.DBus.Introspectable") == 0 &&
        strcmp(member, "Introspect") == 0) {
        reply = dbus_message_new_method_return(message);
        dbus_message_append_args(reply,
            DBUS_TYPE_STRING, &introspect_agent,
            DBUS_TYPE_INVALID);
        dbus_connection_send(connection, reply, NULL);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    if (strcmp(iface, "org.bluez.Agent1") != 0) {
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }

    if (strcmp(member, "RequestPinCode") == 0) {
        log_info("Agent: RequestPinCode -> 0000");
        reply = dbus_message_new_method_return(message);
        const char *pin = "0000";
        dbus_message_append_args(reply, DBUS_TYPE_STRING, &pin, DBUS_TYPE_INVALID);
    } else if (strcmp(member, "RequestPasskey") == 0) {
        log_info("Agent: RequestPasskey -> 0");
        reply = dbus_message_new_method_return(message);
        dbus_uint32_t passkey = 0;
        dbus_message_append_args(reply, DBUS_TYPE_UINT32, &passkey, DBUS_TYPE_INVALID);
    } else if (strcmp(member, "RequestConfirmation") == 0) {
        log_info("Agent: RequestConfirmation -> accepted");
        reply = dbus_message_new_method_return(message);
    } else if (strcmp(member, "RequestAuthorization") == 0) {
        log_info("Agent: RequestAuthorization -> accepted");
        reply = dbus_message_new_method_return(message);
    } else if (strcmp(member, "AuthorizeService") == 0) {
        log_info("Agent: AuthorizeService -> accepted");
        reply = dbus_message_new_method_return(message);
    } else if (strcmp(member, "DisplayPasskey") == 0) {
        log_info("Agent: DisplayPasskey (ignored)");
        reply = dbus_message_new_method_return(message);
    } else if (strcmp(member, "Release") == 0) {
        log_info("Agent: Released");
        reply = dbus_message_new_method_return(message);
    } else if (strcmp(member, "Cancel") == 0) {
        log_info("Agent: Cancel");
        reply = dbus_message_new_method_return(message);
    }

    if (reply) {
        dbus_connection_send(connection, reply, NULL);
        dbus_connection_flush(connection);
        dbus_message_unref(reply);
        return DBUS_HANDLER_RESULT_HANDLED;
    }

    return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
}

static int register_agent(void) {
    DBusMessage *msg, *reply;
    DBusError error;
    DBusPendingCall *pending = NULL;

    dbus_error_init(&error);

    /* RegisterAgent(path, capability) */
    msg = dbus_message_new_method_call(
        "org.bluez", "/org/bluez",
        "org.bluez.AgentManager1", "RegisterAgent");
    if (!msg) return -1;

    const char *path = AGENT_PATH;
    const char *capability = "NoInputNoOutput";
    dbus_message_append_args(msg,
        DBUS_TYPE_OBJECT_PATH, &path,
        DBUS_TYPE_STRING, &capability,
        DBUS_TYPE_INVALID);

    if (!dbus_connection_send_with_reply(dbus_conn, msg, &pending, 5000) || !pending) {
        dbus_message_unref(msg);
        return -1;
    }
    dbus_connection_flush(dbus_conn);
    dbus_message_unref(msg);

    while (!dbus_pending_call_get_completed(pending)) {
        dbus_connection_read_write_dispatch(dbus_conn, 100);
    }
    reply = dbus_pending_call_steal_reply(pending);
    dbus_pending_call_unref(pending);

    if (!reply || dbus_message_get_type(reply) == DBUS_MESSAGE_TYPE_ERROR) {
        const char *err = NULL;
        if (reply) {
            dbus_message_get_args(reply, NULL, DBUS_TYPE_STRING, &err, DBUS_TYPE_INVALID);
            log_error("RegisterAgent failed: %s", err ? err : "unknown");
            dbus_message_unref(reply);
        }
        return -1;
    }
    dbus_message_unref(reply);

    /* RequestDefaultAgent(path) */
    msg = dbus_message_new_method_call(
        "org.bluez", "/org/bluez",
        "org.bluez.AgentManager1", "RequestDefaultAgent");
    if (!msg) return -1;

    dbus_message_append_args(msg,
        DBUS_TYPE_OBJECT_PATH, &path,
        DBUS_TYPE_INVALID);

    if (!dbus_connection_send_with_reply(dbus_conn, msg, &pending, 5000) || !pending) {
        dbus_message_unref(msg);
        return -1;
    }
    dbus_connection_flush(dbus_conn);
    dbus_message_unref(msg);

    while (!dbus_pending_call_get_completed(pending)) {
        dbus_connection_read_write_dispatch(dbus_conn, 100);
    }
    reply = dbus_pending_call_steal_reply(pending);
    dbus_pending_call_unref(pending);
    if (reply) dbus_message_unref(reply);

    log_info("Pairing agent registered (NoInputNoOutput)");
    return 0;
}

/* ---------- Wait for BlueZ on D-Bus ---------- */

static int wait_for_bluez(int timeout_ms) {
    DBusError error;
    int waited = 0;
    dbus_error_init(&error);
    while (waited < timeout_ms) {
        dbus_bool_t has_owner = dbus_bus_name_has_owner(dbus_conn, "org.bluez", &error);
        if (dbus_error_is_set(&error)) {
            dbus_error_free(&error);
        } else if (has_owner) {
            return 0;
        }
        usleep(100 * 1000);
        waited += 100;
    }
    return -1;
}

/* ---------- Main ---------- */

int main(int argc, char **argv) {
    DBusError error;
    const char *bus_name = "org.spotifone.mic";

    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--socket") == 0 && i + 1 < argc) {
            strncpy(ctrl_socket_path, argv[i + 1], sizeof(ctrl_socket_path) - 1);
            i++;
        }
    }

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Connect to system D-Bus */
    dbus_error_init(&error);
    dbus_conn = dbus_bus_get(DBUS_BUS_SYSTEM, &error);
    if (dbus_error_is_set(&error)) {
        log_error("D-Bus connection error: %s", error.message);
        dbus_error_free(&error);
        return 1;
    }
    if (!dbus_conn) {
        log_error("Failed to connect to D-Bus");
        return 1;
    }

    /* Request bus name */
    dbus_bus_request_name(dbus_conn, bus_name, DBUS_NAME_FLAG_REPLACE_EXISTING, &error);
    if (dbus_error_is_set(&error)) {
        log_error("D-Bus name request error: %s", error.message);
        dbus_error_free(&error);
        return 1;
    }

    /* Register Profile1 object path */
    DBusObjectPathVTable profile_vtable = {
        .message_function = profile_message_handler,
    };
    if (!dbus_connection_register_object_path(dbus_conn, PROFILE_PATH, &profile_vtable, NULL)) {
        log_error("Failed to register D-Bus object path %s", PROFILE_PATH);
        return 1;
    }

    /* Register Agent1 object path */
    DBusObjectPathVTable agent_vtable = {
        .message_function = agent_message_handler,
    };
    if (!dbus_connection_register_object_path(dbus_conn, AGENT_PATH, &agent_vtable, NULL)) {
        log_error("Failed to register D-Bus object path %s", AGENT_PATH);
        return 1;
    }

    /*
     * Cypress/Broadcom vendor command: route SCO audio through HCI (Transport)
     * instead of the hardware PCM/I2S interface.
     * Write_SCO_PCM_Int_Param (OGF=0x3F, OCF=0x001C):
     *   sco_routing=1 (HCI), pcm_rate=0, frame_type=0, sync_mode=0, clock_mode=0
     * Without this, the BT controller ignores SCO data from the HCI and uses
     * its PCM pins (which have nothing connected on the Car Thing).
     */
    {
        /* Run with timeout to avoid hanging if BT chip not ready */
        pid_t pid = fork();
        if (pid == 0) {
            /* Child: exec the hcitool command */
            close(STDIN_FILENO);
            int devnull = open("/dev/null", O_WRONLY);
            if (devnull >= 0) { dup2(devnull, STDOUT_FILENO); dup2(devnull, STDERR_FILENO); close(devnull); }
            execlp("hcitool", "hcitool", "cmd", "0x3f", "0x001c", "01", "00", "00", "00", "00", NULL);
            _exit(1);
        } else if (pid > 0) {
            /* Parent: wait with timeout (2 seconds) */
            int waited = 0;
            int status;
            while (waited < 2000) {
                pid_t w = waitpid(pid, &status, WNOHANG);
                if (w > 0) {
                    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
                        log_info("SCO routing set to HCI (Cypress vendor cmd)");
                    } else {
                        log_info("SCO routing vendor cmd failed (status=%d)", status);
                    }
                    break;
                }
                usleep(10000);
                waited += 10;
            }
            if (waited >= 2000) {
                kill(pid, SIGKILL);
                waitpid(pid, NULL, 0);
                log_info("SCO routing vendor cmd timed out");
            }
        }
    }

    /* Wait for BlueZ */
    if (wait_for_bluez(5000) < 0) {
        log_error("Timed out waiting for org.bluez");
        return 1;
    }

    /* Register pairing agent (NoInputNoOutput — auto-accept all) */
    if (register_agent() < 0) {
        log_info("Agent registration failed (another agent may be active)");
    }

    /* Register HFP-HF profile with BlueZ */
    if (register_hfp_profile() < 0) {
        log_error("Failed to register HFP-HF profile");
        return 1;
    }

    /* Force device class to Audio/Video Headset AFTER profile registration.
     * BlueZ overwrites class when profiles register, so we must set it after. */
    {
        pid_t pid = fork();
        if (pid == 0) {
            execlp("hciconfig", "hciconfig", "hci0", "class", "0x240404", NULL);
            _exit(1);
        } else if (pid > 0) {
            int status;
            waitpid(pid, &status, 0);
            if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
                log_info("Device class set to 0x240404 (Audio Headset)");
            }
        }
    }

    /* Configure ALSA mixer: set Audio In Source to PDMIN (PDM microphone).
     * Without this, the Amlogic audio frontend reads from an invalid source
     * and arecord captures only noise/silence instead of actual mic audio. */
    system("amixer -c 0 cset name='Audio In Source' 4 >/dev/null 2>&1");
    log_info("ALSA Audio In Source set to PDMIN");

    /* Set up SCO listener */
    setup_sco_listener();

    /* Set up control socket */
    if (setup_control_socket(ctrl_socket_path) < 0) {
        log_error("Failed to create control socket");
        return 1;
    }

    /* Start audio thread */
    if (pthread_create(&audio_thread_id, NULL, audio_thread_func, NULL) != 0) {
        log_error("Failed to start audio thread: %s", strerror(errno));
        return 1;
    }

    log_info("Spotifone Mic Bridge running");
    log_info("Control socket: %s", ctrl_socket_path);
    log_info("Profile: HFP Hands-Free (%s)", HFP_HF_UUID);

    /* Main event loop */
    int slc_initiate_pending = 0;  /* Set when we need to start SLC */

    while (running) {
        fd_set read_fds;
        struct timeval tv;
        int max_fd = -1;

        FD_ZERO(&read_fds);

        /* Control socket */
        if (ctrl_sock_fd >= 0) {
            FD_SET(ctrl_sock_fd, &read_fds);
            if (ctrl_sock_fd > max_fd) max_fd = ctrl_sock_fd;
        }

        /* RFCOMM fd (if connected) */
        if (rfcomm_fd >= 0) {
            FD_SET(rfcomm_fd, &read_fds);
            if (rfcomm_fd > max_fd) max_fd = rfcomm_fd;
        }

        /* SCO listener (for new SCO connections) */
        if (sco_listen_fd >= 0 && state >= STATE_RFCOMM_CONNECTED) {
            FD_SET(sco_listen_fd, &read_fds);
            if (sco_listen_fd > max_fd) max_fd = sco_listen_fd;
        }

        tv.tv_sec = 0;
        tv.tv_usec = 50000;  /* 50ms timeout — faster for SLC responsiveness */

        int ready = select(max_fd + 1, &read_fds, NULL, NULL, &tv);
        if (ready < 0) {
            if (errno == EINTR) continue;
            log_error("select failed: %s", strerror(errno));
            break;
        }

        /* Handle control commands */
        if (ctrl_sock_fd >= 0 && FD_ISSET(ctrl_sock_fd, &read_fds)) {
            handle_control_command();
        }

        /* Handle RFCOMM data */
        if (rfcomm_fd >= 0 && FD_ISSET(rfcomm_fd, &read_fds)) {
            handle_rfcomm_data();
        }

        /* Handle new SCO connections */
        if (sco_listen_fd >= 0 && FD_ISSET(sco_listen_fd, &read_fds)) {
            accept_sco_connection();
        }

        /* Process D-Bus events (may trigger NewConnection) */
        dbus_connection_read_write_dispatch(dbus_conn, 0);

        /* Check if we need to initiate HFP SLC handshake.
         * After NewConnection gives us rfcomm_fd, we (HF) must send
         * the first AT command (AT+BRSF) to start the SLC. */
        if (slc_initiate_pending && rfcomm_fd >= 0) {
            slc_initiate_pending = 0;
            usleep(50000);  /* Brief delay to let RFCOMM settle */
            initiate_slc();
        }

        /* Detect newly connected state from NewConnection callback */
        if (state == STATE_RFCOMM_CONNECTED && slc_state == SLC_NONE && !slc_initiate_pending) {
            slc_initiate_pending = 1;
        }
    }

    /* Cleanup */
    log_info("Shutting down...");

    streaming = 0;
    audio_thread_running = 0;
    if (audio_thread_id) {
        pthread_join(audio_thread_id, NULL);
    }

    if (rfcomm_fd >= 0) close(rfcomm_fd);
    if (sco_fd >= 0) close(sco_fd);
    if (sco_listen_fd >= 0) close(sco_listen_fd);
    if (ctrl_sock_fd >= 0) {
        close(ctrl_sock_fd);
        unlink(ctrl_socket_path);
    }
    if (dbus_conn) dbus_connection_unref(dbus_conn);
    if (sbc_initialized) sbc_finish(&sbc_encoder);

    log_info("Stopped");
    return 0;
}
