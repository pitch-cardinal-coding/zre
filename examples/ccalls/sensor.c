/*  =========================================================================
    sensor - C sensor device for the ccalls demo (libzyre side).

    Joins CHAT, shouts a temperature reading every --every seconds, prints
    every SHOUT/WHISPER it receives (commands from the Python app), and
    answers each ENTER with a WHISPER greeting.

    Build (libzyre installed, see HOW-TO-RUN.md):
        export PKG_CONFIG_PATH=$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH
        export LD_LIBRARY_PATH=$HOME/.local/lib:$LD_LIBRARY_PATH
        gcc -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE \
          -Wall -Wextra -Wformat=2 -Wformat-security -Werror \
          -o sensor sensor.c $(pkg-config --cflags --libs libzyre) -lczmq \
          -pie -Wl,-z,relro,-z,now

    Run two of them plus the Python app (examples/ccalls/app.py):
        ./sensor mobile-1 --port 5670
        ./sensor mobile-2 --port 5670
    =========================================================================
*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <zyre.h>

static double
now_ms (void)
{
    struct timespec ts;
    clock_gettime (CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1000000.0;
}

/* Print the first frame of an event message without touching the event. */
static void
print_content (const char *prefix, zmsg_t *msg)
{
    zframe_t *frame = msg ? zmsg_first (msg) : NULL;
    if (frame)
        printf ("%s%.*s\n", prefix, (int) zframe_size (frame),
                (const char *) zframe_data (frame));
    else
        printf ("%s(empty)\n", prefix);
    fflush (stdout);
}

int
main (int argc, char *argv [])
{
    setvbuf (stdout, NULL, _IONBF, 0);
    const char *name = "mobile-1";
    int beacon_port = 5670;
    int peer_port = 0;              /* fixed TCP inbox for WAN dialling */
    const char *group = "CHAT";
    double every = 3.0;
    double duration = 60.0;

    int argn;
    for (argn = 1; argn < argc; argn++) {
        if (strcmp (argv [argn], "--port") == 0 && argn + 1 < argc)
            beacon_port = atoi (argv [++argn]);
        else if (strcmp (argv [argn], "--peer-port") == 0 && argn + 1 < argc)
            peer_port = atoi (argv [++argn]);
        else if (strcmp (argv [argn], "--group") == 0 && argn + 1 < argc)
            group = argv [++argn];
        else if (strcmp (argv [argn], "--every") == 0 && argn + 1 < argc)
            every = atof (argv [++argn]);
        else if (strcmp (argv [argn], "--duration") == 0 && argn + 1 < argc)
            duration = atof (argv [++argn]);
        else if (argv [argn][0] != '-')
            name = argv [argn];
        else {
            fprintf (stderr,
                     "usage: %s NAME [--port N] [--peer-port N] [--group G] "
                     "[--every S] [--duration S]\n", argv [0]);
            return 2;
        }
    }

    zyre_t *node = zyre_new (name);
    zyre_set_port (node, beacon_port);
    if (peer_port > 0)
        zyre_set_beacon_peer_port (node, peer_port);
    zyre_set_interval (node, 1000);
    if (zyre_start (node)) {
        fprintf (stderr, "cannot start node\n");
        return 1;
    }
    zyre_join (node, group);
    printf ("[%s] up, shouting readings to %s every %.0fs\n", name, group, every);
    fflush (stdout);

    srand ((unsigned) (time (NULL) ^ getpid ()));
    double started = now_ms ();
    double last_pub = started;
    unsigned seq = 0;

    while (true) {
        /* zyre_event_new blocks until the next event (ENTER/JOIN/SHOUT/
         * PING-driven EVASIVE...). Timers below are re-checked after every
         * event, so publications never stall while peers are around; with
         * zero peers the run is bounded by --duration / timeout(1). */
        zyre_event_t *event = zyre_event_new (node);
        if (!event)
            break;                  /* interrupted */

        const char *type = zyre_event_type (event);
        if (strcmp (type, "ENTER") == 0) {
            printf ("[%s] peer %s entered\n", name, zyre_event_peer_name (event));
            zyre_whispers (node, zyre_event_peer_uuid (event), "hi-from-C");
        }
        else if (strcmp (type, "SHOUT") == 0) {
            printf ("[%s] command from %s: ", name, zyre_event_peer_name (event));
            print_content ("", zyre_event_msg (event));
        }
        else if (strcmp (type, "WHISPER") == 0) {
            printf ("[%s] private from %s: ", name, zyre_event_peer_name (event));
            print_content ("", zyre_event_msg (event));
        }
        else if (strcmp (type, "EXIT") == 0)
            printf ("[%s] peer %s left\n", name, zyre_event_peer_name (event));
        fflush (stdout);
        zyre_event_destroy (&event);

        double now = now_ms ();
        if ((now - last_pub) / 1000.0 >= every) {
            char reading [64];
            snprintf (reading, sizeof reading, "reading-%.1f #%u",
                      20.0 + (rand () % 50) / 10.0, ++seq);
            zyre_shouts (node, group, "%s", reading);
            printf ("[%s] published %s\n", name, reading);
            fflush (stdout);
            last_pub = now;
        }
        if ((now - started) / 1000.0 >= duration)
            break;
    }
    zyre_stop (node);
    zyre_destroy (&node);
    return 0;
}
