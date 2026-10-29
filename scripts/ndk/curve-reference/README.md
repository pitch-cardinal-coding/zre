# curve-reference — the removed Curve implementation, preserved for reuse

This directory stores the **complete Curve-secured ZRE implementation
that was removed from the mobile app on 2026-10-23**, so it can be
restored exactly. It is code, not a summary: the files here compile and
were re-verified with the valgrind harness.

The design narrative, evidence, restoration checklist and the AI/human
handoff protocol live in [`../CURVE-MESH.md`](../CURVE-MESH.md).

## Provenance

| File here | Source | Notes |
|---|---|---|
| `android/zre_jni.c` | pre-removal app working tree | md5 `8facaf7a141ff5fe588a9c20b87d4108` — the Curve file that passed the 2026-10-20 valgrind run |
| `android/ZreNative.kt` | same tree | `create(name, pubZ85, secZ85, iface)` |
| `android/ZreService.kt` | same tree | prefs `zre_identity`, refusal, `ACTION_REKEY`, single executor |
| `android/MainActivity.kt` | same tree | **pre-upstream-merge snapshot (2026-10-20)**: the Curve sections are unchanged by the later merge, but this file lacks the zoom features added upstream afterwards — extract the Curve parts, do not overwrite the current file wholesale |
| `android/strings-mesh.xml` | same tree | the nine removed `mesh_*` strings, exact |
| `valgrind/test_zrejni.c` | reconstructed from the pre-removal driver | Curve-era: bad-key refusal, keyed lifecycle |
| `valgrind/t_probe.c` | reconstructed from the pre-removal driver | Curve-era production-topology probe |
| `valgrind/vg_run.sh`, `vg_probe.sh` | reconstructed | Curve-era: export `VG_PUB`/`VG_SEC` from `device.pub`/`device.priv` |

No private keys are stored here. Never add `.priv`/keystore material to
this directory or to the repository. The frozen `zre_jni.c` header still
describes `signalStop()` as returning `"{}"`; the accurate description
(STOP event, `{}` only after destroy) is in CURVE-MESH.md section 2.4 —
the file is kept byte-identical as evidence and is not edited.

## How to reuse

Follow the restoration checklist in [`../CURVE-MESH.md`](../CURVE-MESH.md)
section 4.2. In short:

1. Apply the Curve code from `android/zre_jni.c` to the app's
   `app/src/main/cpp/zre_jni.c` (all three return paths must unlock
   `g_lock`; the reference file shows the verified shape).
2. Restore the Kotlin surface from `android/ZreNative.kt` and
   `android/ZreService.kt`. The reference gossip recreate path already
   contains `oldPump?.join(5000)` + `stopping = false`; the reference
   `ACTION_REKEY` branch does NOT. Apply the same sequence to the rekey
   branch as described in CURVE-MESH.md section 4.2.3.
3. Extract the Curve UI block and helpers from
   `android/MainActivity.kt` (search for `Z85_CHARS`,
   `applyMeshIdentity`, `mesh_header`) into the CURRENT file; do not
   replace the current file.
4. Merge `android/strings-mesh.xml` into `res/values/strings.xml`; update
   the zre notification description back to Curve wording if desired.
5. Replace the harness drivers/scripts under
   `scripts/ndk/valgrind-harness/` with the copies in `valgrind/` (or
   re-apply their three call-site changes), stage `device.pub/priv`,
   run `vg_run.sh` first, then `vg_probe.sh`.

## Verification of this preserved code

The preserved `android/zre_jni.c` and the `valgrind/` drivers were
compiled and run on the valgrind host on 2026-10-23:

- `test_zrejni`: `VG-HARNESS DONE events=1`, no leaks, 0 errors
- `t_probe`: `nextEvent returned after ... ms (need < 5000)`, no leaks,
  0 errors

This proves the stored files are a working Curve implementation, not
just text. (See the output recorded below.)

## Reference harness output (2026-10-23, Curve reference file)

```text
zre_jni.c md5: 8facaf7a141ff5fe588a9c20b87d4108
test_zrejni:  VG-HARNESS DONE events=1
              All heap blocks were freed -- no leaks are possible
              ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
t_probe:      probe_end: nextEvent returned after 2084.4 ms (need < 5000)
              All heap blocks were freed -- no leaks are possible
              ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

Both runs used the Curve reference `zre_jni.c` plus the `valgrind/`
drivers and scripts from this directory, on the pinned host stack. The
harness verdicts from 2026-10-20 (same file) were identical apart from
the unblock time (~2000 ms).
