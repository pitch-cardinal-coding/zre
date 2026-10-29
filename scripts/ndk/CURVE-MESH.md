# CURVE-MESH — Curve-secured ZRE on Android: design, evidence, removal, restoration

> **Status (2026-10-23): the mobile app runs PLAIN ZRE. Curve was removed.**
> Plain ZRE is unauthenticated and unencrypted: any host on the LAN can
> join, spoof peers, or read traffic. This document preserves how the
> Curve design worked, how it was verified, and how to restore it
> exactly. It is written to be the single pickup point for a human
> engineer or an AI agent.

Companion documents (same directory unless stated otherwise):
[`README.md`](README.md) — NDK toolchain build and phone trials;
[`GETTING-STARTED.md`](GETTING-STARTED.md) — fresh-machine walkthrough;
[`ANDROID_LIBZYRE.md`](ANDROID_LIBZYRE.md) — JNI design history;
[`valgrind-harness/README.md`](valgrind-harness/README.md) — leak-proof runbook;
[`curve-reference/README.md`](curve-reference/README.md) — the removed Curve code, preserved and re-verified;
`../../docs/SESSION-VALIDATION.md` — measured validation ledger (zre repo).

---

## 0. Read this first

### 0.1 Pickup in 60 seconds

| Question | Answer |
|---|---|
| Where is the mesh code? | `vision-cam-health/app/src/main/cpp/zre_jni.c` (native), `.../java/com/visioncamhealth/zyre/{ZreNative.kt,ZreService.kt}` (Kotlin) |
| Current mode? | PLAIN. No keys, no ZAP, no settings UI. |
| Was it ever Curve? | Yes — until 2026-10-23. Section 2 records the design; Section 4 restores it. |
| Why was Curve removed? | Mobile key handling was judged inconvenient; the user chose convenience over LAN security. |
| Can plain and Curve nodes interoperate? | No. Curve beacons are v3 (~54 B) vs plain v1 (~22 B); each side cannot discover the other. A fleet is either all-Curve or all-plain. |
| How do I verify the current build? | Section 3 (Mode A) — works on HEAD as-is. |
| How do I restore Curve? | Section 4 — checklist first, then the Curve runbook. |
| Where is the deleted Curve code? | `scripts/ndk/curve-reference/` — full files + Curve-era drivers, re-verified 2026-10-23. |
| What proves the C node does not leak? | Valgrind harness — Section 3.8 (plain) and Section 4.6 (Curve-era). |
| Where do the repositories come from? | `git@github.com:pitch-cardinal-coding/vision-cam-health.git` (branch `main`); the zre repo URL is in its root README. |

### 0.2 For a human

Read Sections 1 and 2 for context, then choose a mode:

- **Mode A (Section 3)** verifies the current working tree (plain,
  pending commit). It assumes the Section 3 setup (two machines, build
  host, signing material) is complete.
- **Mode B (Section 4)** restores Curve first, then runs the Curve
  verification. Do not mix modes: commands from one mode do not work in
  the other.

Stages state their prerequisites and expected outputs. The restoration
sub-steps in Section 4.2 are a checklist, not a command sequence. If an
output does not match, stop at that stage; the troubleshooting table
(Section 5) covers the known failures.

### 0.3 For an AI agent

Treat the repository as the source of truth and this document as the
map. Before changing anything:

1. Run the invariant checks in Section 1.3. If they fail, the tree is
   not in the documented state — report it, do not "fix" it blind.
2. Decide the mode with the operator; never silently switch plain↔Curve.
3. If you change `zre_jni.c`, re-run the valgrind harness, append a new
   row to the ledger (Section 6 — the only ledger; append-only) and
   update the current verdict in `valgrind-harness/README.md`. A stale
   md5 invalidates the leak claim.
4. Do not add key generation inside the app. In the Curve design keys
   are produced off-device and pasted; that invariant was explicit.
5. Do not commit or push without instruction.
6. When you learn something new, append to the evidence ledger
   (Section 6) with date, command and observed output — not prose.

---

## 1. Current state (working tree, plain mode; commit pending)

### 1.1 Code map

| File | Role in plain mode |
|---|---|
| `app/src/main/cpp/zre_jni.c` | One zyre node per process. `create(name, iface)`: no keys, no `zauth`, no `zcert`. Announces X-IP (always) and X-MAC (when the kernel allows). `gossipConnect` binds `0.0.0.0:19870` and advertises `tcp://<iface-ip>:19870`. Pump-thread contract unchanged. |
| `app/src/main/java/com/visioncamhealth/zyre/ZreNative.kt` | JNI surface: `create(name, iface)`, `gossipConnect`, `start`, `join`, `shout`, `whisper`, `nextEvent`, `signalStop`, `stop`, `uuid`. |
| `app/src/main/java/com/visioncamhealth/zyre/ZreService.kt` | Foreground service (`connectedDevice`), single executor `zre-exec`, one pump thread, `connectedDevice` FGS notification id 2. No prefs, no refusal, no `ACTION_REKEY`. Groups: `CHAT`. |
| `app/src/main/java/com/visioncamhealth/MainActivity.kt` | Starts `ZreService` on launch through the DEBUG-only hook when `BuildConfig.ZRE_DEBUG_GOSSIP` is non-empty (debug value: `tcp://10.0.89.21:19872`). No mesh-keys UI. |
| `app/src/main/java/com/visioncamhealth/receiver/BootReceiver.kt` | On boot starts `ZreService` (no gossip extra) and posts the upstream boot-offer notification (id 3; id 2 is the FGS). |
| `app/src/main/res/values/strings.xml` | No `mesh_*` strings; notification text says "plain ZRE". |

### 1.2 Runtime evidence (plain, 2026-10-23)

Debug install, no key material anywhere (`shared_prefs/zre_identity.xml`
deleted), launch:

```
I visioncamhealth-Zre: zre service start (bootElapsed=...)
I visioncamhealth-Zre: gossip connect rc=0 endpoint=tcp://10.0.89.21:19872
I visioncamhealth-Zre: zre node up uuid=<32 hex> name=<Build.MODEL> group=CHAT gossip=true
```

`gossip=true` because the debug APK carries the rendezvous endpoint.
A release build (no hook, boot start) logs `gossip=false`.

Plain trial (`phone_trial.py gossip --plain ... --assisted-connect ...`):

```
PY {'type': 'ENTER', ... 'peer_name': '<Build.MODEL>', 'address': 'tcp://10.0.2.16:19870'}
PY assisted dial 127.0.0.1:19870 pubkey=MISSING
PY {'type': 'JOIN', ...}
PY {'type': 'SHOUT', ..., 'payload': b'zre-ready'}
GOSSIP_PASS announce ip=10.0.2.16 mac=-
```

### 1.3 Invariant checks (run first)

```sh
cd ~/devcode/vision-cam-health
# plain JNI: the signature has exactly two string args
grep -n -A1 "ZreNative_create" app/src/main/cpp/zre_jni.c
# must show the two-string-arg signature:
#   Java_com_visioncamhealth_zyre_ZreNative_create(JNIEnv *env, jobject self,
#           jstring name, jstring iface)
# no Curve machinery anywhere in the app
! grep -rn "zauth\|zcert\|CURVE_ALLOW_ANY\|curve_pub\|ACTION_REKEY" app/src/main
# no key-generation surface
! grep -rn "generateKeyPair" app/src/main
```

If any check fails, the tree is not in the documented plain state.

---

## 2. How Curve worked (removed 2026-10-23)

### 2.1 Identity lifecycle

1. **Generation, off-device only.** `scripts/ndk/curve_keys.py`
   (`make curve-keys KEY_NAME=<name>`) creates `~/.avedge/<name>.priv`
   (0600) and `<name>.pub` — one bare 40-character Z85 line each. The
   avedge toolchain can manage the same files. `make curve-check`
   proves the public key derives from the private one (x25519).
2. **Handoff.** `make curve-release` stages `dist/curve/<name>.{priv,pub}`;
   a dashboard (planned) would serve the same two strings.
3. **Paste.** The operator entered both keys in the app's Settings
   sheet ("Mesh discovery keys"). The UI filtered input to the Z85
   alphabet and 40 characters (`filterZ85`/`Z85_CHARS`).
4. **Store.** `applyMeshIdentity()` wrote SharedPreferences
   `zre_identity` with `curve_pub` / `curve_sec`, then sent
   `ACTION_REKEY` so the running node rebuilt with the new pair.
5. **Read.** `ZreService.startZre()` read the two prefs; a missing or
   malformed pair logged `no curve keys configured; refusing to run
   plain (paste the key pair in settings)` and stopped the service.
   There was never a plain fallback.

### 2.2 JNI: Z85 → zcert + ZAP

The Curve-era `create(name, pubZ85, secZ85, iface)` did this under
`g_lock`. The complete file is preserved at
[`curve-reference/android/zre_jni.c`](curve-reference/android/zre_jni.c)
(md5 `8facaf7a…`, re-verified 2026-10-23); the core is reproduced below:

```c
zactor_t *auth = zactor_new(zauth, NULL);
if (!auth) { /* free strings */ return -3; }
zstr_sendx(auth, "CURVE", CURVE_ALLOW_ANY, NULL);
zsock_wait(auth);                       /* handshake with the auth actor */

zyre_t *node = zyre_new(cname);
zyre_set_interval(node, 1000);          /* 1 s cadence; 0 makes peers EVASIVE */

byte raw_pub[32], raw_sec[32];
if (!zmq_z85_decode(raw_pub, pub) || !zmq_z85_decode(raw_sec, sec)) {
    /* free strings, zyre_destroy(&node), zactor_destroy(&auth) */
    return -1;                          /* never fall back to plain */
}
zcert_t *cert = zcert_new_from(raw_pub, raw_sec);
zyre_set_zcert(node, cert);
zyre_set_zap_domain(node, "global");
zcert_destroy(&cert);
/* ... X-IP/X-MAC announcement, interface pinning, gossip endpoint ... */
g_node = node; g_auth = auth;
```

Teardown ran pump-thread-only: `zyre_stop`, `zyre_destroy`, then
`zactor_destroy(&g_auth)` — node first, ZAP actor last. The order was
proven by the trial matrix and the valgrind harness.

### 2.3 Discovery modes

- **Beacon (default):** UDP broadcast. Plain nodes emit the version
  0x01 beacon (22 bytes). Curve nodes emit the version 0x03 beacon
  (54 bytes): same header/base plus the 32-byte raw Curve public key
  appended. A plain node only parses 0x01 and cannot parse 0x03 (and
  vice versa), so **plain and Curve never see each other** — the
  downgrade-protection property. Z85 is only the text form used for
  keys and the HELLO `X-PUBLICKEY` header.
- **Gossip (used on the emulator):** the app connects to a ZGS hub
  (Python `zre.gossip`), which returns peer endpoints. The app still
  has to open the TCP leg; in the lab the emulator's one-way NAT
  required the assisted dial (Section 3.7).
- **ZAP:** every peer handshake carried `mechanism=CURVE`; the phone
  log showed `zauth: ZAP request mechanism=CURVE` and the request was
  allowed. `CURVE_ALLOW_ANY` accepted any valid Curve key; per-peer
  grants (a directory) were future work.

### 2.4 Threading and teardown contract (unchanged by the removal)

- `nextEvent()` blocks in native code on the pump thread.
- `stop()` is pump-thread-only and destroys the node.
- `signalStop()` is the only cross-thread trigger; it sends STOP, so the
  blocked `nextEvent()` returns a `{"type":"STOP"}` event. The pump loop
  ends because the service's `pumping` flag is false, then the pump
  calls `stop()`.
- A `"{}"` result means no node exists (already destroyed). A
  single-threaded caller must therefore `stop()` before draining: the
  pinned zyre's recv stays blocked on `zyre_stop` alone (STOP arrives
  as an event; the pipe closes on destroy).
- All service control decisions are serialized on one executor thread
  (`zre-exec`); the recreate path waits on the old pump
  (`oldPump?.join(5000)`) before `create`.

### 2.5 Rotation (Curve era)

Replacing the stored pair and rebuilding produced a new identity. The
hub observed an `ENTER` with a changed `peer_id` from the same app.
`phone2` was generated with `make curve-keys KEY_NAME=phone2`.

### 2.6 Evidence summary (Curve era; the only ledger is Section 6)

| Date | Item | Result |
|---|---|---|
| 2026-10-20 | Beacon discovery phone↔PC | `BEACON_PASS` (v3 beacons, ENTER/JOIN/SHOUT, `announce ip=...`) |
| 2026-10-20 | Curve mechanism | `CURVE_PASS` (`ZAP request mechanism=CURVE` allowed; secured whisper/shout) |
| 2026-10-20 | Gossip through ZGS | `GOSSIP_PASS` (hub `tcp://*:19872`) |
| 2026-10-20 | Direct dial | `DIAL_PASS` only with explicit `--endpoint`; a bare `--beacon-peer-port` does not bind the inbox in this zyre build |
| 2026-10-20 | Rotation | Hub saw a new peer identity (changed `peer_id`) after a pair replacement |
| 2026-10-20 | Valgrind, Curve file `8facaf7a…` | Both drivers: no leaks, 0 errors; probe unblock ~2000 ms |
| 2026-10-23 | Valgrind, plain file `8abdd680…` | Both drivers: no leaks, 0 errors; probe unblock 2067.9 ms |

---

## 3. Mode A — verify the current plain build

### 3.1 Machine roles and overrides

| Role | Needs | Lab default (override!) |
|---|---|---|
| Workstation (**this PC**) | git, Python 3.10+, `sshpass`, `rsync`; **no Android SDK** | `~/devcode/zre`, `~/devcode/vision-cam-health` |
| Build host (**the other PC**) | Android SDK 37 / build-tools 37.0.0 / NDK 30.0.16248370 / CMake 4.1.2, JDK 17+, adb, emulator, `rsync` | `iam@10.0.89.26` |
| Device | emulator or phone | `emulator-5554` |
| Valgrind host | valgrind + pinned libzyre/czmq/zmq/sodium under `~/.local` | any Linux host |

The Makefile defaults are the lab's. Override on every command or via
environment:

```sh
BUILD_HOST=user@yourhost BUILD_PASSWORD=...        # push/build/app-*
PHONE_ADB=<adb-serial> PHONE_IP=<phone-ip> HOST_IP=<workstation-ip>
EMU=emulator-5554 APP_DIR=~/devcode/vision-cam-health
```

Repositories: `~/devcode/zre` (this toolchain) and
`~/devcode/vision-cam-health`
(`git@github.com:pitch-cardinal-coding/vision-cam-health.git`, branch
`main`). The workstation copy of the app repo is the source for
`make app-sync`.

Delivery note: ALL of this work is uncommitted — the app changes, the
Makefile/scripts, this document, `curve-reference/`, and
`valgrind-harness/`. Commit (or archive) everything before a handoff; a
fresh clone cannot build or restore anything without it.

In this lab Android compilation, Gradle, adb and the emulator run ONLY
on the build host. The workstation runs Python, the trials and the
documentation; it reaches the Android side exclusively through the
Makefile's SSH targets. (`sshpass` and `rsync` on the workstation drive
those targets; the build host also needs `/usr/bin/rsync` for
`app-sync`.)

### 3.2 Workstation setup (this PC)

```sh
cd ~/devcode/zre
python3 -m venv ~/devcode/.env/zre
~/devcode/.env/zre/bin/pip install -e ".[secure,dev]"
make style && make lint && make test     # 105 passed in ~2 min
```

Password-auth hosts: create the git-ignored
`scripts/ndk/lab.env` with `BUILD_PASSWORD=...` (mode 0600), or export
`BUILD_PASSWORD`, or use SSH keys. The Makefile ships no password.

Use the venv interpreter for every manual script below:

```sh
PY=~/devcode/.env/zre/bin/python3
```

### 3.3 Build host provisioning (the other PC)

```sh
sudo apt install -y git build-essential autoconf automake libtool \
    pkg-config rsync openjdk-17-jdk
# Android cmdline-tools (sdkmanager) must be installed first, e.g.:
#   https://developer.android.com/studio#command-line-tools-only
SDK=$HOME/Android/Sdk
yes | $SDK/cmdline-tools/latest/bin/sdkmanager --licenses
$SDK/cmdline-tools/latest/bin/sdkmanager "platform-tools" "emulator" \
    "ndk;30.0.16248370" "platforms;android-37" "build-tools;37.0.0" \
    "cmake;4.1.2"
export ANDROID_HOME=$SDK
```

The app repo on the build host needs `local.properties`
(`sdk.dir=/home/<user>/Android/Sdk`); `make app-sync` deliberately does
not copy the workstation's file. `make app-build` exports
`ZRE_ANDROID_PREFIX=$HOME/zre-android-build/prefix` for the Gradle
process, so no login-profile entry is needed; the Gradle default
(`/home/iam/...`) only matters if you build Gradle by hand on a host
whose home is not `/home/iam`.

### 3.4 Cross-build the static stack (commands on the workstation; they run the build on the other PC)

```sh
cd ~/devcode/zre/scripts/ndk
make curve-keys KEY_NAME=phone1     # make push runs curve-check first
make push                           # sources + scripts to the build host
make build                          # detached; repeat `make log` until the ABI listing
make verify                         # readelf NEEDED + artifact listing
make pull                           # dist/zc_peer-<abi>
```

`make push` fails without a valid `phone1` pair because of its
`curve-check` prerequisite, even though the app no longer uses keys
(the PC/`zc_peer` trials still do).

### 3.5 Build the app

`keystore.properties` is a Java-properties file: Gradle reads LITERAL
values, so paste the password itself — `$STORE_PASS` would be read as
the literal string.

On the build host (the only machine with the Android SDK in this lab):

```sh
cd ~/devcode/vision-cam-health
export STORE_PASS=<strong> KEY_PASS=<strong>
make keystore            # only if release.keystore does not exist yet
# create keystore.properties (git-ignored) with:
#   storeFile=../release.keystore
#   storePassword=<the value of STORE_PASS>
#   keyAlias=visioncamhealth
#   keyPassword=<the value of KEY_PASS>
make debug && make release
```

(If a third machine has Android Studio, the same commands work there.)

`make keystore` generates a certificate with
`CN=Jude Etuka, O=Pitch Cardinal Coding Limited` (app Makefile). The
lab's canonical release key was created separately with
`CN=VisionCam Health`. The CN is whatever key you sign with; the build
does not care.

Lab flow (no SDK on the workstation):

```sh
cd ~/devcode/zre/scripts/ndk
make app-sync            # SYNC_KEYFILES=0 withholds the signing files;
                         # use 0 only for debug-only builds (release then fails)
make app-build           # assembleDebug + assembleRelease, detached
make app-wait            # blocks until BUILD SUCCESSFUL / BUILD FAILED
make app-log             # exits non-zero unless the log says BUILD SUCCESSFUL
make app-pull            # dist/app/app-{debug,release}.apk
```

Expected: `BUILD SUCCESSFUL`; release APK R8-minified, `libzrejni.so`
for arm64-v8a / armeabi-v7a / x86_64, zero `assets/zre/` entries. Check:

On the build host (the SDK lives there; use the host-side APK path):

```sh
APP=$HOME/devcode/vision-cam-health/app/build/outputs/apk/release/app-release.apk
unzip -l "$APP" | grep -c assets/zre/        # must print 0
unzip -l "$APP" | grep libzrejni.so          # three ABIs
$HOME/Android/Sdk/build-tools/37.0.0/apksigner verify -v --print-certs "$APP" | head -4
```

`make app-pull` copies the same APKs to the workstation's `dist/app/`
for inspection; the workstation has no SDK tooling.

`make app-sync` uses `rsync --delete` but protects
`release.keystore`/`keystore.properties` on the build host
(`--filter='P ...'`), so a host-only signing file is never deleted by a
sync from a workstation that lacks it. `SYNC_KEYFILES=0` withholds the
files from the transfer entirely (debug-only builds; a release build
then fails).

### 3.6 Run on the emulator

On the build host (boot one if none runs):

```sh
ssh <build-host>
~/Android/Sdk/emulator/emulator -list-avds
~/Android/Sdk/emulator/emulator -avd <name> -no-boot-anim &
# create one if absent:
# sdkmanager "system-images;android-34;google_apis;x86_64"
# avdmanager create avd -n zre_test -k "system-images;android-34;google_apis;x86_64"
```

From the workstation (the Makefile runs adb on the build host; direct
adb commands below use `$HOME/Android/Sdk/platform-tools/adb` there):

```sh
cd ~/devcode/zre/scripts/ndk
make emu-install                  # debug APK (runs adb on the build host)
make emu-launch
sleep 8 && make emu-logcat        # expect service start + gossip connect + node up
```

Expected (debug, hook active):

```
I visioncamhealth-Zre: zre service start (bootElapsed=...)
I visioncamhealth-Zre: gossip connect rc=0 endpoint=tcp://10.0.89.21:19872
I visioncamhealth-Zre: zre node up uuid=... name=... group=CHAT gossip=true
```

`ENTER` events and `greeted the mesh (zre-ready)` appear only during a
trial with a peer — not on a bare start. A release APK or a boot start
logs `gossip=false`.

### 3.7 Plain mesh trial

Two terminals. Terminal A (workstation): start the trial first — it
hosts the ZGS hub (it prints only when events arrive). Terminal B
(workstation): wait 1-2 seconds for the hub to bind, then launch the
app so it connects to a live hub.

```sh
# Terminal A — workstation
cd ~/devcode/zre
$PY scripts/ndk/phone_trial.py gossip --plain --hub-port 19872 \
    --peer-name <app-Build.MODEL> --wait 90

# Terminal B — workstation (1-2 s after Terminal A starts)
cd ~/devcode/zre/scripts/ndk && make emu-launch
```

The peer name matters: `--peer-name` filters PASS logic (default
`phone1`); the app names its node from `Build.MODEL` — read it from the
`node up ... name=` line.

Emulator NAT is one-way (the app dials out; the PC cannot dial in).
For the full ENTER/JOIN/SHOUT sequence:

```sh
# build host
adb -s emulator-5554 forward tcp:19870 tcp:19870
# workstation (keep open)
ssh -N -L 19870:127.0.0.1:19870 <build-host>
# workstation
$PY scripts/ndk/phone_trial.py gossip --plain --hub-port 19872 \
    --peer-name <app-Build.MODEL> \
    --assisted-connect tcp://127.0.0.1:19870 --wait 90
```

Use `forward`, not `reverse`: the forward direction lets the host dial
the device. Expected: `pubkey=MISSING` (plain dial), then
`GOSSIP_PASS ... announce ip=10.0.2.16`.

### 3.8 Leak proof (current file)

Follow `valgrind-harness/README.md`. Summary:

```sh
# stage zre_jni.c, probe_env.c, test_zrejni.c, t_probe.c, vg_run.sh,
# vg_probe.sh, and jni.h/jni_md.h (headers go to ~/vgcheck/jni-include)
# then on the valgrind host:
bash ~/vgcheck/vg_run.sh      # FIRST: compiles probe_env, which generates fake_env_idx.h
bash ~/vgcheck/vg_probe.sh
```

`vg_run.sh` writes `vg.out`; `vg_probe.sh` writes `vg_probe.out`. Both
must contain:

```
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

The probe must also print `nextEvent returned after ... ms (need < 5000)`.
Recorded verdict for `zre_jni.c` md5
`8abdd680e4a9f95a1fe653706af6c900`: probe unblock 2067.9 ms, no leaks,
0 errors. Re-run after ANY change to `zre_jni.c`; update the md5.

---

## 4. Mode B — restore Curve

### 4.1 Decision gate

A Curve node cannot mesh with plain peers. Restoring Curve is a fleet
decision, not a local toggle. Confirm with the operator before starting.

### 4.2 Restoration checklist (exact touch points)

**4.2.1 `app/src/main/cpp/zre_jni.c`** — copy
[`curve-reference/android/zre_jni.c`](curve-reference/android/zre_jni.c)
over the current file. That file IS the exact Curve implementation.
Items 1-7 below are a verification checklist — the reference already
contains them, so do not apply them twice. Item 8 is optional hardening
that the reference does not contain (the recorded leak verdict covers
the reference as-is).

1. Add `static zactor_t *g_auth = NULL;` next to `g_node`; include the
   ZAP teardown in `destroy_locked()` (`zactor_destroy(&g_auth)` after
   the node).
2. Change the exported function to
   `create(JNIEnv *env, jobject self, jstring name, jstring pubZ85, jstring secZ85, jstring iface)`.
   The JNI symbol name does not change (no overload).
3. Copy and free both key strings on every exit path. **Unlock
   `g_lock` on every return path.**
4. Before `zyre_new`: create the `zauth` actor, send
   `"CURVE", CURVE_ALLOW_ANY`, `zsock_wait`. On failure free strings,
   unlock, return `-3`.
5. After `zyre_new`/`zyre_set_interval`: `zmq_z85_decode` both keys;
   on failure free strings, `zyre_destroy(&node)`,
   `zactor_destroy(&auth)`, unlock, return `-1`.
6. `zcert_new_from`, `zyre_set_zcert`, `zyre_set_zap_domain(node, "global")`,
   `zcert_destroy`; store `g_auth = auth`; unlock; return the handle.
7. Keep the announcement, interface-pinning and `gossipConnect` code
   untouched.
8. Optional hardening: check `strdup` results and `zcert_new_from` for
   NULL, and return `-3` after freeing the other strings. Not present in
   the reference; the valgrind verdict does not cover this change.

**4.2.2 `ZreNative.kt`** — `external fun create(name: String, pubZ85: String, secZ85: String, iface: String?): Long`.

**4.2.3 `ZreService.kt`**

- Restore the prefs read (`PREFS_ID = "zre_identity"`, `curve_pub`,
  `curve_sec`), the 40-char check, the refusal log
  (`no curve keys configured; refusing to run plain ...`) and
  `stopSelf()`.
- Restore `ACTION_REKEY = "com.visioncamhealth.action.ZRE_REKEY"` and
  its branch. **It must mirror the gossip recreate path**: capture
  `pumpThread`, `stopZreNode()`, `oldPump?.join(5000)`,
  `stopping = false`, then `startZre(intent)`. Without the join, `create`
  returns `-2` while the old node still exists and the identity never
  changes; without the `stopping` reset the new node is torn down.
- Two `create` call sites: initial and recreate.

**4.2.4 `MainActivity.kt`** — take the Curve UI from
[`curve-reference/android/MainActivity.kt`](curve-reference/android/MainActivity.kt):
state `meshPub`/`meshPriv`/`meshPrivReveal`, `filterZ85`/`Z85_CHARS`,
`applyMeshIdentity`, the two `OutlinedTextField`s and status text, plus
the imports `PasswordVisualTransformation` and
`VisualTransformation`. Also restore the six `SettingsSheetOverlay`
parameters (`meshPub`, `meshPriv`, `meshPrivReveal`, `onMeshPubChange`,
`onMeshPrivChange`, `onMeshPrivRevealToggle`), their call-site
arguments, and replace the plain-mode placeholder comment at the old
section site. That snapshot predates the upstream zoom merge: extract
these sections into the CURRENT file; do not overwrite it.

**4.2.5 `strings.xml`** — restore the nine `mesh_*` strings
(`mesh_header`, `mesh_hint`, `mesh_pub_label`, `mesh_priv_label`,
`mesh_char_count`, `mesh_show`, `mesh_hide`, `mesh_status_ready`,
`mesh_status_wait`). Also change
`zre_notification_channel_description` from "(plain ZRE)" back to
"(Curve-secured ZRE)".

**4.2.6 Harness drivers** — use
[`curve-reference/valgrind/`](curve-reference/valgrind/) as the source;
three call sites (`test_zrejni.c` two,
`t_probe.c` one) become
`create(env, NULL, name, pub, sec, NULL)`, with
`const char *pub = getenv("VG_PUB"); const char *sec = getenv("VG_SEC");`
plus a NULL/length check in each driver. Re-add
`export VG_PUB=$(head -c40 ~/vgcheck/device.pub)` and
`export VG_SEC=$(head -c40 ~/vgcheck/device.priv)` to both vg scripts.
The reference `vg_run.sh`/`vg_probe.sh` already export `VG_PUB` and
`VG_SEC`; if you patch the current drivers instead, add those exports
yourself. Stage a pair: `make curve-keys KEY_NAME=device` then copy
`~/.avedge/device.{pub,priv}` to `~/vgcheck/` on the valgrind host
(`scp` or `$VMSSH "cat > ..."`).

### 4.3 Build and verify after restoration

On the workstation (this PC):

```sh
cd ~/devcode/zre/scripts/ndk
make app-sync && make app-build && make app-wait && make app-log && make app-pull
make emu-install
make emu-launch
sleep 8 && make emu-logcat
```

`app-wait` blocks until the detached build prints SUCCESS/FAILED, with a
5-minute cap; a first build can exceed that. If it prints
`APP BUILD TIMEOUT`, that is not a build failure — re-run `make app-wait`
or poll the log; do not restart the build.

Fresh install with no pair must log the refusal line. Then stage a pair
(Settings paste, or the emulator stand-in):

```sh
make emu-keys-stage KEY_NAME=phone1
sleep 8 && make emu-logcat     # expect gossip connect rc=0 ... node up ...
```

For a truly fresh install, uninstall first on the build host
(`adb -s <dev> uninstall com.visioncamhealth`); `adb install -r` keeps
old prefs, so the refusal may not appear.

### 4.4 Curve trials

Push the peer binary to the phone once — run these ON THE BUILD HOST
(the phone's adb lives there). ABI: `armeabi-v7a` for 32-bit phones,
`arm64-v8a` for 64-bit phones, `x86_64` for emulators:

```sh
adb -s <PHONE_ADB> push ~/zc-android/zc_peer-armeabi-v7a /data/local/tmp/zc_peer
adb -s <PHONE_ADB> shell chmod 755 /data/local/tmp/zc_peer
```

Then, with `phone1` and `avedge` pairs present:

```sh
make trial-beacon    # BEACON_PASS
make trial-gossip    # GOSSIP_PASS (hub tcp://*:19872)
make trial-dial      # DIAL_PASS (needs an explicit --endpoint)
```

Manual forms (phone duration is 120 s). Launch the phone peer first
(`make launch-phone ARGS_FILE=args.txt`, written by
`phone_trial.py build-args --trial-mode <mode> --avedge phone1`), then
on the workstation:

```sh
$PY scripts/ndk/phone_trial.py beacon --avedge --wait 40
$PY scripts/ndk/phone_trial.py gossip --avedge --hub-port 19872 \
    --peer-name <app-Build.MODEL> --wait 40
$PY scripts/ndk/phone_trial.py dial --avedge --phone-ip <phone-ip>
```

`make trial-dial` fails unless the phone peer was launched with an
explicit `--endpoint` (a bare `--beacon-peer-port` does not bind the
inbox in this zyre build); that is a `zc_peer` flag, not a
`phone_trial.py` flag.

For the emulator, use the same assisted recipe as Section 3.7 but with
`--avedge` (not `--plain`) and expect `pubkey=provided` (the tool takes
the peer's key from the `X-PUBLICKEY` HELLO header).

### 4.5 Rotation after restoration

On the workstation:

```sh
make curve-keys KEY_NAME=phone2
```

In Terminal A, start a trial so a hub and peer are present (Curve mode):
`make trial-gossip`. In Terminal B, rotate and watch the hub log:

```sh
make emu-keys-stage KEY_NAME=phone2
```

The hub shows an `ENTER` with a changed `peer_id` from the same app.
The published key pair is the identity; there is no on-device
generation.

### 4.6 Valgrind after restoration

Stage `zre_jni.c`, `probe_env.c`, the drivers, `vg_run.sh`,
`vg_probe.sh`, headers (`~/vgcheck/jni-include/`) and the two
`device.*` key files. Run `vg_run.sh` first (it compiles `probe_env`,
which generates `fake_env_idx.h`), then `vg_probe.sh`.
Both must be leak-free and error-free; record the new md5. The Curve-era
verdict was clean at md5 `8facaf7a141ff5fe588a9c20b87d4108` (probe
unblock ~2000 ms on 2026-10-20; the re-run of the preserved file on
2026-10-23 measured 2084.4 ms).

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no curve keys configured; refusing to run plain` | Curve build with no/invalid prefs | paste a valid pair; this line does not exist in the plain build |
| Plain app + `--avedge` trial sees nothing | mixed modes | use `--plain`; Curve peers cannot mesh with plain |
| Curve app + `--plain` trial sees nothing | mixed modes | use `--avedge` or explicit keys |
| Emulator trial sees app then `EVASIVE`/`EXIT` | one-way NAT; PC cannot dial in | `adb forward tcp:19870 tcp:19870` + `ssh -N -L ...` + `--assisted-connect` |
| `adb forward` tunnel carries nothing | used `reverse` | use `forward` (host dials device) |
| App events missing in trial output | `--peer-name` default `phone1` filters the app | pass `--peer-name <Build.MODEL>` |
| `make push` fails at `curve-check` | no valid `phone1` pair | `make curve-keys KEY_NAME=phone1` |
| `make app-build` fails: missing `libzyre.a` | static prefix absent on the build host | rebuild with `make build`; check `ZRE_ANDROID_PREFIX` |
| Gradle fails on a non-`iam` host | default prefix `/home/iam/...` | `export ZRE_ANDROID_PREFIX=$HOME/zre-android-build/prefix` |
| `rsync` remote command not found | fish login shell | `--rsync-path=/usr/bin/rsync` (app-sync already does) |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | debug keystore changed | uninstall then install; app prefs are lost |
| SharedPreferences ignore the key XML **(Curve restoration only)** | Z85 contains `&`/`<`/`>` unescaped | use `make_identity_xml.py` (escapes) or the settings UI |
| Hub shows idle peers/SILENT/EVASIVE noise | ZGS tuples never expire (TTL 0) | restart the hub |
| `examples/gossip_mesh.py join` → `ZMQError: Invalid argument` | `--hub` without scheme | `--hub tcp://host:port` |
| Valgrind probe cannot find `fake_env_idx.h` | `vg_probe.sh` run before `vg_run.sh` | run `vg_run.sh` first |
| `apksigner` shows no scheme line | missing `-v` | `apksigner verify -v --print-certs` |

---

## 6. Evidence ledger (append-only)

Rules: one row per measurement; include the command and the observed
result; state the file md5 when the claim is about `zre_jni.c`. Never
edit an old row; add a new one.

| Date | Item | Command (abridged) | Result |
|---|---|---|---|
| 2026-10-20 | Beacon, phone↔PC | `make trial-beacon` | `BEACON_PASS`, v3 beacons, `announce ip=...` |
| 2026-10-20 | Curve mechanism | `make trial-beacon` (curve log) | `CURVE_PASS`, ZAP `mechanism=CURVE` allowed |
| 2026-10-20 | Gossip | `make trial-gossip` | `GOSSIP_PASS` |
| 2026-10-20 | Dial | `make trial-dial` | `DIAL_PASS` with `--endpoint` |
| 2026-10-20 | Rotation | paste new pair | hub saw changed `peer_id` |
| 2026-10-20 | Valgrind Curve file `8facaf7a…` | `vg_run.sh`, `vg_probe.sh` | no leaks, 0 errors, probe ~2000 ms |
| 2026-10-23 | Plain node up, no keys | `make emu-install && make emu-launch` | `node up ... gossip=true` |
| 2026-10-23 | Plain trial | `phone_trial.py gossip --plain ... --assisted-connect` | `GOSSIP_PASS`, `pubkey=MISSING` |
| 2026-10-23 | Curve reference re-verified (`8facaf7a…`) | `vg_run.sh`, `vg_probe.sh` on `curve-reference/` | no leaks, 0 errors, probe 2084.4 ms |
| 2026-10-23 | Valgrind plain file `8abdd680…` | `vg_run.sh`, `vg_probe.sh` | no leaks, 0 errors, probe 2067.9 ms |
| 2026-10-23 | Release artifact | `app-pull` + `apksigner verify -v` | 0 `assets/zre`, 3 ABIs, V2 `CN=VisionCam Health` |

MD5 provenance: `8facaf7a141ff5fe588a9c20b87d4108` is the Curve file
preserved in `curve-reference/`; `8abdd680e4a9f95a1fe653706af6c900`
is the plain file at the time of writing. Any edit changes the md5;
recompute and re-run the harness.

---

## 7. AI-agent handoff protocol

**Do**

- Read Sections 1.3 and 6 first and run the invariant checks.
- Ask which mode the operator wants (plain or Curve) before building.
- Use the venv Python (`~/devcode/.env/zre/bin/python3`) for scripts.
- Re-run `make style`, `make lint`, `make test` after Python changes.
- Re-run the valgrind harness after `zre_jni.c` changes; update the md5
  and ledger.
- Keep the app free of key generation, always.
- Prefer `make` targets; they encode the fish-shell, rsync and SSH
  quirks.

**Do not**

- Commit or push without explicit instruction.
- Commit `scripts/ndk/args.txt`, `scripts/ndk/lab.env`, any `*.priv`,
  keystore, or password. Run `git status` before any commit and check
  every untracked file.
- Edit `curve-reference/` files: they are the preserved evidence. Add a
  new copy or a note instead.
- Append to Section 2.6 or rewrite old ledger rows; Section 6 is the
  only ledger and it is append-only.
- Mix plain and Curve peers in one trial and call it a failure of the
  code.
- Trust stale md5s or copy evidence from old logs into new rows.
- Use `adb reverse` for the emulator dial; it is the wrong direction.
- Change the group (`CHAT`) without changing the trial tools.

**File map for restoration**

| Purpose | File |
|---|---|
| Native node | `vision-cam-health/app/src/main/cpp/zre_jni.c` |
| Kotlin JNI surface | `.../zyre/ZreNative.kt` |
| Service | `.../zyre/ZreService.kt` |
| Settings UI | `.../MainActivity.kt` + `res/values/strings.xml` |
| Boot start | `.../receiver/BootReceiver.kt` |
| Key tooling | `zre/scripts/ndk/curve_keys.py`, `make_identity_xml.py`, `zre_feed.sh` |
| Trials | `zre/scripts/ndk/phone_trial.py` |
| Leak proof | `zre/scripts/ndk/valgrind-harness/` |
| Removed Curve code | `zre/scripts/ndk/curve-reference/` (Curve-era files; MainActivity is a pre-merge snapshot) |
| Build/run | `zre/scripts/ndk/{Makefile,README.md,GETTING-STARTED.md}` |

**Decision log**

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-20 | Curve always on, keys off-device, settings paste, no in-app generation | Security: no plaintext mesh; no device-side key custody |
| 2026-10-23 | Remove Curve from the app; run plain | Operator requested convenience; the LAN security tradeoff was accepted explicitly |
| 2026-10-23 | Keep Curve in PC tooling (`zc_peer`, `phone_trial.py --avedge`) | Trials and the Python side still verify the secured path |
