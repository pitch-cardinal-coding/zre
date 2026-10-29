# valgrind-harness — the zero-leak proof for `zre_jni.c`

This directory holds the evidence tooling for the "zero memory leaks in
C" requirement of the vision-cam-health zre node
(`app/src/main/cpp/zre_jni.c`).

Target machine: ANY Linux x86_64 host reachable over SSH, freshly
provisioned with two things only:

1. `valgrind` (`sudo apt-get install valgrind` on Ubuntu/Debian).
2. A local build of the pinned libzyre stack — libsodium, libzmq,
   czmq, zyre — with headers and libs under `$HOME/.local` (the same
   versions the Android app pins; see the main `scripts/ndk/README.md`
   for the exact pins and the `zre_static_all.sh` build order, which
   works unchanged on a plain Linux host when you skip the Android
   cross flags).

Nothing runs inside the Android build; the harness is a separate
native program that links the same `zre_jni.c` source against the
pinned stack plus a fake JNI environment.

## Files

| File | Role |
|------|------|
| `probe_env.c` | Derives the JNI function-table slot indexes mechanically from the NDK `jni.h` on this machine (no layout guessing); emits `fake_env_idx.h`. |
| `test_zrejni.c` | Full-lifecycle driver: plain create, double-create refused, uuid, start, join, shout, whisper-to-self, `stop()`, `{}` terminator, idempotent `stop()`, uuid-empty after teardown. Driver-owned strings are all freed. |
| `t_probe.c` | PRODUCTION topology probe: the pump thread blocks inside `nextEvent()` while a carrier thread runs `signalStop()`; the pump must unblock (< 5 s), the driver stops + exits. Mirrors `ZreService.Kt`'s pumping thread + `stopZreNode()`. |
| `vg_run.sh` | Compile + valgrind the lifecycle driver. |
| `vg_probe.sh` | Compile + valgrind the production-topology probe. |

## How to run

Replace `VMSSH` below with your SSH access to the valgrind host
(for example `VMSSH="ssh iam@<vm-host>"`; the lab uses
`~/devcode/visor/scripts/vm-ssh.sh "de:ad:be:ef:00:02"`).

```bash
# one-time staging: the file under test, the probes, NDK jni.h, device keys
VMSSH='sh ~/devcode/visor/scripts/vm-ssh.sh "de:ad:be:ef:00:02"'   # adapt
$VMSSH "mkdir -p ~/vgcheck"
$VMSSH "cat > ~/vgcheck/zre_jni.c" \
    < ~/devcode/vision-cam-health/app/src/main/cpp/zre_jni.c
for f in probe_env.c test_zrejni.c t_probe.c vg_run.sh vg_probe.sh; do
  $VMSSH "cat > ~/vgcheck/$f" < "$f"
done
tar -C <NDK>/sysroot/usr/include -czf - jni.h jni_md.h \
    | $VMSSH "mkdir -p ~/vgcheck/jni-include && tar -xzf - -C ~/vgcheck/jni-include"
# no key material: the app's node is PLAIN (no Curve/ZAP) since 2026-10-23

# Run vg_run.sh FIRST and wait: it compiles + runs probe_env, which
# generates fake_env_idx.h — vg_probe.sh needs that header. Running them
# concurrently on a fresh host races the header generation.
$VMSSH "bash ~/vgcheck/vg_run.sh > ~/vgcheck/vg_final.out 2>&1 < /dev/null"
$VMSSH \
  "nohup timeout 240 bash ~/vgcheck/vg_probe.sh > ~/vgcheck/vg_probe_final.out 2>&1 < /dev/null & echo GO"
```

`device.pub`/`device.priv` are not needed in plain mode (the current
node takes no key material). **Mode B (restored Curve):** use the
drivers/scripts from `../curve-reference/valgrind/`, stage
`device.pub`/`device.priv`, and run the same two scripts — see
`CURVE-MESH.md` section 4.6.

`zre_jni.c` always gets re-staged from the app source in the app repo —
never edit the staged copy directly (the build-host copies follow the
same rule).

## Verdicts (2026-10-23, plain-mode `zre_jni.c`, md5 8abdd680…)

Lifecycle driver (console output; the transcript is written to `vg.out`):

```
VG-HARNESS DONE events=1
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

Production-bc topology probe (`vg_probe.out`):

```
probe_end: nextEvent returned after 2067.9 ms (need < 5000)
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

## Historical verdicts — Curve-era file (2026-10-20, md5 8facaf7a…)

Lifecycle driver (`vg.out`):

```
VG-HARNESS DONE events=1
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

Production-bc topology probe (`vg_probe.out`):

```
probe_end: nextEvent returned after ~2000 ms (need < 5000)
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

## Historical notes kept for future work

- The 66-error first run traced to a **driver bug** (using the uuid
  string after freeing it), not to `zre_jni.c`. Driver and file are now
  both clean; the same fate met an early second driver that called
  `nextEvent` after `stop()` without the destroy-first contract (see the
  comment in `test_zrejni.c` step 6).
- `zyre_stop` sends STOP, which the blocked `nextEvent()` returns as a
  `{"type":"STOP"}` event (first return). Production is safe because the
  pump exits through its `pumping` flag and then runs `stop()` itself;
  carriers only `signalStop()`. A `{}` result appears only when no node
  exists (after destroy), e.g. a single-threaded drain after `stop()`.
  `t_probe` proves the cross-thread STOP → exit path end to end.
