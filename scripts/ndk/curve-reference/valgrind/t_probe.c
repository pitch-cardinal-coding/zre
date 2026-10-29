/* Probe: the PRODUCTION teardown topology from ZreService.kt:
 * pump thread blocks in nextEvent(); carrier thread stops the node.
 * Question: does the pump UNBLOCK within 5 s of signalStop()?
 *   probe_start  prints the procedures before the sweep;
 *   probe_end    prints the verdict; exit code 0 == unblocked.
 * Sample: LD_LIBRARY_PATH=~/.local/lib ./t_probe
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <time.h>
#include <jni.h>
#include "fake_env_idx.h"

extern jlong Java_com_visioncamhealth_zyre_ZreNative_create(JNIEnv*, void*, jstring, jstring, jstring, jstring);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_start(JNIEnv*, void*);
extern jint  Java_com_visioncamhealth_zyre_ZreNative_join(JNIEnv*, void*, jstring);
extern jstring Java_com_visioncamhealth_zyre_ZreNative_nextEvent(JNIEnv*, void*);
extern void  Java_com_visioncamhealth_zyre_ZreNative_signalStop(JNIEnv*, void*);
extern void  Java_com_visioncamhealth_zyre_ZreNative_stop(JNIEnv*, void*);
extern jstring Java_com_visioncamhealth_zyre_ZreNative_uuid(JNIEnv*, void*);

static void *vtable[512];
static JNIEnv table;
static JNIEnv *env;

static const char *fake_GetStringUTFChars(void *e, void *str, void *copy) {
    (void) e; (void) copy;
    return str ? (const char *) str : "";
}
static void fake_ReleaseStringUTFChars(void *e, void *str, const char *u) {
    (void) e; (void) str; (void) u;
}
static void *fake_NewStringUTF(void *e, const char *u) {
    (void) e;
    return u ? strdup(u) : NULL;
}

static void *carrier(void *arg) {
    (void) arg;
    sleep(2);
    printf("probe_end carrier: signalling stop\n");
    fflush(stdout);
    Java_com_visioncamhealth_zyre_ZreNative_signalStop(env, NULL);
    printf("probe_end carrier: signalStop returned\n");
    fflush(stdout);
    return NULL;
}

static void make_env(void) {
    memset(vtable, 0, sizeof vtable);
    vtable[IDX_GET_STRING_UTF_CHARS] = (void *) fake_GetStringUTFChars;
    vtable[IDX_RELEASE_STRING_UTF_CHARS] = (void *) fake_ReleaseStringUTFChars;
    vtable[IDX_NEW_STRING_UTF] = (void *) fake_NewStringUTF;
    table = (JNIEnv) vtable;
    env = &table;
}

int main(void) {
    make_env();
    const char *kpub = getenv("VG_PUB") ? getenv("VG_PUB") : "";
    const char *ksec = getenv("VG_SEC") ? getenv("VG_SEC") : "";
    if (strlen(kpub) != 40 || strlen(ksec) != 40) { printf("probe: keys\n"); return 3; }

    char *name = strdup("vgprobe"), *pub0 = strdup(kpub), *sec0 = strdup(ksec);
    printf("probe_start: create\n");
    fflush(stdout);
    Java_com_visioncamhealth_zyre_ZreNative_create(env, NULL, name, pub0, sec0, NULL);
    Java_com_visioncamhealth_zyre_ZreNative_start(env, NULL);
    char *g = strdup("CHAT");
    Java_com_visioncamhealth_zyre_ZreNative_join(env, NULL, g);
    free(g);

    pthread_t t;
    pthread_create(&t, NULL, carrier, NULL);

    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    printf("probe_start: pump enters nextEvent\n");
    fflush(stdout);
    char *j = (char *) Java_com_visioncamhealth_zyre_ZreNative_nextEvent(env, NULL);
    struct timespec t1;
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double ms = (t1.tv_sec - t0.tv_sec) * 1000.0 + (t1.tv_nsec - t0.tv_nsec) / 1.0e6;
    if (j) free(j);
    printf("probe_end: nextEvent returned after %.1f ms (need < 5000)\n", ms);
    fflush(stdout);

    Java_com_visioncamhealth_zyre_ZreNative_stop(env, NULL);
    pthread_join(t, NULL);
    free(name); free(pub0); free(sec0);

    if (ms < 5000.0) return 0;      /* the pump unblocked via signalStop */
    return 1;                       /* pump stayed blocked > 5 s */
}
