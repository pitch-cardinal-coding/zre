/* Minimal fake-JNI driver for zre_jni.c: exercises the REAL exported
 * Java_com_visioncamhealth_zyre_ZreNative_* symbols against the real
 * zyre/czmq/zmq/sodium (the VM's ~/.local install — same pinned sources
 * the Android prefix built from), under valgrind. The fake JNIEnv is
 * built from the slot indexes PROBED from this machine's jni.h
 * (fake_env_idx.h) — no layout guessing. NewStringUTF results are
 * registered and freed by the driver (Java heap ownership), so valgrind
 * sees exactly the file's own allocation behavior.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <jni.h>
#include "fake_env_idx.h"

/* The real symbols from zre_jni.o: C prototypes for this driver. */
extern jlong Java_com_visioncamhealth_zyre_ZreNative_create(JNIEnv*, void*, jstring, jstring);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_gossipConnect(JNIEnv*, void*, jstring);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_start(JNIEnv*, void*);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_join(JNIEnv*, void*, jstring);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_shout(JNIEnv*, void*, jstring, jstring);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_whisper(JNIEnv*, void*, jstring, jstring);
extern jstring Java_com_visioncamhealth_zyre_ZreNative_nextEvent(JNIEnv*, void*);
extern void  Java_com_visioncamhealth_zyre_ZreNative_signalStop(JNIEnv*, void*);
extern void  Java_com_visioncamhealth_zyre_ZreNative_stop(JNIEnv*, void*);
extern jstring Java_com_visioncamhealth_zyre_ZreNative_uuid(JNIEnv*, void*);

static void *vtable[512];
static JNIEnv table;      /* pointer-to-function-table (real JNIEnv value) */
static JNIEnv *env;       /* what native code receives: JNIEnv* */

static const char *fake_GetStringUTFChars(void *e, void *str, void *copy) {
    (void) e; (void) copy;
    return str ? (const char *) str : "";
}
static void fake_ReleaseStringUTFChars(void *e, void *str, const char *u) {
    (void) e; (void) str; (void) u;    /* fake jstring: nothing to release */
}
static void *fake_NewStringUTF(void *e, const char *u) {
    (void) e;
    return u ? strdup(u) : NULL;
}

static void make_env(void) {
    memset(vtable, 0, sizeof vtable);
    vtable[IDX_GET_STRING_UTF_CHARS] = (void *) fake_GetStringUTFChars;
    vtable[IDX_RELEASE_STRING_UTF_CHARS] = (void *) fake_ReleaseStringUTFChars;
    vtable[IDX_NEW_STRING_UTF] = (void *) fake_NewStringUTF;
    table = (JNIEnv) vtable;
    env = &table;
}

static void jfree(void *s) { free(s); }

#define CHK(cond, msg) \
    do { if (!(cond)) { printf("VG-HARNESS FAIL: %s\n", msg); exit(1); } } while (0)

int main(void) {
    make_env();

    void *name = strdup("vgcheck");

    /* 1) plain create (no key material — the app runs without Curve) */
    CHK(Java_com_visioncamhealth_zyre_ZreNative_create(env, NULL, name, NULL) > 0,
            "create -> handle");

    /* 2) second create refused */
    CHK(Java_com_visioncamhealth_zyre_ZreNative_create(env, NULL, name, NULL) == -2,
            "double create -> -2");

    /* 3) uuid present */
    char *u0 = (char *) Java_com_visioncamhealth_zyre_ZreNative_uuid(env, NULL);
    CHK(u0 && strlen(u0) == 32, "uuid 32 hex chars");
    /* u0 stays allocated: step 5's whisper reuses the uuid string; it is
     * freed together with the rest of the transcribed strings below. */

    /* 5) lifecycle. The gossip leg is optional: the VM's distro czmq
     *    asserts on the pre-start set_endpoint path, while the pinned
     *    Android build (and the emulator run) handle it — gating keeps
     *    this leak run focused on the file's own allocation behavior. */
    CHK(Java_com_visioncamhealth_zyre_ZreNative_start(env, NULL) == 0, "start");
    if (getenv("VG_GOSSIP")) {
        char *ep = strdup("tcp://127.0.0.1:19872");
        Java_com_visioncamhealth_zyre_ZreNative_gossipConnect(env, NULL, ep);
        jfree(ep);
    }
    char *gr = strdup("CHAT");
    CHK(Java_com_visioncamhealth_zyre_ZreNative_join(env, NULL, gr) == 0, "join");
    jfree(gr);
    char *gs = strdup("CHAT"), *gt = strdup("vg-check");
    CHK(Java_com_visioncamhealth_zyre_ZreNative_shout(env, NULL, gs, gt) == 0, "shout");
    jfree(gs); jfree(gt);
    char *wp = strdup(u0), *wt = strdup("vg-whisper");
    CHK(Java_com_visioncamhealth_zyre_ZreNative_whisper(env, NULL, wp, wt) == 0, "whisper");
    jfree(wp); jfree(wt);
    jfree(u0);   /* the uuid string is driver-owned; nothing needs it now */

    /* 6) lifecycle stop, then the drain. stop() destroys the node pipe
     *    (departure beacon first) — per the pinned zyre that is what
     *    unblocks the caller's recv; the next nextEvent returns "{}"
     *    once g_node is NULL. Single-thread harness mirrors the
     *    production pump's post-stop state; the blocking-recv +
     *    cross-thread signalStop topology is covered by t_probe. */
    sleep(2);
    Java_com_visioncamhealth_zyre_ZreNative_stop(env, NULL);
    int events = 0, sawTerminator = 0;
    for (int i = 0; i < 50; i++) {
        char *j = (char *) Java_com_visioncamhealth_zyre_ZreNative_nextEvent(env, NULL);
        events++;
        if (!j) break;
        if (strcmp(j, "{}") == 0) { jfree(j); sawTerminator = 1; break; }
        jfree(j);
    }
    CHK(sawTerminator, "pump reached the {} terminator");

    /* 7) idempotent teardown */
    Java_com_visioncamhealth_zyre_ZreNative_stop(env, NULL);
    Java_com_visioncamhealth_zyre_ZreNative_stop(env, NULL);

    char *u1 = (char *) Java_com_visioncamhealth_zyre_ZreNative_uuid(env, NULL);
    CHK(u1 && u1[0] == '\0', "uuid empty after teardown");
    jfree(u1);

    jfree(name);
    printf("VG-HARNESS DONE events=%d\n", events);
    return 0;
}
