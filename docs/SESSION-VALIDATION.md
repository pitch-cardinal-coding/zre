# Session validation — zero leaks in P (Python) and C (zre_jni), production build

Verified-facts record for the zre mesh: the Python implementation
(`~/devcode/zre`), the C JNI node of the Android app
(`~/devcode/vision-cam-health/app/src/main/cpp/zre_jni.c`), and the
production app build. Everything below is measured evidence, not claims.

> **Historical note (2026-10-23):** sections 3-5 describe the Curve-era
> app build (keys, refusal, rotation). The app now runs plain ZRE;
> current state and restoration are in `scripts/ndk/CURVE-MESH.md`, and
> the current plain-mode leak verdict is in
> `scripts/ndk/valgrind-harness/README.md`.

## 1. Python side (P)

- Root cause chain documented and fixed: `ZreNode.stop()` used to
  be a no-op when `_running` was already `False` (29 test call-sites and
  one production path leaked the shared context) — rewritten to an
  always-idempotent teardown; `test_security.py` stopped the ZAP
  authenticators after `node.stop()` (socket wedge) — order corrected.
- Verdict that still stands: four consecutive full-suite runs,
  105 passed / 0 failed (latest: 2026-10-20, ~2:08), plus the 20-node
  soak plateau (threads stable, no leak growth).

## 2. C side (C)

Target: `vision-cam-health/app/src/main/cpp/zre_jni.c`.
Tooling: `scripts/ndk/valgrind-harness/` — runs on any Linux host with
valgrind and the pinned libzyre/czmq/zmq/sodium under `~/.local`
(runbook in that directory).

Final verdict on the Curve-era file (md5 `8facaf7a…`; the current
plain file's verdict and md5 live in
`scripts/ndk/valgrind-harness/README.md`):

```
VG-HARNESS DONE events=1
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

for BOTH drivers:

- `test_zrejni.c` — full lifecycle (bad-keys refusal, create,
  double-create refusal, uuid, start, join, shout, whisper, stop,
  `{}` terminator, idempotent stop, uuid-empty-after-teardown).
- `t_probe.c` — the production topology: pump thread blocking in
  `nextEvent()`, carrier `signalStop()` at t=2 s, unblock measured
  ~2000 ms, teardown + process exit.

History for the record (kept in `valgrind-harness/README.md`): the
first clean-leak run showed 66 runtime errors — all traced to a driver
bug (use-after-free of the uuid string), not to `zre_jni.c`. A later
driver rewrite made the single-threaded harness deadlock because it
drained events AFTER `stop()` instead of after
`zyre_destroy` (the pinned zyre unblocks the caller's recv only when
the pipe closes). Both drivers and their fixes are preserved.

JNI-side hardenings that the audit produced (all in the file now):
`create()` fully under `g_lock` with allocation-failure guards at the
`zactor_new`/`zyre_new` sites (NULL frees everything and returns −3),
fixed teardown order (node first, then the ZAP actor), bounded
announcements, no leaking `jstring_to_c` paths. The preserved reference
does NOT NULL-check `strdup` or `zcert_new_from`; that gap and the
optional hardening are documented in `scripts/ndk/CURVE-MESH.md`
section 4.2.1.

## 3. Production (release) build

- R8 keep rules for the JNI-bound `ZreNative`/`ZreService` (symbol-name
  binding from `libzrejni.so`) — `app/proguard-rules.pro`.
- `BuildConfig.ZRE_DEBUG_GOSSIP` declared blank in main, overridden in
  debug only — release compiles the same code without it.
- Release signing material (`release.keystore` + `keystore.properties`,
  both git-ignored) lives canonically in the app repo root.
- Verified artifact `app-release.apk` (final rebuild 2026-10-20,
  including the executor-serialized `ZreService`): BUILD SUCCESSFUL,
  R8-minified, classes.dex + `libzrejni.so` for arm64-v8a /
  armeabi-v7a / x86_64, **zero files under `assets/zre/`** (nothing
  bundled), V2-signed CN=VisionCam Health (SHA-256
  `8d18253d2138c4cb…`). `app-debug.apk` green alongside.
- The full pipeline is scripted in `scripts/ndk/Makefile`:
  `app-sync` (rsync, md5-verified transfer), `app-build` (detached
  gradle), `app-pull` — a fresh environment reproduces the release
  with one command per step (walkthrough:
  `scripts/ndk/GETTING-STARTED.md`).

## 4. Key-possession rules (enforced)

- The app NEVER generates its Curve identity. Generation is
  off-device (dashboard when it ships; operator tooling in
  `scripts/ndk/` until then): keys are pasted as text into the app's
  Settings sheet ("Mesh discovery keys") and stored in
  `zre_identity` prefs. `ZreService` reads that pair and refuses to
  run plain without it. The interim `generateKeyPair` JNI was removed
  from `ZreNative.kt` and `zre_jni.c`.
- Compromise response: paste a fresh pair (the settings save sends
  `ACTION_REKEY`); the service rebuilds the node with the replacement
  identity. Verified on the emulator: after a pasted rotation the hub
  observed a NEW peer identity (`ENTER` with changed `peer_id`) —
  plus `ENTER/JOIN/SHOUT zre-ready` handshake flow.

## 5. Emulator validations (emulator-5554 on the build host)

- Fresh install, no keys: service logs
  "no curve keys configured; refusing to run plain (paste the key pair
  in settings)" — the never-plain contract on device.
- Ghost-BOOT quirk + the launcher hook raced
  `startZre` within milliseconds: the second leg's recreate read a
  pump handle the first had not set (rc=−2 twice). Fixed by
  serializing ALL service actions on one executor thread
  ("zre-exec", the Kotlin half of the native one-pump-thread
  contract) — after: boot leg node up → recreate with gossip →
  `gossip connect rc=0` → node up, in ONE clean pass.
- Full app↔Python mesh on gossip: the emulator's NAT is one-way (it
  can dial out, nothing can dial in). Recipe proven in
  `scripts/ndk/README.md` § APP (zre_jni) on the emulator:
  `adb forward` (host dials device), an SSH `-L` bridge because the
  trial tool runs on a third machine, and
  `--assisted-connect` (the tool takes the app's key from the
  HELLO's `X-PUBLICKEY` header). Result `GOSSIP_PASS`: ENTER + JOIN +
  the app's one-shot SHOUT zre-ready, zero EVASIVE in the window.
  (Build note: the earlier `adb reverse` wiring was the wrong
  direction and never carried a frame — forward is the one that works.)
- Final-package re-check 2026-10-20 with the rebuilt APKs: `adb
  install -r` keeps the stored pair, then in ONE clean pass:
  `zre service start` → `gossip connect rc=0` →
  `zre node up uuid=C7ACBF16… name=sdk_gphone64_x86_64 group=CHAT
  gossip=true`. The install/update, relaunch, and node-up chain is
  now stable end to end.

## 6. Left as documented future work (not blockers)

- ZGS store hygiene: the Python gossip hub keeps tuples forever
  (TTL 0), so dead app/app-instance identities linger as idle
  "unknown" peers (SILENT/EVASIVE noise only). Record in
  `scripts/ndk/README.md`.
- Release-app keystore custody is operator-side single-machine today;
  multi-machine signing = rotate the entries.

## 7. Post-merge revalidation (2026-10-23; Curve era)

- App repo pulled upstream zoom/stream/boot-notification work
  (fast-forward). Conflicts — `MainActivity.kt` (imports),
  `BootReceiver.kt` (boot path), `values/strings.xml` (both sides add
  strings) — resolved keeping BOTH feature sets: upstream zoom/stream
  and the zre settings-paste node.
- Integration fix: upstream's boot-offer notification used id 2, the
  same id as `ZreService`'s foreground notification, so the service
  replaced the boot offer on start. Boot offer moved to id 3; after a
  reboot both are present (`id=2 channel=zre_service`,
  `id=3 channel=boot`) and `zre service start` →
  `node up ... gossip=false` in one pass.
- Key-staging fix: `scripts/ndk/make_identity_xml.py` now XML-escapes
  values — Z85 keys can contain `&`/`<`/`>`, which made
  SharedPreferences discard the file (service then logged
  "no curve keys configured"). Settings paste (SharedPreferences API)
  was never affected; only the emulator XML stand-in.
- Toolchain reproducibility: `scripts/ndk/zre_static_all.sh` now
  creates its dependency directory on a cleaned host and CHECKS OUT
  the pinned commits (libzmq `4649337…`, czmq `b669c7e…`, zyre
  `f716769…`) instead of building whatever HEAD a fresh clone lands
  on.
- Rebuild from the cleaned build host: all three ABIs re-cross-built
  (RC=0), app `BUILD SUCCESSFUL`, release APK re-verified — 0
  `assets/zre/` entries, `libzrejni.so` for three ABIs, V2-signed
  CN=VisionCam Health.
