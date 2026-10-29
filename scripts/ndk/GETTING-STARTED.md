# Getting started — build, run, and deploy the zre mesh (fresh environment)

A novice-friendly, stage-by-stage walkthrough. Every stage lists the
commands, what the output must look like, and where the details live.
The docs assume a FRESH development environment: nothing pre-exists,
and every stage tells you how to create what it needs.

Machine roles (the lab names are examples — override everything with
Make variables or your own addresses):

| Role        | Lab example          | Needs                                    |
|-------------|----------------------|------------------------------------------|
| Workstation | this PC              | git, Python 3.10+ — runs tests, trials, docs |
| Build host  | `iam@10.0.89.26`     | Android SDK + NDK, JDK 17+, adb/emulator |
| Device      | emulator, or a phone | used for install + mesh trials           |
| Valgrind VM | any Linux host       | valgrind + the pinned zyre stack         |

Two repositories:

| Repo                   | What it is                                       |
|------------------------|--------------------------------------------------|
| `zre/` (this repo)     | Pure-Python ZRE library + `scripts/ndk/` toolchain + docs |
| `vision-cam-health/`   | The Android app (the zre node in `app/src/main/cpp/zre_jni.c`) |

---

## Stage 1 — Workstation: Python environment

```sh
git clone <your-fork-url> ~/devcode/zre
cd ~/devcode/zre
python3 -m venv ~/devcode/.env/zre
~/devcode/.env/zre/bin/pip install -e ".[secure,dev]"
```

Quality gates (all three must pass before anything else):

```sh
make style      # ruff format check + lint      -> clean
make lint       # ruff lint                     -> All checks passed!
make test       # full suite                    -> 105 passed
```

`make test` takes ~2 minutes. The `PYTHON ?=` line in the Makefile
points at `~/devcode/.env/zre/bin/python3`; use `make PYTHON=...` if
your venv lives elsewhere.

### Try the mesh without Android (sanity check)

Verified command shapes (from `examples/gossip_mesh.py`):

```sh
PY=~/devcode/.env/zre/bin/python3    # or your venv path

# Terminal A — the gossip hub
$PY examples/gossip_mesh.py hub hub1 --hub-port 19872

# Terminal B and C — two peers joining the hub
$PY examples/gossip_mesh.py join alice --hub tcp://127.0.0.1:19872 --send hello-from-alice --wait 5
$PY examples/gossip_mesh.py join bob   --hub tcp://127.0.0.1:19872 --send hello-from-bob   --wait 5
```

Expected on each peer:

```
[alice] READY inbox=<port> uuid=<hex> (beacons off, gossip only)
[alice] ENTER hub1 tcp://<ip>:<port>
[alice] SHOUT 'hello-from-alice'
[alice] JOIN hub1
```

`--hub` must include the `tcp://` scheme even though the CLI help text
says HOST:PORT — the endpoint string is passed to ZeroMQ as-is.

---

## Stage 2 — Build host: one-time onboarding

The build host needs: git, build tools, JDK 17+, Android SDK
(platform-tools + emulator + cmake) and NDK r30
(`30.0.16248370`). All lab values below are the versions verified for
this project; newer NDKs are UNTESTED.

From the workstation, the Makefile automates access:

Workstation prerequisites for the SSH-driven targets: `sshpass`
(`sudo apt install sshpass`) and `rsync`. The build host needs neither
for you to log in, but `rsync` is what `make app-sync` uses.

```sh
cd ~/devcode/zre/scripts/ndk
make curve-keys KEY_NAME=phone1          # push runs curve-check first
make push BUILD_HOST=user@yourhost      # override — the default is the lab host
```

- Password auth uses `sshpass`; `BUILD_PASSWORD` mirrors it. Prefer
  passwordless SSH: put your key on the host and the same commands
  keep working (sshpass still fine).
- Every remote payload runs under `bash -lc` because a login shell on
  the host may be fish (it breaks POSIX multi-line recipes). Keep that
  wrapper for new targets.

On the build host itself, the verified stack:

```sh
sudo apt install -y git build-essential autoconf automake libtool pkg-config
# Android cmdline-tools -> sdkmanager, then:
sdkmanager "platform-tools" "emulator" "ndk;30.0.16248370"
sdkmanager "platforms;android-37" "build-tools;37.0.0"
sdkmanager "cmake;4.1.2"              # check what your SDK offers here
export ANDROID_HOME=$HOME/Android/Sdk # the app's gradle picks this up
```

The app repo also needs `local.properties` on that machine (one line:
`sdk.dir=/home/<you>/Android/Sdk`) — `make app-sync` deliberately does
NOT copy the workstation's `local.properties`, machines differ.

The app's CMake reads the static prefix from `ZRE_ANDROID_PREFIX` and
defaults to `/home/iam/zre-android-build/prefix`. On any host whose
user is not `iam`, export it before the Gradle build (the Makefile's
`app-build` target inherits the SSH login environment):

```sh
export ZRE_ANDROID_PREFIX=$HOME/zre-android-build/prefix
```

Exact toolchain pins the app requires: compileSdk 37, build-tools
37.0.0, NDK 30.0.16248370, CMake 4.1.2 (`app/build.gradle.kts`).

---

## Stage 3 — Static libzyre for Android (cross-build)

From `~/devcode/zre/scripts/ndk` on the workstation:

```sh
make push      # sends zc_peer.c + zre_static_all.sh to the build host
make build     # detached; watches via make log
make log       # ... until you see the final ABIs listing
make verify    # readelf NEEDED check + artifact listing
make pull      # copies zc_peer-<abi> binaries into dist/
```

Expected layout on the host (details in `README.md`, "What you get"):
`~/zre-android-build/prefix/<abi>/lib/lib{sodium,zmq,czmq,zyre}.a` and
`~/zc-android/zc_peer-arm64-v8a` etc. The static zyre stack is what
the app's `app/src/main/cpp` CMake consumes; `zc_peer` is the
phone-test peer used in Stage 7.

Timebox: the full cross-build takes a while on first run (~10–15 min).
If it stops early, `make log` shows the last step; fix and rerun
(rebuilds are incremental per stage in `zre_static_all.sh`).

---

## Stage 4 — Curve tooling (PC side; the app is plain since 2026-10-23)

**Current app state: PLAIN ZRE — no keys, no ZAP, nothing to paste.**
Plain mode is unauthenticated and unencrypted on the LAN. The mobile
Curve design (off-device keys, settings paste, rotation) with full
evidence and a restoration checklist is archived in
`~/devcode/zre/scripts/ndk/CURVE-MESH.md`.

Curve remains the DEFAULT for the PC tools (`zc_peer`, and
`phone_trial.py` unless `--plain` is given), so the key commands below
still matter for the PC↔phone trials:

```sh
cd ~/devcode/zre/scripts/ndk
make curve-keys  KEY_NAME=phone1   # ensure ~/.avedge/phone1.{priv,pub}
make curve-check KEY_NAME=phone1   # 40-char Z85 + pub derives from priv (x25519)
make curve-release KEY_NAME=phone1 # stage dist/curve/phone1.{priv,pub} (operator handoff)
make keys-xml    KEY_NAME=phone1   # prefs XML snapshot (archived Curve flow / fixtures)
make curve-keys  KEY_NAME=avedge   # the PC-side trial pair (`--avedge`, Stage 7)
```

Rules that apply to the pair:

- Format: `~/.avedge/<name>.priv` (0600, never commit) and
  `<name>.pub` — one bare 40-char Z85 line each. The same files the
  avedge operator toolchain manages, so both projects stay
  byte-compatible.
- Phone trials with `zc_peer` use `phone1`; the PC trial node uses
  `avedge` (`phone_trial.py --avedge` loads `~/.avedge/avedge.*`).
- Curve rotation (Curve mode only): regenerate, grant the new `.pub`
  to peers, and replace the identity on every Curve node — for the
  archived mobile flow that meant re-pasting the pair (see
  `scripts/ndk/CURVE-MESH.md`).

---

## Stage 5 — Build the Android app

`cd ~/devcode/vision-cam-health`. Two flows; use the one that matches
your machine.

### Flow A — local machine WITH Android Studio/SDK

The app repo's own Makefile drives everything locally:

```sh
make debug          # assembleDebug
make release        # assembleRelease (needs release.keystore + keystore.properties)
make avd AVD=Pixel_9_Pro_XL   # boot an emulator you created in Studio
make devices        # confirm adb sees it
make install-debug  # adb install the debug APK
```

### Flow B — lab flow: workstation without SDK, build host with everything

```sh
cd ~/devcode/zre/scripts/ndk
make app-sync     # rsync app repo -> build host (APP_DIR/APP_HOST overrides)
make app-build    # detached gradle assembleDebug+assembleRelease
make app-wait     # blocks until BUILD SUCCESSFUL/FAILED (5-min cap)
make app-log      # expect: BUILD SUCCESSFUL (in $HOME/zre-apk-build.log on the host)
make app-pull     # dist/app/app-debug.apk + app-release.apk on the workstation
```

Signing material (fresh environment): create it ONCE, keep it private,
never commit it:

```sh
cd ~/devcode/vision-cam-health
export STORE_PASS=<choose-strong> KEY_PASS=<choose-strong>
make keystore      # keytool -genkey ... release.keystore, alias visioncamhealth
cat > keystore.properties <<EOF
storeFile=../release.keystore
storePassword=$STORE_PASS
keyAlias=visioncamhealth
keyPassword=$KEY_PASS
EOF
```

Both files are git-ignored, and `make app-sync` copies them
(SYNC_KEYFILES=0 withholds them if you share a build host). Losing the
keystore means losing update-in-place installs (signature continuity);
custody is operator-side.

What the verified release artifact contains: R8-minified, classes.dex,
`libzrejni.so` for arm64-v8a / armeabi-v7a / x86_64, no `assets/zre/`
entries (nothing bundled), V2-signed.

---

## Stage 6 — Run it on the emulator

On the build host (or workstation in Flow A). If no emulator is running
yet, boot one first:

```sh
ssh <build-host>
~/Android/Sdk/emulator/emulator -list-avds
~/Android/Sdk/emulator/emulator -avd <name> -no-boot-anim &

# no AVD exists yet? install an image, create one, then boot it:
# sdkmanager "system-images;android-34;google_apis;x86_64"
# ~/Android/Sdk/cmdline-tools/latest/bin/avdmanager create avd \
#     -n zre_test -k "system-images;android-34;google_apis;x86_64"
```

Then, from the workstation (`EMU` is `emulator-5554` by default):

```sh
cd ~/devcode/zre/scripts/ndk
make emu-install        # debug APK via adb
make emu-launch         # start MainActivity
make emu-logcat         # service log lines, tag visioncamhealth-Zre
```

First run — the plain node needs nothing staged; the log MUST show:

```
zre service start (bootElapsed=...)
zre node up uuid=... name=... group=CHAT gossip=...
```

No key step exists any more. (The archived Curve flow needed a pair in
`zre_identity` prefs or the node refused to run; `make keys-xml` /
`zre_feed.sh` / `make emu-keys-stage` remain available only for
reproducing that archived design — see `scripts/ndk/CURVE-MESH.md`.)

After a reboot the app starts the service itself (BOOT receiver) —
same single executor path, so just `make emu-bootlog` to confirm
"node up".

Curve rotation evidence (archived Curve flow only): feed a SECOND pair
(`make curve-keys KEY_NAME=phone2 && make emu-keys-stage KEY_NAME=phone2`)
and watch the hub (Stage 7) observe the OLD identity leave / NEW
identity enter. The current plain node has no identity to rotate.

---

## Stage 7 — Prove the mesh (PC <-> device)

The trial matrix and full evidence live in `README.md`
("Phone trial procedure" + the app-on-emulator section). Short recipes:

Phone peer (zc_peer from Stage 3, pushed with `make launch-phone`) —
Curve by default:

```sh
make trial-beacon        # discovery + JOIN/SHOUT over Curve
make trial-gossip        # same via the Python hub on tcp://*:19872
make trial-dial          # direct TCP dial PC -> phone
```

The app runs PLAIN, so its trial MUST use `--plain`, and the peer name
must match the app's `Build.MODEL` (the app names its node from it):

```sh
$PY scripts/ndk/phone_trial.py gossip --plain --hub-port 19872 \
    --peer-name <app-Build.MODEL> --wait 90
```

PASS lines must contain `announce ip=<device-ip>` and zero EVASIVE in
the window. Emulator caveat: its NAT is one-way (the emulator dials
OUT, nothing dials IN), so the emulator case uses `adb forward` (host
dials device) plus the hub's assisted dial — step 4 in `README.md`
section "The APP (zre_jni) on the emulator". Success looks like:

```
GOSSIP_PASS   (ENTER + JOIN + SHOUT zre-ready, announce ip=10.0.2.16)
```

---

## Stage 8 — Zero-leak proof for the C node

`scripts/ndk/valgrind-harness/README.md` is the runbook (fresh-env
requirements included: valgrind + the pinned zyre stack on any Linux
host). Two drivers must BOTH produce, on the shipped `zre_jni.c`:

```
All heap blocks were freed -- no leaks are possible
ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
```

One is the full lifecycle, the other the production topology (pump
thread blocked in recv while another thread stops the node). Any
change to `zre_jni.c` re-runs the harness.

---

## Stage 9 — Deploy

- Debug/development: `make emu-install` (emulator) or
  `adb -s <phone-serial> install -r -t dist/app/app-debug.apk`
  (wireless adb: `adb connect <phone-ip>:<port>` first).
- Production: `app-release.apk` — signed with YOUR `release.keystore`.
  Same keystore for every update, or Android refuses the in-place
  upgrade. Uninstall+reinstall accepts a different signer but loses
  app data.
- The current app needs no keys: it runs plain ZRE. The archived Curve
  flow (keys pasted in settings) is in `CURVE-MESH.md`.

---

## Troubleshooting (common first-run failures)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `make app-log`: "BUILD FAILED" with a Kotlin warning | debug Kotlin treats warnings as errors | fix the warning as reported |
| Gradle can't find the SDK on the build host | `local.properties` missing there | add `sdk.dir=...` on the host app copy |
| `refusing to run plain` persists after feeding keys **(Curve restoration only; this line does not exist in the plain build)** | stale app process, or malformed XML | relaunch the app; validate 40-char Z85 (`make curve-check`) |
| Emulator app unreachable from the PC | one-way emulator NAT (not a bug) | use the assisted-dial recipe (Stage 7) |
| `adb reverse` tunnel carries no frames | wrong direction for this app's dial | use `adb forward` (host dials device) |
| `pytest` hangs in test_security | stop order of the legacy code | fixed upstream of this repo; pin this repo's HEAD |
| Valgrind harness: 66 "errors" | the DRIVER's use-after-free (historical) | use the committed drivers as-is; re-stage `zre_jni.c` from the app repo |
| SSH remote targets misbehave | login shell is fish | keep the `bash -lc` wrapper (Makefile note) |
| `examples/gossip_mesh.py join`: `ZMQError: Invalid argument` | `--hub` passed without a scheme | use `--hub tcp://<host>:<port>` |
| `rsync` to the build host: "remote command not found" | fish login shell breaks rsync's server bootstrap | `--rsync-path=/usr/bin/rsync` (the Makefile's app-sync already does) |
| `adb install`: `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | debug keystore changed (new/cleaned host) | `adb uninstall com.visioncamhealth` then install; app prefs are lost (re-stage keys only for a restored Curve build) |
| Trial PASS lines show EVASIVE spikes | hub store keeps dead tuples (TTL 0) | restart the hub; hygienes are future work (README) |
