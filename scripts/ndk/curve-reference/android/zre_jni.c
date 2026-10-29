// One zyre node per process. CURVE is ALWAYS on here — keys are
// parameters, and there is no plain path (same contract as zc_peer).
// The node also announces its IP (always) and interface MAC (when the
// kernel allows) as X-IP / X-MAC headers, the same values the Python
// side prints in ENTER-headers during the phone trials.
//
// Threading contract (mirrors the verified zc_peer discipline):
// create/start/join/shout/whisper/uuid may be called from any thread;
// nextEvent() and stop() belong to the ONE pump thread the service
// owns. signalStop() is the only cross-thread teardown trigger — it
// pushes a stop to the node actor, which makes the blocked
// nextEvent() return "{}" so the pump thread can run stop() itself
// (never destroy a node another thread is blocked on).
#include <jni.h>
#include <pthread.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <net/if.h>
#ifndef ARPHRD_ETHER
#define ARPHRD_ETHER 1
#endif

#include <zyre.h>
#include <zmq.h>

static zyre_t *g_node = NULL;
static zactor_t *g_auth = NULL;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

static void jstring_to_c(JNIEnv *env, jstring js, char **out) {
    const char *utf = (*env)->GetStringUTFChars(env, js, NULL);
    *out = strdup(utf ? utf : "");
    if (utf) (*env)->ReleaseStringUTFChars(env, js, utf);
}

// Minimal JSON string escaping for content we embed in event JSON.
static void json_escape(const char *in, char *out, size_t outsz) {
    size_t o = 0;
    for (size_t i = 0; in[i] && o + 8 < outsz; i++) {
        unsigned char c = (unsigned char) in[i];
        if (c == '"' || c == '\\') {
            out[o++] = '\\'; out[o++] = (char) c;
        } else if (c >= 0x20 && c < 0x7f) {
            out[o++] = (char) c;
        } else {
            o += (size_t) snprintf(out + o, outsz - o, "\\u%04x", c);
        }
    }
    out[o] = '\0';
}

/* Best-effort interface facts for the announcement (see zc_peer.c:
 * the same logic, same evidence). ziflist picks the first broadcast
 * interface (the ioctl loader on Android); the MAC comes from
 * SIOCGIFHWADDR with a /sys/class/net fallback — both may be denied
 * for unprivileged uids on Android (verified denied for MAC on an
 * Android 13 retail build), so X-MAC is "when found" and X-IP
 * announces reliably. */
static bool s_local_iface(const char *pinned, char *addr, char *mac) {
    addr[0] = mac[0] = '\0';
    bool found = false;
    ziflist_t *ifl = ziflist_new();
    if (!ifl) return false;
    const char *iname = ziflist_first(ifl);
    while (iname) {
        if (!pinned || !strcmp(iname, pinned)) { found = true; break; }
        iname = ziflist_next(ifl);
    }
    char name[64] = "";
    if (found) {
        snprintf(name, sizeof name, "%s", iname);
        snprintf(addr, 64, "%s", ziflist_address(ifl));
    }
    ziflist_destroy(&ifl);
    if (!found || !addr[0]) return false;
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s >= 0) {
        struct ifreq ifr; memset(&ifr, 0, sizeof ifr);
        snprintf(ifr.ifr_name, sizeof ifr.ifr_name, "%s", name);
        if (ioctl(s, SIOCGIFHWADDR, &ifr) == 0
        &&  ifr.ifr_hwaddr.sa_family == ARPHRD_ETHER) {
            const unsigned char *hw = (const unsigned char *) ifr.ifr_hwaddr.sa_data;
            if (hw[0] | hw[1] | hw[2] | hw[3] | hw[4] | hw[5])
                snprintf(mac, 32, "%02x:%02x:%02x:%02x:%02x:%02x",
                         hw[0], hw[1], hw[2], hw[3], hw[4], hw[5]);
        }
        close(s);
    }
    if (!mac[0]) {
        char path[96]; snprintf(path, sizeof path,
                                "/sys/class/net/%s/address", name);
        FILE *f = fopen(path, "r");
        if (f) {
            char line[64];
            if (fgets(line, sizeof line, f)) {
                char *nl = strchr(line, '\n');
                if (nl) *nl = '\0';
                size_t n = strlen(line);
                /* bounded, printf-free copy (a "%s" snprintf here trips
                 * -Werror=format-truncation on host gcc; see zc_peer) */
                if (strchr(line, ':') && n > 0 && n < 32
                &&  strcmp(line, "00:00:00:00:00:00"))
                    memcpy(mac, line, n + 1);
            }
            fclose(f);
        }
    }
    return true;
}

static void destroy_locked(void) {
    // Pump-thread-only. Node first (sends the departure beacon), then
    // the ZAP authenticator — the teardown order verified in the
    // trials and docs/LIBZYRE_INTEROP.md section 7.
    if (g_node) { zyre_stop(g_node); zyre_destroy(&g_node); g_node = NULL; }
    if (g_auth) { zactor_destroy(&g_auth); g_auth = NULL; }
}

// create(name, pubZ85, secZ85, ifaceOrNull)
// -> node handle (>0), -1 bad keys, -2 already built, -3 resource failure.
// The whole body runs under g_lock: two overlapping create calls could
// otherwise BOTH pass the already-built check and the loser's node
// would never be stored or destroyed (an orphaned leak).
JNIEXPORT jlong JNICALL
Java_com_visioncamhealth_zyre_ZreNative_create(JNIEnv *env, jobject self,
        jstring name, jstring pubZ85, jstring secZ85, jstring iface) {
    pthread_mutex_lock(&g_lock);
    if (g_node) { pthread_mutex_unlock(&g_lock); return -2; }  // already built

    char *cname, *pub, *sec, *ifp = NULL;
    jstring_to_c(env, name, &cname); jstring_to_c(env, pubZ85, &pub);
    jstring_to_c(env, secZ85, &sec);
    if (iface) jstring_to_c(env, iface, &ifp);

    // CURVE always-on: server-side allowance at this harness level is
    // CURVE_ALLOW_ANY; production ZAP allowance (per-peer grants)
    // swaps "CURVE" CURVE_ALLOW_ANY for a directory zauth — see avedge.
    zactor_t *auth = zactor_new(zauth, NULL);
    if (!auth) {
        free(cname); free(pub); free(sec); free(ifp);
        pthread_mutex_unlock(&g_lock);
        return -3;                      // out of resources; nothing leaked
    }
    zstr_sendx(auth, "CURVE", CURVE_ALLOW_ANY, NULL);
    zsock_wait(auth);   // zc_peer parity: handshake with the auth actor

    zyre_t *node = zyre_new(cname);
    free(cname);
    if (!node) {
        free(pub); free(sec); free(ifp);
        zactor_destroy(&auth);
        pthread_mutex_unlock(&g_lock);
        return -3;                      // out of resources; nothing leaked
    }
    zyre_set_interval(node, 1000);    // 1 s cadence; libzyre's 0 makes peers EVASIVE

    byte raw_pub[32], raw_sec[32];
    if (!zmq_z85_decode(raw_pub, pub) || !zmq_z85_decode(raw_sec, sec)) {
        free(pub); free(sec); free(ifp);
        zyre_destroy(&node); zactor_destroy(&auth);   // node first, then ZAP actor
        pthread_mutex_unlock(&g_lock);
        return -1;                    // never fall back to plain
    }
    zcert_t *cert = zcert_new_from(raw_pub, raw_sec);
    zyre_set_zcert(node, cert);
    zyre_set_zap_domain(node, "global");
    zcert_destroy(&cert);

    // Announcement: X-IP always (from the first broadcast interface or
    // the pinned interface), X-MAC when the kernel allows it. Same
    // code, same evidence as zc_peer.c: s_local_iface.
    char announce_ip[64] = "", announce_mac[32] = "";
    if (s_local_iface(ifp, announce_ip, announce_mac) && announce_ip[0]) {
        zyre_set_header(node, "X-IP", "%s", announce_ip);
        if (announce_mac[0])
            zyre_set_header(node, "X-MAC", "%s", announce_mac);
    }

    // Endpoint truth: without a pinned interface, libzyre may bind its
    // inbox to an unroutable address and publish that (on the emulator
    // this is 127.0.0.1:49152 and the whole peer session decays
    // EVASIVE). Pin the SAME interface we announce in X-IP so the
    // published endpoint matches the reality on the wire. This is the
    // address form the pinned header documents for set_interface.
    if (ifp) zyre_set_interface(node, ifp);
    else if (announce_ip[0]) zyre_set_interface(node, announce_ip);
    free(pub); free(sec); free(ifp);
    g_node = node;
    g_auth = auth;
    pthread_mutex_unlock(&g_lock);
    return (jlong) (intptr_t) node;
}

// Gossip-mode inbox port: fixed so the published endpoint is exact
// (the zc_peer --endpoint parity behavior, phone-proven). Beacon mode
// never binds here — the beacon ping announces ip+port by itself.
#define GOSSIP_BIND_PORT 19870

// Optional UDP-free rendezvous: call BEFORE start() (emulator NAT, AP
// isolation). Beacons stay the default when this is never called.
//
// Publication truth: with gossip set, zyre would bind the inbox on the
// right interface but PUBLISH "tcp://<hostname>:<port>" — on the
// emulator the hostname maps to 127.0.0.1 (zyre_node.c gossip branch),
// so peers get an undialable endpoint and the session decays EVASIVE.
// Binding explicitly with zyre_set_endpoint publishes exactly what we
// intend (this is the SET ENDPOINT path, used by zc_peer --endpoint).
JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_gossipConnect(JNIEnv *env, jobject self,
        jstring endpoint) {
    (void) env; (void) self;
    char *ep; jstring_to_c(env, endpoint, &ep);
    pthread_mutex_lock(&g_lock);
    int rc = g_node ? 0 : -1;
    if (g_node) {
        zyre_gossip_connect(g_node, "%s", ep);
        char addr[64] = "", mac[32] = "";
        if (s_local_iface(NULL, addr, mac) && addr[0]) {
            char bound[96], advertised[96];
            snprintf(bound, sizeof bound, "tcp://0.0.0.0:%d", GOSSIP_BIND_PORT);
            snprintf(advertised, sizeof advertised, "tcp://%s:%d", addr, GOSSIP_BIND_PORT);
            // Bind ALL interfaces and PUBLISH the interface IP: the pair
            // zyre documents for NAT (set_advertised_endpoint). This is
            // what lets a hub-side peer dial an assisted path (adb
            // reverse) while peers on the real interface still see the
            // routable address.
            zyre_set_advertised_endpoint(g_node, advertised);
            if (zyre_set_endpoint(g_node, "%s", bound) != 0) rc = -1;
        } else {
            rc = -1;    // no routable interface: refuse to publish garbage
        }
    }
    pthread_mutex_unlock(&g_lock);
    free(ep); return rc;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_start(JNIEnv *env, jobject self) {
    pthread_mutex_lock(&g_lock);
    int rc = (g_node && zyre_start(g_node) == 0) ? 0 : -1;
    pthread_mutex_unlock(&g_lock);
    return rc;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_join(JNIEnv *env, jobject self,
        jstring group) {
    char *g; jstring_to_c(env, group, &g);
    pthread_mutex_lock(&g_lock);
    int rc = g_node ? zyre_join(g_node, g) : -1;  // effects flow via events
    pthread_mutex_unlock(&g_lock);
    free(g); return rc;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_shout(JNIEnv *env, jobject self,
        jstring group, jstring text) {
    char *g, *t; jstring_to_c(env, group, &g); jstring_to_c(env, text, &t);
    pthread_mutex_lock(&g_lock);
    int rc = g_node ? zyre_shouts(g_node, g, "%s", t) : -1;
    pthread_mutex_unlock(&g_lock);
    free(g); free(t); return rc;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_whisper(JNIEnv *env, jobject self,
        jstring peer, jstring text) {
    // zyre_whispers takes the peer UUID as a string on this pinned
    // zyre (same call the verified zc_peer whisper-on-enter uses);
    // zyreWhisper (zuuid_t form) is not part of this header surface.
    char *p, *t; jstring_to_c(env, peer, &p); jstring_to_c(env, text, &t);
    pthread_mutex_lock(&g_lock);
    int rc = g_node ? zyre_whispers(g_node, p, "%s", t) : -1;
    pthread_mutex_unlock(&g_lock);
    free(p); free(t); return rc;
}

// Blocking; returns ONE event as a JSON object string, or "{}" when
// the node is gone/stopping. ONLY call from the pump thread.
JNIEXPORT jstring JNICALL
Java_com_visioncamhealth_zyre_ZreNative_nextEvent(JNIEnv *env, jobject self) {
    pthread_mutex_lock(&g_lock);
    zyre_t *node = g_node;
    pthread_mutex_unlock(&g_lock);
    if (!node) return (*env)->NewStringUTF(env, "{}");
    zyre_event_t *event = zyre_event_new(node);   // blocks
    if (!event) return (*env)->NewStringUTF(env, "{}");
    const char *type = zyre_event_type(event);
    const char *peer = zyre_event_peer_uuid(event); // UPPERCASE uuid
    const char *name = zyre_event_peer_name(event);
    const char *group = zyre_event_group(event);
    char content[512] = "", escaped[1024] = "";
    zmsg_t *msg = zyre_event_msg(event);
    if (msg && zmsg_size(msg) > 0) {
        zframe_t *first = zmsg_first(msg);
        const byte *data = zframe_data(first);
        size_t size = zframe_size(first);
        if (data && size > 0 && size < sizeof content) {
            size_t i = 0;
            while (i < size && data[i] >= 0x20 && data[i] < 0x7f) i++;
            if (i == size) {                       // printable ASCII only
                memcpy(content, data, size);
                content[size] = '\0';
            }
        }
    }
    if (content[0]) json_escape(content, escaped, sizeof escaped);
    char buf[1400];
    snprintf(buf, sizeof buf,
        "{\"type\":\"%s\",\"peer_id\":\"%s\",\"peer_name\":\"%s\","
        "\"group\":\"%s\",\"content\":\"%s\"}",
        type ? type : "", peer ? peer : "", name ? name : "",
        group ? group : "", escaped);
    zyre_event_destroy(&event);
    return (*env)->NewStringUTF(env, buf);
}

// Cross-thread teardown TRIGGER only: pushes stop to the node actor,
// which unblocks the pump thread's nextEvent() with "{}". The pump
// thread then calls stop() itself.
JNIEXPORT void JNICALL
Java_com_visioncamhealth_zyre_ZreNative_signalStop(JNIEnv *env, jobject self) {
    pthread_mutex_lock(&g_lock);
    if (g_node) zyre_stop(g_node);
    pthread_mutex_unlock(&g_lock);
}

// Pump-thread-only: full teardown (departure beacon, then destroy,
// then the ZAP actor). Idempotent.
JNIEXPORT void JNICALL
Java_com_visioncamhealth_zyre_ZreNative_stop(JNIEnv *env, jobject self) {
    pthread_mutex_lock(&g_lock);
    destroy_locked();
    pthread_mutex_unlock(&g_lock);
}

JNIEXPORT jstring JNICALL
Java_com_visioncamhealth_zyre_ZreNative_uuid(JNIEnv *env, jobject self) {
    pthread_mutex_lock(&g_lock);
    const char *u = g_node ? zyre_uuid(g_node) : "";
    char copy[64] = "";
    snprintf(copy, sizeof copy, "%s", u ? u : "");
    pthread_mutex_unlock(&g_lock);
    return (*env)->NewStringUTF(env, copy);
}
