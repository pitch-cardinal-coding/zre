# Embedding libzyre (C) in the Android app, talking to Python `zre`

> **HISTORICAL (2026-10-23):** the mobile app now runs **plain ZRE** (no
> Curve, no ZAP, no keys, no settings sheet). Statements below about
> Curve-always-on, key handling, refusal to run plain, and the settings
> paste describe the Curve era and are kept as design history. Current
> state, evidence and the restoration checklist live in
> [`CURVE-MESH.md`](CURVE-MESH.md); the plain-mode leak verdict is in
> [`valgrind-harness/README.md`](valgrind-harness/README.md).

**Audience:** the mobile developer of `vision-cam-health` (or any LLM
working on it). **Status: the verified sections below were backed by a
run on the real phone (Xiaomi 23028RNCAG, armeabi-v7a, Wi-Fi
`10.0.89.13/22`) against the Python `zre` implementation on this PC
(`10.0.89.21`); clearly-marked sketches and future work are not claims.
Dates of verification: 2026-10-19. Nothing in here is theory.**

Companion files (this directory unless a path is given):

- `README.md` — the full cross-build story (all seven build fixes, patch
  evidence, make targets).
- `zre_static_all.sh` — the working cross-build script.
- `docs/LIBZYRE_INTEROP.md` (repo root) — desktop interop ground truth
  (Curve, WAN, elections, valgrind results) that the Python side mirrors.
- `interop/zc_peer.c` (repo root) — the verified C harness used in every
  trial.

---

## 1. What was verified on the phone

| Capability | Result | How |
|---|---|---|
| Static cross-build of libzmq/czmq/zyre for Android | ✅ all 3 ABIs | NDK r30, API 24, zyre `v2.0.1-96`, libsodium 1.0.20 |
| Static `zc_peer` executable | ✅ NEEDED = `libc.so libdl.so libm.so liblog.so` only | `llvm-readelf -d` on all three binaries |
| Push + run on a real 32-bit phone via adb | ✅ | `/data/local/tmp/zc_peer`, no `LD_LIBRARY_PATH` |
| **UDP beacon discovery phone ↔ PC over Wi-Fi** | ✅ `PHONE_PASS` | both bind UDP 5670; `ENTER`/`JOIN`/`SHOUT` both directions; whisper received by phone |
| **Gossip discovery phone ↔ PC** (no UDP at all) | ✅ `GOSSIP_PASS` | Python hosts a ZGS.1 hub; phone connects with `--gossip-connect` |
| **Direct TCP dial PC → phone** | ✅ `DIAL_PASS` | phone binds a fixed `--endpoint tcp://<phone>:19870` (no beacons); Python `connect_peer` dials it, full ENTER/JOIN/SHOUT flow |
| Whispers + shouts both directions | ✅ | phone log: `EVENT WHISPER ... content=hello-from-Python` |
| Curve/encrypted mode on the phone | ✅ `CURVE_PASS` | both sides Curve-enabled: v3 (54-byte) beacons matched, phone `zauth: ZAP request mechanism=CURVE` allowed the PC cert, whisper/shout flowed both ways — see `scripts/ndk/README.md` trial procedure |

Videos of nothing — this is a chat/telemetry protocol, all results are
from captured `EVENT` lines, quoted in section 8.

## 2. Deliverables and where they are

On the build host (`iam@10.0.89.26`; password auth reads
`BUILD_PASSWORD` from the git-ignored `scripts/ndk/lab.env` or the
environment — the Makefile ships no password):

```
~/zre-android-build/prefix/<abi>/lib/   libsodium.a libzmq.a libczmq.a libzyre.a
~/zre-android-build/prefix/<abi>/include/  zmq.h czmq.h zyre.h + all deps
~/zc-android/zc_peer-armeabi-v7a        1.3 MB static PIE (32-bit phones)
~/zc-android/zc_peer-arm64-v8a          1.9 MB static PIE
~/zc-android/zc_peer-x86_64             1.9 MB static PIE
```

ABI facts for this project:

- The real phone (`23028RNCAG`) is **32-bit only**: it runs the
  `armeabi-v7a` artifacts. arm64 libs will not load on it.
- Emulators used on the build host (API 37 AVDs) take `x86_64`.
- `libzyre.a` (`armeabi-v7a`) is what the app links against.
- Rebuild/reproduce: `scripts/ndk/README.md` → `make build`. The script
  is idempotent and BUILDS ALL SEVEN FIXES IN (section 4).

## 3. How ZRE works (30-second picture)

- Presence: every node broadcasts a 22-byte UDP beacon on **port 5670**
  (`ZRE-DISC`, IANA) once per second. Beacons carry a UUID + the TCP
  port of the peer's inbox.
- Connection: a node that hears a beacon dials `tcp://<sender-ip>:<port>`
  and both sides exchange a `HELLO` (name, groups, headers). That gives
  an `ENTER` event with peer name/id/headers.
- Messaging: `SHOUT` (per group), `WHISPER` (per peer id); groups via
  `JOIN`/`LEAVE`; liveness via beacon/PING cadence → `EVASIVE`/`SILENT`
  → `EXIT`.
- Optional gossip replaces UDP entirely: nodes connect to a *gossip hub*
  and publish `uuid → tcp endpoint` tuples (`ZGS.1` protocol). Verified
  working with the Python hub and libzyre as client, both under adb and
  on the desktop.
- Curve encryption: same protocol with zcert keys; the phone build has
  Curve enabled (libsodium) and matches desktop behavior. Python side
  mirrors the `X-PUBLICKEY` header scheme.

## 4. The app integration (`vision-cam-health`)

### 4.1 What the app has today (updated from the historical snapshot)

- Kotlin + Compose, `minSdk 24`, `targetSdk 37`; the native zre node is
  IN: `app/src/main/cpp/zre_jni.c` + `CMakeLists.txt` + Gradle
  `externalNativeBuild` (section 4.4). Everything below that reads like
  a pre-integration snapshot is historical — the task list in section 8
  tracks what actually happened.
- Permissions include `INTERNET`, `WAKE_LOCK`, and
  `CHANGE_WIFI_MULTICAST_STATE` (AndroidManifest.xml).
- Foreground service: `MetricsService` (`dataSync` type pattern) — the
  libzyre event pump lives in `ZreService` (`connectedDevice` type;
  section 4.7), which avoids the `dataSync` BOOT/6-hour limitations.

### 4.2 NDK version pin

The app pins `ndkVersion = "30.0.16248370"` (r30) — the verified build
(2026-10-20): the shipped `app-release.apk` was produced by it. If the
pin ever moves, rerun `scripts/ndk/zre_static_all.sh` with
`ANDROID_NDK_ROOT` pointed at the new NDK and budget a trial cycle for
its re-verification (those reruns are not yet exercised).

The libraries were configured with `ANDROID_PLATFORM=android-24`,
matching `minSdk 24` — do not raise `ANDROID_PLATFORM` above the app
`minSdk`.

### 4.3 Curve keys — `make curve-*` (HISTORICAL: the app is plain since 2026-10-23)

> **Current state:** the mobile app runs **plain ZRE** (no Curve/ZAP, no
> keys, no settings). The complete Curve design, its evidence, and a
> restoration checklist live in
> `scripts/ndk/CURVE-MESH.md`. Everything below this note
> describes the Curve era and remains accurate for `zc_peer` and the
> Python trial tooling, which still default to Curve.

Curve was **not optional** on the device while it was in use:
`zc_peer` runs Curve with no plain fallback by design, and the app did
too — a plain node cannot even discover a Curve node (54-byte v3
beacons vs 22-byte v1, downgrade protection). The key commands remain
valid for the PC/`zc_peer` side:

```sh
make curve-keys        # ensure <NAME>.priv/<NAME>.pub in ~/.avedge (avedge format)
make curve-check       # validate: 40-char Z85 + public derives from the private (x25519)
make curve-release     # stage dist/curve/<NAME>.{priv,pub} (operator handoff)
```

- Format (also what `make -C ~/devcode/avedge keys keys-check` manages):
  files `~/.avedge/<name>.priv` and `<name>.pub`, one bare 40-char Z85
  key line each, priv 0600. `scripts/ndk/curve_keys.py` delegates to the
  avedge toolchain when `~/devcode/avedge` exists, so the two projects
  produce byte-identical files; otherwise it writes the same format
  itself from `zmq.curve_keypair()`.
- Verification is the same standard as avedge's `zcurve check`: the
  public key must **derive** from the private key (x25519) — so a
  mismatched or truncated file can never silently break handshakes.
- `make push` runs `curve-check` first; the phone trials load this pair:
  the phone peer takes `<name>` from the avedge store
  (`phone1`), and the PC parity `--avedge` loads its own pair (`avedge`)
  from the very same files the operator's `make keys` manages.
- The staged `dist/curve/` directory is the OPERATOR handoff material.
  The app neither bundles keys nor generates them: the two Z85 strings
  are pasted into the app's Settings sheet ("Mesh discovery keys");
  `ZreService` stores the pair in app-private prefs and only reads what
  is stored (the never-running-plain rule stands: a missing or bad pair
  refuses to run, unchanged). Future dashboards will serve the same two
  strings; `make curve-release` prints this handoff today. Never commit
  the `.priv` to a repo; rotate by regenerating, granting the new
  `.pub`, and pasting the new pair — the settings save rebuilds the
  running node with the replacement identity.

### 4.4 Native glue (full listing)

Minimal JNI surface: the app talks to libzyre through native calls and
pumps events as JSON on a dedicated thread. Real package prefix
(`com.visioncamhealth`, applicationId verified in the repo):

**`app/src/main/cpp/zre_jni.c`** (complete):

```c
// One zyre node per process. CURVE is ALWAYS on here — keys are
// parameters, and there is no plain path (same contract as zc_peer).
// The node also announces its IP (always) and interface MAC (when the
// kernel allows) as X-IP / X-MAC headers, the same values the Python
// side prints in ENTER-headers during the phone trials.
#include <jni.h>
#include <pthread.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <net/if.h>
#ifndef ARPHRD_ETHER
#define ARPHRD_ETHER 1
#endif

#include <zyre.h>
#include <zmq.h>

#define ZRE_BUILD_DRAFT_API   // zyre_set_interval / set_beacon_peer_port
#define CZMQ_BUILD_DRAFT_API  // zactor/zauth/zcert API family
static zyre_t *g_node = NULL;
static zactor_t *g_auth = NULL;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

static void jstring_to_c(JNIEnv *env, jstring js, char **out) {
    const char *utf = (*env)->GetStringUTFChars(env, js, NULL);
    *out = strdup(utf ? utf : "");
    if (utf) (*env)->ReleaseStringUTFChars(env, js, utf);
}

/* Best-effort interface facts for the announcement (see zc_peer.c:
 * the same logic, same evidence). ziflist picks the first broadcast
 * interface (the ioctl loader on Android); the MAC comes from
 * SIOCGIFHWADDR with a /sys/class/net fallback — both are allowed for
 * unprivileged uids on Android 11+, and both were verified to be
 * DENIED for MAC on this Android 13 retail build, so X-MAC is "when
 * found" and X-IP announces reliably. */
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
                if (strchr(line, ':') && n > 0 && n < sizeof mac
                &&  strcmp(line, "00:00:00:00:00:00"))
                    { memcpy(mac, line, n + 1); }   // bounded, printf-free
            }
            fclose(f);
        }
    }
    return true;
}

// create(name, pubZ85, secZ85, ifaceOrNull) -> handle or -1 (bad keys)
JNIEXPORT jlong JNICALL
Java_com_visioncamhealth_zyre_ZreNative_create(JNIEnv *env, jobject self,
        jstring name, jstring pubZ85, jstring secZ85, jstring iface) {
    char *cname, *pub, *sec, *ifp = NULL;
    jstring_to_c(env, name, &cname); jstring_to_c(env, pubZ85, &pub);
    jstring_to_c(env, secZ85, &sec);
    if (iface) jstring_to_c(env, iface, &ifp);

    // CURVE always-on: server-side allowance this harness level is
    // CURVE_ALLOW_ANY; production ZAP allowance (per-peer grants) swaps
    // "CURVE" CURVE_ALLOW_ANY for a directory zauth — see avedge zauth.
    g_auth = zactor_new(zauth, NULL);
    zstr_sendx(g_auth, "CURVE", CURVE_ALLOW_ANY, NULL);
    zsock_wait(g_auth);   // zc_peer parity: handshake with the auth actor

    zyre_t *node = zyre_new(cname);
    free(cname);
    zyre_set_interval(node, 1000);    // 1 s cadence; libzyre's 0 makes peers EVASIVE

    byte raw_pub[32], raw_sec[32];
    if (!zmq_z85_decode(raw_pub, pub) || !zmq_z85_decode(raw_sec, sec)) {
        free(pub); free(sec); free(ifp);
        zactor_destroy(&g_auth); zyre_destroy(&node);
        return -1;                    // never fall back to plain
    }
    zcert_t *cert = zcert_new_from(raw_pub, raw_sec);
    zyre_set_zcert(node, cert);
    zyre_set_zap_domain(node, "global");
    zcert_destroy(&cert);

    // Announcement: X-IP always (from the first broadcast interface or
    // the pinned --iface), X-MAC when the kernel allows it. Same code,
    // same evidence as zc_peer.c: s_local_iface (ioctl + /sys fallback).
    char announce_ip[64] = "", announce_mac[32] = "";
    if (s_local_iface(ifp, announce_ip, announce_mac) && announce_ip[0]) {
        zyre_set_header(node, "X-IP", "%s", announce_ip);
        if (announce_mac[0])
            zyre_set_header(node, "X-MAC", "%s", announce_mac);
    }

    if (ifp) zyre_set_interface(node, ifp);
    free(pub); free(sec); free(ifp);
    pthread_mutex_lock(&g_lock);
    g_node = node;
    pthread_mutex_unlock(&g_lock);
    return (jlong) (intptr_t) node;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_start(JNIEnv *env, jobject self) {
    // ENDPOINT-LESS start uses UDP beacons; zyre_set_interface pins the
    // NIC; zyre_gossip_connect() is the UDP-free alternative.
    return zyre_start(g_node) == 0 ? 0 : -1;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_join(JNIEnv *env, jobject self,
        jstring group) {
    char *g; jstring_to_c(env, group, &g);
    int rc = zyre_join(g_node, g);   // returns 0; effects flow via events
    free(g); return rc;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_shout(JNIEnv *env, jobject self,
        jstring group, jstring text) {
    char *g, *t; jstring_to_c(env, group, &g); jstring_to_c(env, text, &t);
    int rc = zyre_shouts(g_node, g, "%s", t);
    free(g); free(t); return rc;
}

JNIEXPORT jint JNICALL
Java_com_visioncamhealth_zyre_ZreNative_whisper(JNIEnv *env, jobject self,
        jstring peer, jstring text) {
    char *p, *t; jstring_to_c(env, peer, &p); jstring_to_c(env, text, &t);
    zuuid_t *id = zuuid_new();
    zuuid_set_str(id, p);
    int rc = zyre_whisper(g_node, id, "%s", t);
    zuuid_destroy(&id); free(p); free(t); return rc;
}

// Blocking; returns ONE event as a JSON object string, or "{}" on timeout.
// zyre_event_new() blocks up to the node's expiry window, so ONLY call it
// from the dedicated event-pump thread (never the UI/main thread).
JNIEXPORT jstring JNICALL
Java_com_visioncamhealth_zyre_ZreNative_nextEvent(JNIEnv *env, jobject self) {
    zyre_event_t *event = zyre_event_new(g_node);   // blocks
    if (!event) return (*env)->NewStringUTF(env, "{}");
    const char *type = zyre_event_type(event);
    const char *peer = zyre_event_sender(event);    // UPPERCASE uuid (zuuid_str)
    const char *name = zyre_event_name(event);
    const char *group = zyre_event_group(event);
    char buf[1024];
    snprintf(buf, sizeof buf,
        "{\"type\":\"%s\",\"peer_id\":\"%s\",\"peer_name\":\"%s\","
        "\"group\":\"%s\"}",
        type ? type : "", peer ? peer : "", name ? name : "",
        group ? group : "");
    zyre_event_destroy(&event);
    return (*env)->NewStringUTF(env, buf);
}

JNIEXPORT void JNICALL
Java_com_visioncamhealth_zyre_ZreNative_stop(JNIEnv *env, jobject self) {
    if (g_node) {
        zyre_stop(g_node);   // send the port-0/departure beacon BEFORE destroy
        zyre_destroy(&g_node);
        g_node = NULL;
    }
    if (g_auth) { zactor_destroy(&g_auth); g_auth = NULL; }
}

JNIEXPORT jstring JNICALL
Java_com_visioncamhealth_zyre_ZreNative_uuid(JNIEnv *env, jobject self) {
    return (*env)->NewStringUTF(env, zyre_uuid(g_node));
}
```

Spot the one deliberate structural difference from `zc_peer` (which
computes the announce inside its own flow): the wrapper decodes the
Z85 pair from `make curve-release` files and calls the same
`zcert_new_from(raw, raw)` path verified in the CURVE_PASS trial.

**`app/src/main/cpp/CMakeLists.txt`** (complete):

```cmake
cmake_minimum_required(VERSION 3.22)
project(zrejni C)

set(PREFIX "$ENV{ZRE_ANDROID_PREFIX}" CACHE PATH "prefix with .a + headers")
# ABI sanity: the static archives must match the ABI being built
# ($ANDROID_ABI: armeabi-v7a | arm64-v8a | x86_64).

add_library(zrejni SHARED zre_jni.c)
target_include_directories(zrejni PRIVATE "${PREFIX}/include")
target_compile_definitions(zrejni PRIVATE ZYRE_BUILD_DRAFT_API CZMQ_BUILD_DRAFT_API)
target_compile_options(zrejni PRIVATE
    -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong
    -Wall -Wextra -Wformat=2 -Wformat-security)
# Static archives carry their own deps in this exact order:
target_link_libraries(zrejni
    "${PREFIX}/lib/libzyre.a"
    "${PREFIX}/lib/libczmq.a"
    "${PREFIX}/lib/libzmq.a"
    "${PREFIX}/lib/libsodium.a"
    log dl m)
```

**`app/build.gradle.kts` additions:**

```kotlin
android {
    ndkVersion = "30.0.16248370"     // matches the verified build (was 28.2.13676358)
    defaultConfig {
        externalNativeBuild { cmake { arguments += "-DZRE_ANDROID_PREFIX=/abs/path/to/prefix" } }
        ndk { abiFilters += listOf("armeabi-v7a", "arm64-v8a", "x86_64") }
    }
    externalNativeBuild { cmake { path = file("src/main/cpp/CMakeLists.txt"); version = "3.22.1" } }
    packagingOptions { jniLibs { useLegacyPackaging = false } }
}
```

The `PREFIX` must be per-ABI — point it at
`~/zre-android-build/prefix/<abi>` for each configured ABI via a
per-ABI `-DZRE_ANDROID_PREFIX` argument (CMake receives ANDROID_ABI and
can assemble it there instead — either way the archives per ABI are
already built by `scripts/ndk/zre_static_all.sh`).

### 4.5 Kotlin layer: a service that stays out of the way

The zre node runs **inside the app's normal lifecycle** as a second,
independent foreground service — the pattern the repo already uses for
`MetricsService` (MainActivity starts it via `startForegroundService` +
bind; the only service today has `foregroundServiceType="dataSync|location"`,
and the ZMQ singleton `ZmqPublisher` lives on the `"camera"`
HandlerThread with a `sendLock`). The zre node deliberately shares
nothing with that socket: different transport, separate thread, own
notification — it stays visible-but-neutral while the app's normal
camera/stream work continues.

**`app/src/main/java/com/visioncamhealth/zyre/ZreNative.kt`:**

```kotlin
package com.visioncamhealth.zyre

object ZreNative {
    init { System.loadLibrary("zrejni") }

    external fun create(name: String, pubZ85: String, secZ85: String,
                        iface: String?): Long
    external fun start(): Int
    external fun join(group: String)
    external fun shout(group: String, text: String)
    external fun whisper(peer: String, text: String)
    external fun nextEvent(): String     // blocking JSON; pump thread only
    external fun stop()
    external fun uuid(): String
}
```

**`app/src/main/java/com/visioncamhealth/zyre/ZreService.kt`** (sketch —
the start/stop/foreground chrome is verbatim the `MetricsService`
pattern):

```kotlin
package com.visioncamhealth.zyre

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.*
import com.visioncamhealth.MainActivity   // start/stop like MetricsService

class ZreService : Service() {
    companion object {
        const val ACTION_START = "com.visioncamhealth.action.ZRE_START"
        const val ACTION_STOP = "com.visioncamhealth.action.ZRE_STOP"
        private const val CHANNEL_ID = "zre_service"
        private const val NOTIFICATION_ID = 2   // MetricsService uses 1
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null
    private lateinit var pumpThread: HandlerThread

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()      // IMPORTANCE_LOW like metrics
        pumpThread = HandlerThread("zre").apply { start() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, id: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                try { startForeground(NOTIFICATION_ID, buildNotification()) }
                catch (e: SecurityException) { stopSelf(); return START_NOT_STICKY }

                job?.cancel()
                job = scope.launch {
                    // Identity: the pair the operator pasted in Settings
                    // ("Mesh discovery keys"), stored app-private. The app
                    // never bundles or generates keys (see section 4.3).
                    val prefs = getSharedPreferences("zre_identity", MODE_PRIVATE)
                    val pub = prefs.getString("curve_pub", null)?.trim()
                    val sec = prefs.getString("curve_sec", null)?.trim()
                    if (pub.isNullOrEmpty() || sec.isNullOrEmpty()
                        || pub.length != 40 || sec.length != 40) {
                        Log.i(TAG, "no curve keys configured; refusing to run plain")
                        stopSelf(); return@launch
                    }
                    val handle = ZreNative.create("camhealth", pub, sec, null)
                        .also { if (it < 0) fail("bad curve keys; refusing to run plain") }
                    ZreNative.start()
                    ZreNative.join("CHAT")
                    // dedicated pump thread — blocks in native code
                    Handler(pumpThread.looper).post { pumpEvents() }
                }
            }
            ACTION_STOP -> { stopZre(); stopSelf() }
        }
        return START_NOT_STICKY
    }

    private fun pumpEvents() {
        while (true) {
            val json = ZreNative.nextEvent()
            // forward to UI/scope (EventBus/Flow/whatever the app uses)
        }
    }

    private fun stopZre() {
        job?.cancel()
        ZreNative.stop()                 // sends the departure beacon first
        pumpThread.quitSafely()
    }

    override fun onDestroy() { stopZre(); scope.cancel(); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null
    private fun createNotificationChannel() { /* verbatim MetricsService */ }
    private fun buildNotification(): Notification { /* verbatim MetricsService */ }
    private fun fail(msg: String) { Log.e("ZreService", msg) }
}
```

**Manifest + start path** (mirrors `MetricsService` lines of code):

```xml
<service android:name=".zyre.ZreService" android:exported="false"
         android:foregroundServiceType="dataSync"/>
<uses-permission android:name="android.permission.CHANGE_WIFI_MULTICAST_STATE"/>
```

Start it FROM the Activity like `startMetricsService()` does
(`startForegroundService(Intent(this, ZreService::class.java).setAction(ACTION_START))`
+ `bindService(..., BIND_AUTO_CREATE)`), and stop it with `ACTION_STOP`.
The dataSync foreground type plus the app's existing battery-optimization
exemption (onboarding already gates `isIgnoringBatteryOptimizations`) keep
it running with the screen off, alongside the normal app run.

**Verified facts it leans on, with were to check in code/tests:**

- `zyre_set_interval(node, 1000)` every C endpoint — libzyre's default 0
  is send-once beacons; peers go `EVASIVE` after 5 s if missing.
- Teardown order: the ZAP authenticator releases its socket before the
  node's context dies; this wrapper owns both (`g_auth`, `g_node`) so
  the order is enforced inside `stop()`.
- Event sender id is UPPERCASE hex; normalize once in Kotlin if needed.
- A fixed inbox port for the phone (server-style) needs
  `zyre_set_beacon_peer_port()` (draft) or `zyre_set_endpoint()` — both
  verified working on the phone under shell uid.

### 4.6 Manifest and Wi-Fi specifics

```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.CHANGE_WIFI_MULTICAST_STATE"/>
<uses-permission android:name="android.permission.WAKE_LOCK"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
```

Multicast/broadcast reception on Wi-Fi may require:

```kotlin
val wifi = context.getSystemService(WifiManager::class.java)
val lock = wifi.createMulticastLock("zre-beacon").apply {
    setReferenceCounted(false); acquire()
}
// ... release() in onDestroy()
```

**Verified on the trial phone: broadcasts from the PC were received
without any lock while running under adb shell; a run-as-app trial
should confirm the same, and the lock is the standard fallback.** The
lock must be released or the system degrades the whole Wi-Fi stack.

Also verified: this phone's `getifaddrs()`/`RTM_GETLINK` **is blocked
for adb-shell uid (Android docs: restricted since targeting SDK 30)**,
and the czmq patch in `scripts/ndk/zre_static_all.sh` (pure-ioctl
interface enumeration) is what makes zbeacon work — it is already
baked into the produced `.a` files. Keep it when rebuilding.

### 4.7 Boot autostart (implemented, research-backed)

The zre node starts at device boot: `BootReceiver` → `startForegroundService`
→ `ZreService` (foreground type `connectedDevice`, sticky restart, Curve
always-on, same JNI glue as section 4.4 plus the thread-safe
`signalStop()`/content-bearing event JSON). The full platform research —
every Android version's rules, the `dataSync`-from-boot block on Android
15+ (with its 6 h timeout), the `connectedDevice` choice, MIUI/HyperOS
Autostart reality, `ACCESS_LOCAL_NETWORK` for Android 17, all test knobs
and precedent code — is documented claim-by-claim with sources in
**`scripts/ndk/BOOT-AUTOSTART-RESEARCH.md`** (shipped next to this doc).
Nothing in the app code is OEM-specific: receivers catch a boot-start
rejection and log it, and the version split (`startForegroundService` on
API 26+, plain `startService` below) is the universal path.

### 4.8 The Python peer on the PC (verified counterparts)

All three conversation shapes, exactly as verified today:

```python
# (1) beacon discovery, same Wi-Fi
node = ZreNode("pyphone")
await node.start(); await node.join("CHAT")
async for ev in node.events(): ...

# (2) gossip (UDP-free): PC hosts the ZGS.1 hub, phone connects to it
node.gossip_bind("tcp://*:19872")            # before start()

# (3) Curve/secure (desktop-verified recipe, docs/LIBZYRE_INTEROP.md):
cert = (pub, sec) = fresh Curve25519 keys
node.set_zcert(pub, sec)                     # advertises X-PUBLICKEY header
await node.start()
# peers see v3 beacons (54 bytes) and connect via CURVE; ZAP domain 'global'
```

The gossip hub can also be started standalone (libzyre peers can
`zyre_gossip_connect()` to it, wire-compatible with czmq's `zgossip`):

```sh
python3 -m zre.gossip --port 15671     # ZGS.1-compatible hub
```

Port/cap facts: UDP **5670** for discovery on BOTH sides (hard-wired in
both implementations), TCP inbox freely chosen (ephemeral by default,
any port ≥ 1024 works unprivileged).

## 5. Verified event shapes (Python side logs, quoted)

From the actual phone trial (`phone_trial.py beacon`):

```
PY {'type': 'ENTER', 'peer_id': '65894de966112e8bcc70e76063445349',
    'peer_name': 'phone1', 'address': 'tcp://10.0.89.13:49152'}
PY {'type': 'JOIN',  'peer_id': '65894de966112e8bcc70e76063445349',
    'peer_name': 'phone1', 'group': 'CHAT'}
PY {'type': 'SHOUT', 'peer_id': '65894de966112e8bcc70e76063445349',
    'peer_name': 'phone1', 'group': 'CHAT', 'payload': b'hello-from-phone'}
PHONE_PASS
```

Phone-side (`zc_peer --verbose`), same seconds:

```
I: 26-10-19 20:45:13 zbeacon: configured, hostname=10.0.89.13
EVENT ENTER peer=B097B6AD2BC540C29BCE35DE986C39AF name=pyphone address=tcp://10.0.89.21:44289
I: 26-10-19 20:45:24 (phone1) recv WHISPER from peer=pyphone sequence=2
EVENT WHISPER peer=B097B6AD2BC540C29BCE35DE986C39AF name=pyphone content=hello-from-Python
I: 26-10-19 20:45:24 (phone1) send SHOUT to peer=pyphone sequence=2
```

`zc_peer` prints one `EVENT <TYPE> ...` line per event occurrence
(`READY/ENTER/JOIN/SHOUT/WHISPER/LEAVE/LEADER/EVASIVE/SILENT/EXIT/DONE/
CURVE-PUBKEY/LOCAL-IFACE`) — handy for assertions in adb-log-driven
tests. The CURVE/announce trials print `EVENT LOCAL-IFACE name=... ip=...
mac=...` at startup (mac=- when the device denies it, see section 2).

## 6. CLI of the phone-side harness (`zc_peer`)

```
zc_peer --name NAME [options]           (Curve is ALWAYS on — no --curve flag)
  --curve-pub Z85       Curve public key (both or neither)
  --curve-sec Z85       Curve secret key; missing pair -> ephemeral zcert
  --zap-domain D        ZAP domain (default global)
  --beacon-port N       UDP discovery port (default 5670)
  --beacon-peer-port N  fixed TCP inbox port for WAN dialling
  --interface IF        pin NIC (name or IP) for beacons
  --endpoint EP         bind this endpoint, disable beacon (gossip mode)
  --gossip-connect EP   connect to a gossip hub
  --gossip-bind EP      host a gossip hub
  --join GROUP          join a group (repeatable)
  --contest GROUP       leader election in GROUP (repeatable)
  --shout "GRP:TEXT"    shout TEXT to GRP every --shout-every seconds
  --whisper-on-enter T  whisper T to every peer that enters
  --duration S          run for S seconds (default 10)
  --verbose             EVENT lines + wire log
  --announce NAME=VAL   HELLO header (X-IP/X-MAC are auto-added)
```

## 7. Troubleshooting (verified rows only)

| Symptom | Cause | Fix |
|---|---|---|
| `E: zbeacon: No broadcast interface found` | Android 11+ blocks `RTM_GETLINK`, breaking `getifaddrs()` | use the czmq patch in `scripts/ndk/zre_static_all.sh` (ioctl enumeration) — already in the verified `.a` files |
| `ip link show` → `Permission denied` on the phone | same restriction, shell uid | not fixable by zre; irrelevant once the patch is in |
| `X-MAC` missing from the announcement | Android 13+ denies both MAC paths (`SIOCGIFHWADDR` ioctl — verified on 23028RNCAG — and `/sys/class/net/<if>/address`: `Permission denied`) for unprivileged uids | by design X-MAC is "when found": X-IP always announces; a privileged/system build that can read the MAC announces it with no extra code |
| adb shell with `( cmd & )` hangs forever | Android keeps the session until all children exit | run the peer as a **foreground** adb shell inside a host-side `nohup` (as `make launch-phone` does) |
| `EVENT ERROR invalid-curve-key` at start | the two Z85 keys are incomplete/mangled — zc_peer refuses instead of running plain | pass a valid pair (`make curve-check` validates derivation); launch via `ARGS_FILE` so no shell eats the keys |
| Peer goes `EVASIVE` ~5 s after connecting | libzyre default beacon interval is 0 (send-once) | `zyre_set_interval(node, 1000)` — both sides |
| `ENTER` seen but `SHOUT`/`WHISPER` never arrive | endpoints not dialable (NAT/host-only adapter AP isolation) | switch to gossip mode or pin `--endpoint` on a reachable address |
| Phone shows `EVENT ENTER` twice for the same peer | (pre-2026-10-19 Python bug) gossip keys are UPPERCASE (`zuuid_str`); Python created a duplicate peer — fixed by lowercasing in `_gossip_require` | update the Python side (already fixed in `zre/node.py`) |
| pytest hangs at exit (term() inside `Context.__del__`) | fixed 2026-10-19: `ZreNode.stop()` no longer skips teardown when the run loop set `_running` false itself, and terminates with `destroy(linger=0)` | update `zre/node.py` (already fixed; see `tests/test_teardown.py`) |
| `duplicate symbol sha1_*` at static link | libzmq (WebSocket sha1) vs czmq (foreign sha1) | `-DENABLE_WS=OFF` for libzmq (in the build script) |
| `-lstdc++` not found / `__android_log_print` undefined | NDK r30 has no libstdc++; czmq/zyre tools miss `liblog` | `CMAKE_C_STANDARD_LIBRARIES` + staged-`.pc` sed — in the build script |
| App killed ~5 min in background | Doze / background restrictions | keep the zre node inside a **foreground service** (like `MetricsService`); WAKE_LOCK already in the app |

## 8. Task list for the Android side (tick as solved)

Everything above this line was solved and re-run on 2026-10-19. This
list is the honest hand-off: what a human still has to do inside the
app repo, one checkbox per item.

- [x] zre stack runs Curve end-to-end phone↔PC (BEACON/CURVE/GOSSIP/DIAL
  trials PASS with avedge-generated keys; no plain mode anywhere).
- [x] `zc_peer` Curve-always-on (the `--curve` toggle removed), refuses
  invalid keys instead of falling back to plain (`EVENT ERROR invalid-curve-key`).
- [x] Announce X-IP always, X-MAC when the kernel allows (verified:
  Android 13 retail denies both MAC paths under a non-privileged uid —
  the header is simply omitted).
- [x] pytest teardown hang (`term()` blocked in `__del__`) — fixed in
  `zre/node.py` `stop()` (+ `destroy(linger=0)`); regression tests in
  `tests/test_teardown.py`, suite 105 passed twice with no wedge.
- [x] Key tooling: `make curve-keys / curve-check / curve-release` in
  `scripts/ndk` (avedge-compatible format; cross-checked against the
  avedge toolchain).
- [x] Static cross-build for all three ABIs, `NEEDED` = bionic-only,
  hardened compile flags, `zbeacon` works (ioctl czmq patch).
- [x] Key-possession flow settled (2026-10-20): NO assets bundling and
  NO in-app generation — the pair is pasted into Settings ("Mesh
  discovery keys"), stored app-private (`zre_identity` prefs), the
  service refuses to run plain without it, and rotation = paste a new
  pair then rebuild (section 4.3; emulation + rotation evidence in
  `../../docs/SESSION-VALIDATION.md`). **Historical: the current plain
  app has no key flow at all (see CURVE-MESH.md).**
- [x] Native glue in `vision-cam-health` written: `app/src/main/cpp/zre_jni.c`
  + `CMakeLists.txt` + Gradle `externalNativeBuild` (section 4.4, with a
  thread-safe `signalStop()` so the pump thread owns the teardown, and
  gossipConnect for UDP-free networking) — `ndkVersion` pinned to
  30.0.16248370 (the verified build).
- [x] `ZreNative.kt` + `ZreService.kt` written (section 4.5 + 4.7):
  foreground type `connectedDevice` (never `dataSync` — it is blocked
  from BOOT_COMPLETED on Android 15+ and has the 6 h timeout), sticky
  restart, synchronous `startForeground()`, notification channel, keys
  from the settings paste with plain-mode refusal.
- [x] Assembled APK verified on the build host (2026-10-20, final
  `ZreService`): gradle green with the static prefixes for all three
  ABIs, release APK R8-minified, zero `assets/zre` entries, V2-signed
  CN=VisionCam Health; boot tests green on the emulator —
  `install -r` keeps the stored pair, one clean pass to `zre node up`
  (detail in `docs/SESSION-VALIDATION.md`).
- [x] `CHANGE_WIFI_MULTICAST_STATE` permission added
  (AndroidManifest.xml). The app node runs GOSSIP mode (`gossipConnect`
  plus a hub connect are verified on-device), so UDP beacons are not in
  its path and `MulticastLock` stays unwired unless beacon mode is ever
  added.
- [x] ProGuard/R8 keep rules for `ZreNative`/`ZreService`
  (`app/proguard-rules.pro`); verified in the shipped release build.
- [x] In-app Curve conversation trial: assisted emulator trial
  `GOSSIP_PASS` (app↔Python hub, ENTER/JOIN/SHOUT zre-ready,
  `X-PUBLICKEY` assisted dial) — recipe and evidence in
  `scripts/ndk/README.md` § "The APP (zre_jni) on the emulator". App-uid
  MAC read: not attempted (the announcement omits X-MAC when denied,
  which Android 13 does — section 7).
- [x] Same trials on the emulator: DONE with the app itself on
  `emulator-5554` (assisted path above) — no `zc_peer-x86_64` CI
  variant needed for this evidence.
- [ ] Long-run stability soak in-app (30+ min screen-off while streaming
  continues; watch `EVASIVE`/reconnects) and battery/thermals at the
  chosen shout cadence.

## 9. Repository pointers

- This directory — the annotated build package (script, Makefile, phone
  trials, curve key tooling, EXE/verification notes, and this doc).
- `interop/zc_peer.c` — the C harness compiled on the phone (Curve
  always-on + X-IP/X-MAC announcement — the JNI wrapper mirrors it).
- `scripts/ndk/curve_keys.py` — the `make curve-*` implementation.
- `zre/node.py` + `zre/gossip.py` — Python peer, ZGS.1-compatible hub.
- `tests/test_teardown.py` — regression tests for the stop()/GC hang.
- Build source pins (verified set): libzmq `4649337`, czmq `b669c7e`,
  zyre `f716769` (`v2.0.1-96`), libsodium `1.0.20-stable`, NDK r30.
