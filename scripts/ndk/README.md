# Static libzyre for Android — verified build + phone interop

Everything needed to cross-compile **libzmq / czmq / zyre as static
libraries** for Android, build a static `zc_peer` harness, and verify
end-to-end interop with the Python `zre` implementation on this PC.

Nothing here is guessed: the verified results below were reproduced by
experiment on the build host and the phone; sketches are marked.

> **New to this project?** Start with
> [`GETTING-STARTED.md`](GETTING-STARTED.md) — the fresh-environment,
> stage-by-stage walkthrough (toolchain, keys, app build, emulator
> run, trials, leak proof, deploy). This README is the deep reference
> each stage links back to.

## What you get

```
scripts/ndk/
├── ANDROID_LIBZYRE.md  app-integration guide (JNI/CMake/Gradle glue, events)
├── zre_static_all.sh   one-shot cross-build (run ON the build host)
├── phone_trial.py      phone <-> PC conversation trial (run on this PC)
├── curve_keys.py       device Curve pair: ensure / check / release
├── host_verify.sh      static-link NEEDED check (run on the build host)
├── host_launch_phone.sh  adb launch helper (run on the build host)
├── Makefile            push / build / verify / pull / trial targets
├── README.md           this file
└── dist/               pulled zc_peer binaries + curve-release staging
```

On the build host the script produces:

```
~/zre-android-deps/            libsodium 1.0.20-stable, libzmq, czmq clones
~/zre-android-build/
  prefix/<abi>/lib/            libsodium.a libzmq.a libczmq.a libzyre.a (+ headers)
  prefix/<abi>/bin/            czmq/zyre CLI tools that build here
                               (zmakecert, zpinger — no zgossip CLI exists)
~/zc-android/
  zc_peer-armeabi-v7a          static, for 32-bit phones
  zc_peer-arm64-v8a            static, for 64-bit phones/emulators
  zc_peer-x86_64               static, for x86_64 emulators
```

## Verified environment

| Component        | Version / value                              |
|------------------|----------------------------------------------|
| Build host       | Ubuntu 26.04, 16 CPUs (`iam@10.0.89.26`)     |
| NDK              | r30 (`30.0.16248370`)                        |
| API level        | 24 (matches the app `minSdk`)                |
| CMake/Ninja      | Android SDK bundled `cmake/4.1.2`            |
| libsodium        | 1.0.20-stable release tarball                |
| libzmq           | master @`4649337`                            |
| czmq             | master @`b669c7e`                            |
| zyre             | master @`f716769` (`v2.0.1-96-gf716769`)     |
| Phone            | Xiaomi `23028RNCAG` (Redmi Note 13), armeabi-v7a only, Wi-Fi `10.0.89.13/22` |
| PC (zre Python)  | `10.0.89.21`, venv `~/devcode/.env/zre/bin/python3` |

## Quick start

```sh
make push          # scp zc_peer.c + zre_static_all.sh to the build host
make build         # launch the cross-build there (all three ABIs)
make log           # follow ~/zre_static_all.out on the build host
make verify        # readelf NEEDED check + artifact listing
make pull          # copy zc_peer-* back into dist/
make trial-beacon  # beacon-discovery conversation phone <-> PC
make trial-gossip  # same conversation through a ZGS hub (no UDP)
```

`push`, `build`, `log`, `verify`, `pull`, `trial-*` talk to the build
host over SSH (`BUILD_HOST`, `BUILD_PASSWORD` in the Makefile) and to
the phone over wireless adb (`PHONE_ADB`, `PHONE_IP`). Every remote
payload is wrapped in `bash -lc` on purpose: the build host's login
shell is fish, which rejects POSIX recipes (`RE=...` assignments and
backslash line continuations) — keep that wrapper in new targets.

## The build chain (and the seven problems that had to be fixed)

Each ABI builds in this order: **libsodium → libzmq → czmq → zyre →
zc_peer**, all static (`--disable-shared`-equivalent CMake options), then
`zc_peer` is linked as a hardened static PIE.

1. **libzmq's CMake needs libsodium for Curve.**
   `ENABLE_CURVE=ON` without `WITH_LIBSODIUM` fails: *"No CURVE provider
   found"* (libzmq CMakeLists.txt:290). The bundled tweetnacl fallback
   only exists in the autotools build. Solution: build libsodium first
   and stage `libsodium.a` + headers into the same prefix.
2. **libsodium master has no CMake and blocks autogen.sh**
   (*"please do not run arbitrary, undocumented commands"*). Solution: use
   the official **`dist-build/android-<arch>.sh`** scripts from the
   1.0.20-stable tarball (`ANDROID_NDK_HOME=<ndk> NDK_PLATFORM=android-24
   LIBSODIUM_FULL_BUILD=1`). They produce correct per-arch trees
   (`libsodium-android-armv7-a`, `-armv8-a+crypto`, `-westmere`) with
   16 KB-page-friendly LDFLAGS.
3. **NDK cross-mode find filtering.** The NDK toolchain restricts
   `find_library` to the sysroot (`FIND_ROOT_PATH_MODE=ONLY`), so the
   staged prefix is invisible to `find_package`. Solution per project:
   `CMAKE_FIND_ROOT_PATH_MODE_{LIBRARY,INCLUDE,PACKAGE}=BOTH` plus
   `PKG_CONFIG_LIBDIR=<prefix>/lib/pkgconfig` so pkg-config cannot see
   host (x86_64) packages by mistake.
4. **Duplicate `sha1_result`/`sha1_loop` symbols at static link.**
   libzmq's built-in sha1 (`external/sha1`, used by the WebSocket
   transport) collides with czmq's foreign sha1 (`src/foreign/sha1`,
   compiled into `zdigest.c`) — only in a static-only build; shared
   builds hide it behind visibility. Solution: `-DENABLE_WS=OFF` in
   libzmq (zyre never uses WebSocket transport).
5. **czmq's `zsp` and zyre's tools/selftests link without `liblog`.**
   czmq `zsys.c` calls `__android_log_print` on Android; zproject's
   CMake link lines do not add `-llog`, and zyre's exe links also miss
   the C++ runtime and `-L`. Solution per project:
   `-DCMAKE_C_STANDARD_LIBRARIES="-llog -lsodium -lc++_static -lc++abi"`
   and `-DCMAKE_EXE_LINKER_FLAGS="-L<prefix>/lib"`.
6. **`-lstdc++` does not exist in NDK r30.** libzmq's generated
   `libzmq.pc` puts `-lstdc++` in `Libs.private`; anything consuming it
   statically fails. Solution: after each install, sed the staged
   `.pc` files: `-lstdc++` → `-lc++_static -lc++abi` (the `fix_pc`
   helper in the script).
7. **Android 11+ blocks `RTM_GETLINK` netlink for unprivileged
   processes.** Official docs (developer.android.com, *Best practices
   for unique identifiers*): this impacts `getifaddrs()` and
   `RTM_GETLINK`; observed on this phone: `ip link show` → *Permission
   denied*, and czmq `zbeacon` → *"No broadcast interface found"* with
   name or IP pinned. czmq already contains a pure-ioctl interface
   loader in `src/ziflist.c` (`SIOCGIFCONF`/`SIOCGIFADDR`/
   `SIOCGIFFLAGS`/`SIOCGIFBRDADDR`/`SIOCGIFNETMASK`); the script forces
   it on Android:
   `#if defined (HAVE_GETIFADDRS) && !defined (__ANDROID__)`.
   After the patch the phone logs `zbeacon: configured,
   hostname=10.0.89.13` and UDP discovery works.

Also required: libzmq's `curve_keygen` target is removed from the
cloned CMakeLists (it compiles before `zmq.h` is staged in static-only
builds — `tools/curve_keygen.cpp:5 fatal error: 'zmq.h' file not
found`). The script keeps this idempotent.

## Static-link verification

`make verify` runs `llvm-readelf -d` on every `zc_peer` binary. Expected:

```
NEEDED: libc.so, libdl.so, libm.so, liblog.so   (bionic system libs only)
```

No `libzmq`/`libczmq`/`libzyre`/`libsodium`/`libc++_shared.so` entries —
everything is inside the executable. Run it on the phone with no
`LD_LIBRARY_PATH` at all.

## Curve keys for the PC tools (`zc_peer`, `phone_trial.py`)

> **Since 2026-10-23 the mobile app runs PLAIN ZRE** (no keys, no ZAP,
> no settings sheet). Curve remains the default on the PC side:
> `zc_peer` and `phone_trial.py` (unless `--plain`) require a pair. The
> archived app Curve design is
> [`CURVE-MESH.md`](CURVE-MESH.md).

The PC-side key commands:

```sh
make curve-keys        # ensure ~/.avedge/phone1.{priv,pub} (avedge format)
make curve-check       # 40-char Z85 + public derives from private (x25519)
make curve-release     # stage dist/curve/ handoff text (operator material)
make curve-cross-repo-keys-check   # avedge toolchain cross-check when present
```

Key generation is off-device ONLY (dashboard today, operator tooling like
these targets alongside it). The phone peer receives the pair through
`phone_trial.py build-args`; the app no longer consumes it.

`make push` runs `curve-check` first: a broken pair can never reach the
build host. The trial targets generate their phone args with
`phone_trial.py build-args` (phone pair `phone1`, PC pair `avedge`,
both read from `~/.avedge` — the same files the avedge `make keys`
flow manages). **Since 2026-10-23 the mobile app runs PLAIN ZRE and
consumes no keys**; the pair is only for the PC/`zc_peer` side. The
archived app Curve flow (off-device keys pasted in settings) is in
[`CURVE-MESH.md`](CURVE-MESH.md).

## Phone trial procedure

```sh
# on the build host: one-time
adb -s <PHONE_ADB> push ~/zc-android/zc_peer-armeabi-v7a /data/local/tmp/zc_peer
adb -s <PHONE_ADB> shell chmod 755 /data/local/tmp/zc_peer
```

Then either `make trial-beacon` (recommended — it drives `build-args`,
the base64 launch and the trial script) or run pieces manually. Every
trial now runs **Curve-only with avedge keys**: the phone pair is
`~/.avedge/phone1.{priv,pub}` (generated by the avedge toolchain via
`make curve-keys`), the PC node loads `~/.avedge/avedge.*` with
`--avedge`, and `phone_trial.py build-args --trial-mode <mode>` writes
the args file so no shell ever mangles the Z85 keys.

The phone side note (beacon mode context, in an `adb shell` that must
stay attached):

```
/data/local/tmp/zc_peer --name phone1 --interface wlan0 \
    --curve-pub <z85> --curve-sec <z85> \
    --join CHAT --shout CHAT:hello-secure-from-phone --shout-every 2 \
    --duration 120 --verbose
```

The PC side: `python3 scripts/ndk/phone_trial.py beacon --avedge`. PASS
means the Python node saw `ENTER` + `JOIN` + `SHOUT` from `phone1` with
`announce ip=...` on the PASS line (X-MAC when the device allows), and
the phone's log shows `EVENT CURVE-PUBKEY`, `zauth: ZAP request
mechanism=CURVE`, and the secured `EVENT WHISPER ... content=hello-from-Python`.

Verified on 2026-10-19, phone `23028RNCAG` over Wi-Fi (same subnet as
the PC, no hotspot needed) — final state, every row Curve-only with
avedge-generated keys on both sides:

| Trial                        | Result                     |
|------------------------------|----------------------------|
| beacon discovery phone↔PC    | **BEACON_PASS** (curve: v3 beacons both sides, ENTER/JOIN/SHOUT both ways, whisper received by phone, `announce ip=10.0.89.13`) |
| gossip through a ZGS hub     | **GOSSIP_PASS** (Python hub on `tcp://*:19872`, phone `--endpoint tcp://10.0.89.13:19870 --gossip-connect`, secured ZRE links) |
| curve-specific beacon run    | **CURVE_PASS** (`zauth: ZAP request mechanism=CURVE` → allowed; secured whisper/shout) |
| direct TCP dial PC→phone     | **DIAL_PASS with `--endpoint`** — the phone binds `tcp://10.0.89.13:19870` (`--endpoint`, `PEER_PORT` default) and Python dialled it, full ENTER/JOIN/SHOUT flow; a bare `--beacon-peer-port` WITHOUT `--endpoint` does NOT work — in this zyre build the inbox bind happens only in the beacon-start or endpoint branch (`zyre_node.c:1601-1612`), so always pair a fixed inbox port with an explicit `--endpoint` or working beacons |

### The APP (zre_jni) on the emulator — one-way-NAT trial

> **Since 2026-10-23 the app runs PLAIN ZRE.** Same NAT recipe, but the
> trial must pass `--plain` and the app's peer name (its `Build.MODEL`);
> no `--avedge`, no `X-PUBLICKEY`. The Curve-era command is kept below
> as history — the full Curve record is
> `scripts/ndk/CURVE-MESH.md`.

The emulator sits behind its software NAT: it can dial OUT (the
gossip/ZGS connect works), but the PC cannot dial IN — the app's
published endpoint `tcp://10.0.2.16:<port>` is unreachable from any
host. The assisted path that the pinned zyre's
`zyre_set_advertised_endpoint` plus our `--assisted-connect` provide:

1. The app binds its gossip inbox on `0.0.0.0:19870` and advertises
   `tcp://<iface-ip>:19870` (zre_jni.c `gossipConnect`).
2. On the BUILD HOST (the emulator's own machine), forward the
   host-side port to the device. Use `forward` (host dials device),
   NOT `reverse` — the direction the dial needs:
   `adb -s emulator-5554 forward tcp:19870 tcp:19870`
3. The trial tool runs on the zre WORKSTATION (a different machine
   than the build host in this lab): bridge the first hop with an SSH
   local forward, `ssh -N -L 19870:127.0.0.1:19870 <build-host>`, so
   the tool's `127.0.0.1:19870` dial lands on the build host's adb
   forward.
4. Run the trial with the assisted dial. Current plain app:

   ```
   phone_trial.py gossip --plain --hub-port 19872 \
       --peer-name <app-Build.MODEL> \
       --assisted-connect tcp://127.0.0.1:19870
   ```

   Curve-era app (historical): `--avedge` instead of `--plain`; the
   tool then took the key from the peer's HELLO `X-PUBLICKEY` header.

`GOSSIP_PASS` = ENTER + JOIN + the app's one-shot `SHOUT zre-ready`
seen by the Python hub with `announce ip=10.0.2.16`, zero EVASIVE in
the window. Curve-era key rotation ("compromise → replace pair") was
verified the same way: after pasting a fresh pair, the hub observed a
NEW peer identity (`ENTER` with a changed `peer_id`) from the same
app.

Lab observation that is NOT a defect of this repo (kept on record):
the Python zre hub's gossip hub (`zre/gossip.py`) keeps published
tuples forever (TTL 0 = never expires), so dead app instances linger
as "unknown" peers that produce idle SILENT/EVASIVE noise on both
sides until the hub restarts. Harmless to the mesh; collision-hygiene
for the ZGS store is a future improvement, not a blocker.

## CURVE trial — manual invocation (post `make push`)

The `make trial-*` targets already do this end-to-end; to drive one
manually, use the avedge store the same way the targets do:

```sh
# phone args file: phone pair from the avedge store; zc_peer is
# Curve-only (no --curve flag) and no shell ever sees the keys
make curve-keys KEY_NAME=phone1
$(PYTHON) phone_trial.py build-args --trial-mode beacon --avedge phone1 --args-file args.txt
make launch-phone ARGS_FILE=args.txt
# PC side: its own pair, from the operator's default avedge pair
$(PYTHON) phone_trial.py curve --avedge --wait 35
```

Expected phone log: `EVENT CURVE-PUBKEY <z85>` then `zauth: ZAP request
mechanism=CURVE` → `- allowed (CURVE allow any client)`. If keys are
refused, `zc_peer` exits with `EVENT ERROR invalid-curve-key` rather
than running plain, by design — `make curve-check` validates the pair
(format + public-derived-from-private) before anything ships.

## Known device constraints (measured)

- The phone is **32-bit only** (`armeabi-v7a, armeabi`): arm64 artifacts
  do not run on it; the arm build is the one to push.
- `getifaddrs()` fails for **shell uid (adb)** — see fix 7 above. An app
  running under its own uid must be checked on-device; the ioctl path
  makes czmq's enumeration legal in both contexts.
- Android refuses `nohup`-style backgrounding inside `adb shell` (the
  session hangs until the child exits). Launch the phone peer as a
  **foreground** adb shell inside a `nohup` on the PC/build host —
  `make trial-beacon` does exactly that.
- Wi-Fi reception of UDP broadcasts from the PC works without a
  MulticastLock for *broadcast* datagrams; if a device filters
  multicast, acquire `WifiManager.MulticastLock` in the app
  (permission `CHANGE_WIFI_MULTICAST_STATE`) — verify on-device.

## App integration

See `ANDROID_LIBZYRE.md` in this directory — it covers the
JNI/CMake glue for embedding `libzyre.a` + `zc_peer.c` into the
Android app, the `ndkVersion` pin reconciliation, thread rules, and the
Python-side recipes (plain, Curve, WAN, gossip) with verified event
shapes.

## App prefs staging for the archived Curve flow (emulator only)

> Applies only to a restored Curve build. The current plain app ignores
> `zre_identity` prefs entirely. See [`CURVE-MESH.md`](CURVE-MESH.md).

The identity paste that the Curve settings sheet wrote can be staged
with an XML file whose content mirrors
`shared_prefs/zre_identity.xml` exactly (`curve_pub` + `curve_sec`,
40-char Z85 each):

1. Build the XML, push it, and feed it with `scripts/ndk/zre_feed.sh`
   (run via `adb shell run-as com.visioncamhealth sh
   /data/local/tmp/zre_feed.sh <file>`, from the BUILD host where the
   emulator is adbd-connected).
2. Relaunch the app. `ZreService` reads the stored pair and builds the
   node — the exact code path future dashboard users get, no special
   app-side switches, and NO key generation in the app.
3. Rotation ("compromise") = paste a different pair and relaunch
   (or tap Apply, which sends `ACTION_REKEY`); the mesh observes the
   identity change as a new peer entering.

Evidence trail: `docs/SESSION-VALIDATION.md` (zre repo root),
`scripts/ndk/valgrind-harness/README.md` (C-side zero-leak verdicts).
