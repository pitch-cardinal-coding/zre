/* Probe: derive the real JNINativeInterface slot indexes zre_jni.c needs
 * (the three JNIEnv functions it calls) from the actual jni.h shipped
 * alongside (the NDK variant: struct name has no trailing underscore),
 * and emit them as a header. Compile+run FIRST; the harness includes
 * the generated fake_env_idx.h. */
#include <stdio.h>
#include <stddef.h>
#include <jni.h>

int main(void) {
    struct JNINativeInterface *t = NULL;   // NDK jni.h declares this name
    long getchars = offsetof(struct JNINativeInterface, GetStringUTFChars)
                    / sizeof(void *);
    long release = offsetof(struct JNINativeInterface, ReleaseStringUTFChars)
                   / sizeof(void *);
    long newstr = offsetof(struct JNINativeInterface, NewStringUTF)
                  / sizeof(void *);
    FILE *out = fopen("fake_env_idx.h", "w");
    if (!out) return 2;
    fprintf(out, "#define IDX_GET_STRING_UTF_CHARS %ld\n", getchars);
    fprintf(out, "#define IDX_RELEASE_STRING_UTF_CHARS %ld\n", release);
    fprintf(out, "#define IDX_NEW_STRING_UTF %ld\n", newstr);
    fclose(out);
    printf("probe: getchars=%ld release=%ld newstr=%ld\n",
           getchars, release, newstr);
    return 0;
}
