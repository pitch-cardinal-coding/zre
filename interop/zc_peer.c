/*  =========================================================================
    zc_peer - libzyre test peer used for zre (Python) interoperability

    A small, scriptable libzyre node. It prints one machine readable line
    per event so the Python side (or a shell driver) can assert on it.

    Build (inside the VM that has libzyre installed):
        export PKG_CONFIG_PATH=$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH
        export LD_LIBRARY_PATH=$HOME/.local/lib:$LD_LIBRARY_PATH
        gcc -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE \
          -Wall -Wextra -Wformat=2 -Wformat-security -Werror \
          -o zc_peer zc_peer.c $(pkg-config --cflags --libs libzyre) -lczmq -lzmq \
          -pie -Wl,-z,relro,-z,now

    Usage:
      zc_peer --name NAME [options]

        --beacon-port N     UDP discovery port (default 5670)
        --beacon-peer-port N fixed TCP inbox port for WAN dialling (default ephemeral)
        --interface IF       pin NIC for beacons / endpoint
        --endpoint EP        gossip mode: bind this endpoint, disable beacon
        --gossip-connect EP  gossip mode: connect to a hub
        --gossip-bind EP     gossip mode: host a hub
        --join GROUP         join a group (repeatable)
        --leave-after S      leave all groups S seconds after start
        --contest GROUP      enable leader election in GROUP (repeatable)
        --shout "GRP:TEXT"   shout TEXT to GRP every --shout-every seconds
        --shout-every S      shout interval (default 2)
        --shout-delay S      first shout after S seconds (default 1)
        --whisper-on-enter T whisper T to every peer that enters
        --curve-pub Z85      Curve public key (z85; Curve is ALWAYS on)
        --curve-sec Z85      Curve secret key
                             with no keys: an ephemeral zcert is generated
        --zap-domain D       ZAP domain (default global)
        --duration S         run for S seconds then exit (default 10)
        --verbose            verbose wire log on stderr
        --announce NAME=VAL  set a header advertised in HELLO
        --no-join-group      used internally; ignore
    =========================================================================
*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <sys/ioctl.h>
#include <sys/socket.h>
#include <net/if.h>
#ifndef ARPHRD_ETHER
#define ARPHRD_ETHER 1
#endif

#include <zyre.h>
#include <zmq.h>

#define MAX_LIST 32

static char *g_shouts[MAX_LIST];
static int   g_shout_count = 0;
static double g_shout_every = 2.0;
static double g_shout_delay = 1.0;
static const char *g_whisper_on_enter = NULL;
static const char *g_leave_after_group = NULL;

/* Best-effort local interface discovery for the X-IP / X-MAC headers.
 * ziflist (ioctl loader on Android, getifaddrs elsewhere) yields the
 * first broadcast interface — or the pinned one — with its address; the
 * MAC comes from SIOCGIFHWADDR with a /sys/class/net fallback. Both are
 * allowed under unprivileged uids on Android 11+, unlike the netlink
 * path. Zeroed/absent MACs are reported as empty (header omitted). */
static bool
s_local_iface (const char *pinned, char *name, char *addr, char *mac)
{
    name [0] = '\0';
    addr [0] = '\0';
    mac [0] = '\0';
    ziflist_t *ifl = ziflist_new ();
    if (!ifl)
        return false;
    bool found = false;
    const char *iname = ziflist_first (ifl);
    while (iname) {
        if (pinned == NULL || streq (iname, pinned)) {
            found = true;
            break;
        }
        iname = ziflist_next (ifl);
    }
    if (found) {
        snprintf (name, 64, "%s", iname);
        snprintf (addr, 64, "%s", ziflist_address (ifl));
    }
    ziflist_destroy (&ifl);
    if (!found)
        return false;

    int sock = socket (AF_INET, SOCK_DGRAM, 0);
    if (sock >= 0) {
        struct ifreq ifr;
        memset (&ifr, 0, sizeof (ifr));
        snprintf (ifr.ifr_name, sizeof (ifr.ifr_name), "%s", name);
        if (ioctl (sock, SIOCGIFHWADDR, &ifr) == 0
        &&  ifr.ifr_hwaddr.sa_family == ARPHRD_ETHER) {
            const byte *hw = (const byte *) ifr.ifr_hwaddr.sa_data;
            if (hw [0] | hw [1] | hw [2] | hw [3] | hw [4] | hw [5])
                snprintf (mac, 32, "%02x:%02x:%02x:%02x:%02x:%02x",
                          hw [0], hw [1], hw [2], hw [3], hw [4], hw [5]);
        }
        close (sock);
    }
    if (!mac [0]) {
        char path [96];
        snprintf (path, sizeof (path), "/sys/class/net/%s/address", name);
        FILE *stream = fopen (path, "r");
        if (stream) {
            char line [64];
            if (fgets (line, sizeof (line), stream)) {
                char *nl = strchr (line, '\n');
                if (nl)
                    *nl = '\0';
                size_t n = strlen (line);
                /* manual bounded copy: a printf-family copy would trip
                 * -Wformat-truncation on hosts that cannot prove the
                 * buffer (the file line is trusted but sized, keep the
                 * copy provably bounded) */
                if (strchr (line, ':') && n > 0 && n < sizeof (mac)
                &&  !streq (line, "00:00:00:00:00:00")) {
                    memcpy (mac, line, n + 1);
                }
            }
            fclose (stream);
        }
    }
    return true;
}

/* Copy the frames of an event message into a NUL terminated string,
 * separated by '|'. Frames are binary: copy with memcpy, never read them
 * as NUL terminated strings (strncat et al. over-read past a frame that
 * holds no NUL, or stop early at an embedded one). */
static char *
first_frame (zmsg_t *msg)
{
    static char buffer [4096];
    if (!msg)
        return (char *) "";
    size_t used = 0;
    buffer [0] = 0;
    zframe_t *frame = zmsg_first (msg);
    bool first = true;
    while (frame) {
        size_t size = zframe_size (frame);
        size_t need = size + (first ? 1 : 2);   /* '|' + NUL */
        if (used + need > sizeof (buffer))
            break;
        if (!first)
            buffer [used++] = '|';
        memcpy (buffer + used, zframe_data (frame), size);
        used += size;
        buffer [used] = 0;
        first = false;
        frame = zmsg_next (msg);
    }
    return buffer;
}

static double
now_ms (void)
{
    struct timespec ts;
    clock_gettime (CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1000000.0;
}

static void
add_list (char **list, int *count, const char *value)
{
    if (*count < MAX_LIST)
        list[(*count)++] = strdup (value);
}

int
main (int argc, char **argv)
{
    /* Unbuffered stdout: when killed by timeout(1) with no events,
     * block-buffered EVENT lines would otherwise be lost. */
    setvbuf (stdout, NULL, _IONBF, 0);
    const char *name = "cpeer";
    const char *iface = NULL;
    const char *endpoint = NULL;
    const char *gossip_bind = NULL;
    const char *gossip_connect = NULL;
    const char *zap_domain = "global";
    char *headers[16][2];
    int header_count = 0;
    char *groups[MAX_LIST];
    int group_count = 0;
    char *contests[MAX_LIST];
    int contest_count = 0;
    int beacon_port = ZRE_DISCOVERY_PORT;
    int beacon_peer_port = 0;
    const char *curve_pub = NULL;
    const char *curve_sec = NULL;
    int verbose = 0;
    double duration = 10.0;
    double leave_after = -1.0;

    int argn;
    for (argn = 1; argn < argc; argn++) {
        if (streq (argv [argn], "--name"))
            name = argv [++argn];
        else if (streq (argv [argn], "--beacon-port"))
            beacon_port = atoi (argv [++argn]);
        else if (streq (argv [argn], "--beacon-peer-port"))
            beacon_peer_port = atoi (argv [++argn]);
        else if (streq (argv [argn], "--interface"))
            iface = argv [++argn];
        else if (streq (argv [argn], "--endpoint"))
            endpoint = argv [++argn];
        else if (streq (argv [argn], "--gossip-bind"))
            gossip_bind = argv [++argn];
        else if (streq (argv [argn], "--gossip-connect"))
            gossip_connect = argv [++argn];
        else if (streq (argv [argn], "--join"))
            add_list (groups, &group_count, argv [++argn]);
        else if (streq (argv [argn], "--contest"))
            add_list (contests, &contest_count, argv [++argn]);
        else if (streq (argv [argn], "--shout")) {
            add_list (g_shouts, &g_shout_count, argv [++argn]);
        }
        else if (streq (argv [argn], "--shout-every"))
            g_shout_every = atof (argv [++argn]);
        else if (streq (argv [argn], "--shout-delay"))
            g_shout_delay = atof (argv [++argn]);
        else if (streq (argv [argn], "--whisper-on-enter"))
            g_whisper_on_enter = argv [++argn];
        else if (streq (argv [argn], "--leave-after"))
            leave_after = atof (argv [++argn]);
        else if (streq (argv [argn], "--curve-pub"))
            curve_pub = argv [++argn];
        else if (streq (argv [argn], "--curve-sec")) {
            curve_sec = argv [++argn];
        }
        else if (streq (argv [argn], "--zap-domain"))
            zap_domain = argv [++argn];
        else if (streq (argv [argn], "--duration"))
            duration = atof (argv [++argn]);
        else if (streq (argv [argn], "--verbose"))
            verbose = 1;
        else if (streq (argv [argn], "--announce")) {
            char *eq = strchr (argv [++argn], '=');
            if (eq && header_count < 16) {
                headers [header_count][0] = strndup (argv [argn], eq - argv [argn]);
                headers [header_count][1] = strdup (eq + 1);
                header_count++;
            }
        }
        else {
            fprintf (stderr, "unknown option: %s\n", argv [argn]);
            return 2;
        }
    }
    (void) g_leave_after_group;

    /* One keypair handling: Curve is ALWAYS on (project decision — no
     * plain option). With explicit keys use them (verify BOTH, not one);
     * otherwise generate an ephemeral cert. Partial keys are refused
     * rather than silently ignored. */
    if ((curve_pub != NULL) != (curve_sec != NULL)) {
        fprintf (stderr, "EVENT ERROR invalid-curve-key\n");
        return 2;
    }
    zactor_t *auth = zactor_new (zauth, NULL);
    assert (auth);
    if (verbose)
        zstr_send (auth, "VERBOSE");
    zsock_wait (auth);
    zstr_sendx (auth, "CURVE", CURVE_ALLOW_ANY, NULL);
    zsock_wait (auth);

    zyre_t *node = zyre_new (name);
    assert (node);
    if (verbose)
        zyre_set_verbose (node);
    if (iface)
        zyre_set_interface (node, iface);
    zyre_set_port (node, beacon_port);
    if (beacon_peer_port > 0)
        zyre_set_beacon_peer_port (node, beacon_peer_port);
    // Match zre's 1 s beacon cadence explicitly: libzyre's default interval
    // is 0 ("instant"), which zbeacon treats as send-once, so without this
    // peers go EVASIVE after 5 s even though both sides are alive.
    zyre_set_interval (node, 1000);
    for (argn = 0; argn < header_count; argn++)
        zyre_set_header (node, headers [argn][0], "%s", headers [argn][1]);
    /* X-IP / X-MAC announcement (best effort): same values the Android app
     * sets; explicit --announce headers take precedence. Anything the
     * kernel denies (MAC under an unprivileged uid, some devices) is
     * simply not announced — "when found". */
    {
        char ifname [64], ifaddr [64], ifmac [32];
        if (s_local_iface (iface, ifname, ifaddr, ifmac) && ifaddr [0]) {
            zyre_set_header (node, "X-IP", "%s", ifaddr);
            if (ifmac [0])
                zyre_set_header (node, "X-MAC", "%s", ifmac);
            printf ("EVENT LOCAL-IFACE name=%s ip=%s mac=%s\n",
                    ifname, ifaddr, ifmac [0]? ifmac: "-");
            fflush (stdout);
        }
    }
    {
        zcert_t *cert = NULL;
        if (curve_pub && curve_sec) {
            /* zcert_new_from takes raw 32-byte keys, not Z85 text:
             * decode first (zyre_peer.c does the same via zarmour). */
            byte raw_pub [32];
            byte raw_sec [32];
            if (!zmq_z85_decode (raw_pub, curve_pub)
            ||  !zmq_z85_decode (raw_sec, curve_sec)) {
                fprintf (stderr, "EVENT ERROR invalid-curve-key\n");
                fflush (stdout);
                zyre_destroy (&node);
                zactor_destroy (&auth);
                return 1;
            }
            cert = zcert_new_from (raw_pub, raw_sec);
        }
        else
            cert = zcert_new ();
        assert (cert);
        zyre_set_zcert (node, cert);
        zyre_set_zap_domain (node, zap_domain);
        printf ("EVENT CURVE-PUBKEY %s\n", zcert_public_txt (cert));
        fflush (stdout);
        zcert_destroy (&cert);
    }
    if (endpoint) {
        if (zyre_set_endpoint (node, "%s", endpoint) != 0) {
            fprintf (stderr, "EVENT ERROR cannot-bind-endpoint\n");
            fflush (stdout);
            zyre_destroy (&node);
            zactor_destroy (&auth);
            return 1;
        }
    }
    if (gossip_bind)
        zyre_gossip_bind (node, "%s", gossip_bind);
    if (gossip_connect)
        zyre_gossip_connect (node, "%s", gossip_connect);

    if (zyre_start (node)) {
        fprintf (stderr, "EVENT ERROR cannot-start\n");
        fflush (stdout);
        zyre_destroy (&node);
        zactor_destroy (&auth);
        return 1;
    }
    for (argn = 0; argn < contest_count; argn++)
        zyre_set_contest_in_group (node, contests [argn]);
    for (argn = 0; argn < group_count; argn++)
        zyre_join (node, groups [argn]);

    printf ("EVENT READY uuid=%s name=%s\n", zyre_uuid (node), zyre_name (node));
    fflush (stdout);

    double started = now_ms ();
    double last_shout = started + g_shout_delay * 1000.0;
    int left = 0;

    while (true) {
        zyre_event_t *event = zyre_event_new (node);
        if (!event)
            break;

        const char *type = zyre_event_type (event);
        if (streq (type, "ENTER")) {
            const char *impl = zyre_event_header (event, "X-IMPL");
            const char *role = zyre_event_header (event, "X-ROLE");
            printf ("EVENT ENTER peer=%s name=%s address=%s",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event),
                    zyre_event_peer_addr (event));
            if (impl)
                printf (" X-IMPL=%s", impl);
            if (role)
                printf (" X-ROLE=%s", role);
            printf ("\n");
        }
        else
        if (streq (type, "EXIT"))
            printf ("EVENT EXIT peer=%s name=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event));
        else
        if (streq (type, "JOIN"))
            printf ("EVENT JOIN peer=%s name=%s group=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event),
                    zyre_event_group (event));
        else
        if (streq (type, "LEAVE"))
            printf ("EVENT LEAVE peer=%s name=%s group=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event),
                    zyre_event_group (event));
        else
        if (streq (type, "SHOUT"))
            printf ("EVENT SHOUT peer=%s name=%s group=%s content=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event),
                    zyre_event_group (event), first_frame (zyre_event_msg (event)));
        else
        if (streq (type, "WHISPER"))
            printf ("EVENT WHISPER peer=%s name=%s content=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event),
                    first_frame (zyre_event_msg (event)));
        else
        if (streq (type, "LEADER"))
            printf ("EVENT LEADER peer=%s name=%s group=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event),
                    zyre_event_group (event));
        else
        if (streq (type, "EVASIVE"))
            printf ("EVENT EVASIVE peer=%s name=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event));
        else
        if (streq (type, "SILENT"))
            printf ("EVENT SILENT peer=%s name=%s\n",
                    zyre_event_peer_uuid (event), zyre_event_peer_name (event));
        else
            printf ("EVENT %s\n", type);
        fflush (stdout);

        if (streq (type, "ENTER") && g_whisper_on_enter)
            zyre_whispers (node, zyre_event_peer_uuid (event), "%s", g_whisper_on_enter);
        zyre_event_destroy (&event);

        double elapsed = now_ms ();
        if (!left && leave_after >= 0 && (elapsed - started) / 1000.0 >= leave_after) {
            for (argn = 0; argn < group_count; argn++)
                zyre_leave (node, groups [argn]);
            left = 1;
        }
        if (g_shout_count && (now_ms () - last_shout) >= g_shout_every * 1000.0) {
            for (argn = 0; argn < g_shout_count; argn++) {
                char *sep = strchr (g_shouts [argn], ':');
                if (sep) {
                    *sep = 0;
                    zyre_shouts (node, g_shouts [argn], "%s", sep + 1);
                    *sep = ':';
                }
            }
            last_shout = now_ms ();
        }
        if ((now_ms () - started) / 1000.0 >= duration)
            break;
    }
    printf ("EVENT DONE\n");
    fflush (stdout);
    // Polite departure: EXIT beacon (port 0) so peers emit EXIT instead of
    // timing the peer out via EVASIVE/SILENT/expire. zyre_destroy() alone
    // just kills the actor without any departure signal.
    zyre_stop (node);
    zyre_destroy (&node);
    zactor_destroy (&auth);
    for (argn = 0; argn < group_count; argn++)
        free (groups [argn]);
    for (argn = 0; argn < contest_count; argn++)
        free (contests [argn]);
    for (argn = 0; argn < g_shout_count; argn++)
        free (g_shouts [argn]);
    for (argn = 0; argn < header_count; argn++) {
        free (headers [argn][0]);
        free (headers [argn][1]);
    }
    return 0;
}