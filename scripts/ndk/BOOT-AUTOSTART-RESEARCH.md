# Boot-autostart foreground service for the zre node — research and design record

**Status:** research completed 2026-10-20; design decisions derived from it and
implemented in the app repo `/home/iam/devcode/vision-cam-health`
(`ZreService.kt` / `BootReceiver.kt` / `AndroidManifest.xml` — paths below
like `app/src/main/...` are relative to that repo; this document is part of
the `scripts/ndk` package that ships the verified libzyre stack).
**Why this document exists:** the question "can an Android service run
automatically at every boot, on any Android phone?" is version- and
OEM-dependent, the answers contradict folk wisdom found online, and the
details below pin every claim to a primary source so another engineer (or
LLM) can audit, extend, or re-decide without starting from scratch.

- App under discussion: `com.visioncamhealth` ("AVEdge"), `minSdk 24`,
  `targetSdk 37`.
- The service in question runs a **Curve-secured libzyre node** (statically
  linked, JNI) that sends 1 Hz UDP discovery beacons and keeps ZMQ sockets
  open — a long-lived LAN P2P presence, not a one-shot data transfer.
- Test devices in scope: any Android phone (version-generic code, not
  built for one model), plus x86_64 emulators and one retail Android 13
  Xiaomi during field validation.

---

## 0. TL;DR verdict matrix

| Platform (OS version) | FGS start from `BOOT_COMPLETED` receiver                      | Long-run always-on viability with our chosen type          |
|-----------------------|----------------------------------------------------------------|------------------------------------------------------------|
| Android 8/9 (26–28)   | YES (receiver must call `startForegroundService()` on 26+)     | YES (no FGS-type system yet; plain `startForeground()`)     |
| Android 10–13 (29–33)| YES (typed FGS required from 29; type declared in manifest)    | YES; Android 13 has **no** dataSync-from-boot block and **no** 6 h timeout |
| Android 14 (34)       | YES; only `microphone` type is blocked from boot               | YES; per-type FGS permissions required from targetSdk 34    |
| Android 15/16/17 (35+)| **YES for `connectedDevice` / `specialUse` / etc.; NO for `dataSync`, `camera`, `mediaPlayback`, `phoneCall`, `mediaProjection`, `microphone`** → `ForegroundServiceStartNotAllowedException` | YES with `connectedDevice` (no runtime timeout; `dataSync` would die at the 6 h/24 h limit on these platforms) |
| Any platform, app **force-stopped** or "restricted" standby bucket | NO — broadcast not delivered / FGS start rejected           | Re-launch the app once after install/force-stop             |
| MIUI/HyperOS (Xiaomi) | Broadcast delivered ONLY if the vendor "Autostart" toggle is ON | User hand-toggle required; not settable programmatically or via adb (non-root) |

**Bottom line:** boot-autostart is **doable and implemented**, conditioned on
(a) the `connectedDevice` FGS type (NOT `dataSync`), (b) the app having been
launched once since the last install/force-stop, (c) on some OEM skins
(notably MIUI/HyperOS) a one-time user Autostart toggle, and (d) on Android 17
platforms a runtime `ACCESS_LOCAL_NETWORK` grant because targetSdk 37 makes
it mandatory for LAN UDP traffic.

**Premise correction:** targetSdk 37 = **Android 17** (released 2026-06-16),
not "Android 16-era APIs".
Sources: https://en.wikipedia.org/wiki/Android_17 ,
https://developer.android.com/about/versions/17/release-notes

---

## Q1. Can a foreground service be started from a `BOOT_COMPLETED` receiver (targetSdk 37 on Android 13/14/15/16/17)?

### Q1.1 The boot receiver FGS start is a documented exemption from background-start restrictions

Source: https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start

Quote:

> "In the following situations, your app can start foreground services even
> while your app runs in the background: … After the device reboots and
> receives the ACTION_BOOT_COMPLETED, ACTION_LOCKED_BOOT_COMPLETED, or
> ACTION_MY_PACKAGE_REPLACED intent action in a broadcast receiver."

### Q1.2 Required permissions

Source: https://developer.android.com/develop/background-work/services/fgs/service-types and
https://developer.android.com/develop/background-work/services/fgs/troubleshooting

- `FOREGROUND_SERVICE` — normal permission, required since API 28. The
  troubleshooter page: *"If an app targets Android 9 (API level 28) or
  higher, it must have the FOREGROUND_SERVICE permission to launch a
  foreground service."*
- Per-type FGS permissions since targetSdk/API 34: the service-types page —
  *"Beginning with Android 14… request the appropriate foreground service
  permission for that type"*. For us: `FOREGROUND_SERVICE_CONNECTED_DEVICE`
  (and, for the app's OTHER service, `FOREGROUND_SERVICE_DATA_SYNC`).
  Missing permission → `SecurityException` (troubleshooting page).

### Q1.3 The exact blocked-types list from BOOT_COMPLETED (Android 15 platform change)

Source: https://developer.android.com/about/versions/15/behavior-changes-15#fgs-boot-completed

Quote (verbatim):

> "BOOT_COMPLETED receivers are not allowed to launch the following types of
> foreground services: **dataSync**, **camera**, **mediaPlayback**,
> **phoneCall**, **mediaProjection**, **microphone** (this restriction has
> been in place for microphone since Android 14). If a BOOT_COMPLETED
> receiver tries to launch any of those types of foreground services, the
> system throws `ForegroundServiceStartNotAllowedException`."

Cross-confirmed on the service-types page:

> "Apps that target Android 15 or higher are not allowed to launch a data
> sync foreground service from a BOOT_COMPLETED broadcast receiver."
> https://developer.android.com/develop/background-work/services/fgs/service-types

`connectedDevice`, `specialUse`, `systemExempted`, `health`, `remoteMessaging`,
`shortService`, `navigation`, `mediaSession`, and `location` (from certain
contexts) are NOT on this list — of these, `connectedDevice` and `specialUse`
are the ones without additional time limits or narrow qualifying conditions
(see Q2.2).

### Q1.4 `startForeground()` promotion window after `startForegroundService()`

Source: https://developer.android.com/develop/background-work/services/fgs/troubleshooting

Quote:

> "When you launch a service by calling context.startForegroundService(), that
> service has a few seconds to promote itself to a foreground service by
> calling ServiceCompat.startForeground(). If the service does not do so, it
> throws an internal ForegroundServiceDidNotStartInTimeException:
> Context.startForegroundService() did not then call Service.startForeground()."

Exact value in current AOSP `main` (may be DeviceConfig-tuned per build):
`DEFAULT_SERVICE_START_FOREGROUND_TIMEOUT_MS = 30 * 1000` (30 s), DeviceConfig
key `service_start_foreground_timeout_ms`. Source (AOSP ActivityManagerConstants):

https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/am/ActivityManagerConstants.java (lines 304, 449–450, 1941–1945)

Community folklore says "~5 s" on older releases (e.g. the drozer-agent
boot receiver comment, https://github.com/ReversecLabs/drozer-agent/blob/master/agent/src/pentest/java/com/reversec/dz/receivers/BootReceiver.java);
treat the exact duration as **version/config-dependent**. Engineered rule:
**call `startForeground()` synchronously at the very top of
`onStartCommand()`, before any I/O** (our implementation does exactly this).

### Q1.5 Enforcement is OS-version-gated, not targetSdk-gated (proves our version matrix)

Source: https://developer.android.com/about/versions/15/behavior-changes-15#fgs-boot-completed (testing note)

Quote:

> "To test your app's behavior, you can enable these new restrictions even if
> your app is not targeting Android 15 (as long as the app is running on an
> Android 15 device). Run:
> `adb shell am compat enable FGS_BOOT_COMPLETED_RESTRICTIONS your-package-name`"

Consequence:

- An **Android 13/14 platform does not contain** the dataSync-from-boot
  check at all → the boot start works there regardless of targetSdk (only
  the older caveats above apply). This is what makes the retail Android 13
  Xiaomi viable (Q4 covers its OEM gate).
- Android **15/16/17** platforms enforce the block against targetSdk-35+
  apps. Our targetSdk 37 is affected on those platforms — hence the
  `connectedDevice` choice (Q2.2).

---

## Q2. Android 15 / 16 / 17 specifics

### Q2.1 The dataSync 6-hour timeout (why dataSync was never going to be "always-on" on 15+)

Source: https://developer.android.com/about/versions/15/behavior-changes-15#datasync-timeout and
https://developer.android.com/develop/background-work/services/fgs/timeout

Quotes (verbatim):

> "The system permits an app's dataSync services to run for a total of 6
> hours in a 24-hour period, after which the system calls the running
> service's `Service.onTimeout(int, int)` method… If the service does not
> call `Service.stopSelf()`, the system throws an internal exception: Fatal
> Exception: android.app.RemoteServiceException: 'A foreground service of
> type dataSync did not stop within its timeout'…"

> "…if the user brings the app to the foreground, the timer resets."

> "If your app's dataSync foreground services have run for 6 hours in the
> last 24, you cannot start another dataSync foreground service unless the
> user has brought your app to the foreground… `ForegroundServiceStartNotAllowedException`
> with an error message like 'Time limit already exhausted for foreground
> service type dataSync'."

Test knobs:

```sh
adb shell am compat enable FGS_INTRODUCE_TIME_LIMITS <pkg>
adb shell device_config put activity_manager data_sync_fgs_timeout_duration <ms>
```

Note: the timeout **does not exist** on Android 13/14 platforms (same
platform-gating proof as Q1.5) — an always-on dataSync FGS "works" on the
13 Xiaomi only until the app runs on a 15+ device.

### Q2.2 Long-run-eligible FGS types: `connectedDevice`, `specialUse`, `systemExempted`

Source: https://developer.android.com/develop/background-work/services/fgs/service-types

- **`connectedDevice`** — documented description covers *"interactions with
  external devices that require a Bluetooth, NFC, IR, USB, or **network
  connection**"*. No runtime timeout; not on the Q1.3 boot block list. Our
  qualifying prerequisite permission: `CHANGE_WIFI_MULTICAST_STATE`
  (the docs require at least one of a permission set for the type — e.g.
  `BLUETOOTH_CONNECT`/`BLUETOOTH_SCAN`, `CHANGE_NETWORK_STATE`,
  `CHANGE_WIFI_STATE`, `**CHANGE_WIFI_MULTICAST_STATE**`, `NFC`,
  `TRANSMIT_IR`, `UWB_RANGING`). We declare it (also needed anyway for
  Wi-Fi multicast/broadcast reception efficiency).
- **`specialUse`** — requires free-form use-case declarations in the
  manifest reviewed at Play submission: *"developers should declare use
  cases in the manifest… These values and corresponding use cases are
  reviewed when you submit your app in the Google Play Console."*. Viable
  but review-gated; not needed when connectedDevice genuinely fits.
- **`systemExempted`** — heavily condition-gated. Quote: *"Device is in
  demo mode state; App is a Device Owner; App is a Profiler Owner; Safety
  Apps that have the ROLE_EMERGENCY role; Device Admin apps; Apps holding
  SCHEDULE_EXACT_ALARM or USE_EXACT_ALARM permission; VPN apps… Otherwise,
  declaring this type causes the system to throw
  `ForegroundServiceTypeNotAllowedException`."* Not our case.

**Design decision:** `connectedDevice`, rationale: honest fit (network P2P),
no timeout, boot-allowed, prerequisite already declared. Play-policy note:
declare the type in the Play Console (policy:
https://support.google.com/googleplay/android-developer/answer/13392821).

### Q2.3 Broadcast-delivery changes in 15/16/17 (what does NOT change)

- **Android 16 (all apps):** *"In Android 16, broadcast delivery order using
  the android:priority attribute or IntentFilter#setPriority() across
  different processes will not be guaranteed. Broadcast priorities will
  only be respected within the same application process."*
  https://developer.android.com/about/versions/16/behavior-changes-all —
  irrelevant to a single-package receiver, but explains nondeterministic
  ordering vs other apps' boot receivers.
- **Android 16 JobScheduler:** quota enforcement now factors standby bucket
  and FGS-concurrent jobs; simulate with
  `adb shell am set-standby-bucket APP_PACKAGE_NAME restricted` (same page).
- **Android 16 "Safer Intents"** is opt-in (`intentMatchingFlags="enforceIntentFilter"`)
  — does NOT affect system-sent BOOT_COMPLETED.
  https://developer.android.com/about/versions/16/behavior-changes-16
- **Android 17 — mandatory `ACCESS_LOCAL_NETWORK` runtime permission for
  LAN traffic for targetSdk-37+ apps.** Sources:
  https://developer.android.com/about/versions/17/behavior-changes-17 and
  (mechanics) https://developer.android.com/about/versions/16/behavior-changes-16
  which documents the permission matrix rows: *"Sending a UDP unicast,
  multicast, broadcast | yes"* and the `sendto failed: EPERM` failure mode.
  **Impact on this app:** the 1 Hz zyre UDP beacons + LAN ZMQ sockets need
  the runtime grant on Android 17 platforms. It falls under the
  NEARBY_DEVICES-group UX (users who granted a Nearby permission may not
  be re-prompted — test the actual grant state on-device).
  **Implemented:** declared in the manifest (harmless on pre-17 platforms,
  where the permission doesn't exist); runtime grant must happen via
  normal permission UX (Activity `requestPermissions`) or — for lab /
  field-trial installs — `adb shell pm grant com.visioncamhealth
  android.permission.ACCESS_LOCAL_NETWORK`.
- **Android 17 JNI restrictions (two, both respected by our native layer):**
  - *"Attempting to modify one of these fields through JNI APIs (such as
    SetStaticLongField()) will cause the app to crash"* — our JNI glue
    (app/src/main/cpp/zre_jni.c) does not touch Java static fields. Source:
    https://developer.android.com/about/versions/17/behavior-changes-17
  - *"All native files loaded using System.load() must be marked as
    read-only. Otherwise the system throws UnsatisfiedLinkError"* — we use
    `System.loadLibrary("zrejni")`, which loads from the APK-managed
    read-only native lib dir; no `System.load()` of app-writable paths.
- No WATCHDOG-style blocking of BOOT_COMPLETED **senders** was found: only
  an AOSP commit that *logs* receiver durations —
  https://android.googlesource.com/platform/frameworks/base/+log/3bd83b4772e20cd53e5aafff87dbf8f9cb110820

### Q2.4 Background-restriction states that block FGS entirely (any version)

Sources: https://developer.android.com/about/versions/13/behavior-changes-all ,
https://source.android.com/docs/core/power/app_mgmt

> "Can't launch foreground services / Existing foreground services are
> removed from the foreground / Alarms aren't triggered / Jobs aren't
> executed"

Simulate the "restricted" state:

```sh
adb shell cmd appops set PACKAGE_NAME RUN_ANY_IN_BACKGROUND ignore
```

Related: `adb shell am set-standby-bucket <pkg> restricted` (Q2.3).

---

## Q3. `RECEIVE_BOOT_COMPLETED` validity and unlock timing

### Q3.1 Still valid, and one of the last exempted implicit broadcasts

Source: https://developer.android.com/develop/background-work/background-tasks/broadcasts/broadcast-exceptions

> "ACTION_LOCKED_BOOT_COMPLETED, ACTION_BOOT_COMPLETED: Exempted because
> these broadcasts are sent only once, at first boot."

### Q3.2 Direct-boot / unlock timing

Sources: https://developer.android.com/privacy-and-security/direct-boot ,
https://android.googlesource.com/platform/frameworks/base/+/android-7.1.0_r7/docs/html/training/articles/direct-boot.jd

> "Encryption aware components can register to receive an
> ACTION_LOCKED_BOOT_COMPLETED broadcast… Once the user has unlocked the
> device, all components can access both the device encrypted storage as
> well as credential encrypted storage… or, you can receive the existing
> ACTION_BOOT_COMPLETED message, which now indicates the device has booted
> and the user has unlocked the device."

**Our choice:** plain `BOOT_COMPLETED` only (no `directBootAware`
components) because (a) the node's keys live in app assets of the
credential-encrypted storage, (b) pre-unlock runtime buys nothing for this
use case, (c) user unlock is a natural "device is really up" gate.

### Q3.3 The stopped-state gotcha (critical for testing)

- A freshly installed (never launched) or force-stopped app is in the
  "stopped state" and receives NO broadcasts — including BOOT_COMPLETED.
  Community evidence: https://stackoverflow.com/questions/71185639/receive-boot-completed-not-received-on-android-11
- Community observation (not officially documented): BOOT_COMPLETED is also
  delivered after app reinstall/update on A13–15:
  https://stackoverflow.com/questions/54625859/receive-boot-completed-when-will-it-be-triggered

**Testing rule:** after `adb install`, launch the app (or start the service
once via adb) BEFORE the first reboot, or the boot broadcast will never be
delivered — this looks exactly like "boot autostart doesn't work" and is
actually stopped-state gating.

---

## Q4. Xiaomi / MIUI / HyperOS "Autostart" (device-family operations manual — NOT hard-coded app behavior)

### Q4.1 The toggle is decisive and OFF by default for third-party apps

- *"Problem with Xiaomi devices is that they block autostarting of the
  application so it does not receive the BOOT_COMPLETED Broadcasts. To be
  able to receive it the user has to enable the application to autostart in
  settings."* — https://stackoverflow.com/questions/44145876/xiaomi-boot-completed-not-received
- Maintainer-confirmed (MacroDroid wiki): *"On Xiaomi (MIUI) devices, the
  custom Autostart permission must be enabled for MacroDroid. Without it
  MacroDroid will not run after a reboot and may stop working when the app
  updates."* — https://wiki.macrodroid.com/wiki/index.php/Troubleshooting
- Vendor documentation of the toggle location (official Xiaomi FAQ):
  Settings → Apps → Permissions → **Background autostart** —
  https://www.mi.com/uk/support/faq/details/KA-497681 ,
  https://www.mi.com/ph/support/faq/details/KA-507611
- dontkillmyapp (Sleep as Android maintainers): *"Traditionally, Xiaomi…
  belongs in the most troubled group… There are no APIs and no
  documentation for those extensions."* — https://dontkillmyapp.com/xiaomi

### Q4.2 What is and is NOT possible programmatically

- **Setting the toggle: NOT possible.** No documented Android API, no
  `settings put`/`appops`/`pm grant` key (the state lives in MIUI's
  SecurityCenter, its own permission store).
- **adb: NOT possible without root.** The circulating "Xiaomi-ADB" script
  that flips MIUI-internal settings explicitly requires root (`check_root`):
  https://github.com/ImKKingshuk/Xiaomi-ADB/blob/main/XiaomiADB.sh
  This research could NOT confirm any adb-only (non-root) method.
- **Reading the state: possible via hidden MIUI framework APIs**, with a
  Play-review risk warning from the library's own README: *"This library
  relies on hidden APIs in the MIUI framework to access the auto-start
  state… your application might not pass review unless you stop reporting
  library usage"* — https://github.com/XomaDev/MIUI-autostart
- **Deep-linking the user to the toggle screen: possible** (unofficial,
  device-surface dependent):
  `ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity")`
  — https://stackoverflow.com/questions/34149198/how-to-enable-auto-start-for-my-app-in-xiaomi-programmatically and
  https://stackoverflow.com/questions/49473230/

### Q4.3 MIUI battery management is a SECOND layer above AOSP battery optimization

- dontkillmyapp MIUI instructions (beyond the AOSP
  `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` grant): Power Saving Modes Off +
  *"Saving Power in The Background > Choose apps > select your app >
  Background Settings > **No restrictions**"* + App Battery Saver
  "No restriction" — https://dontkillmyapp.com/xiaomi
- Community guide (three-toggle checklist): *"Autostart alone is not enough
  if battery saver is still throttling the app"* —
  https://shizukudownload.com/guides/xiaomi/
- HyperOS-specific aggravations (community evidence, single-sourced and
  flagged as such):
  - *"Xiaomi HyperOS… will automatically disable autostart for apps that
    don't hold an Accessibility Service permission"* —
    https://github.com/MidnightQuery/Revanced-MicroG-Keep-Alive (single
    source; plausible, uncorroborated)
  - HyperOS 2 extra layer: *"Settings → Battery → Battery Usage Limits…
    This is separate from the Autostart permission"* —
    https://github.com/Riyaansh-Mittal/Xiaomi-HyperOS-Battery-Drain-Fix/blob/main/README.md
- Vendor-support confirmation of the multi-toggle reality (aCalendar):
  https://tapirappsgmbh.freshdesk.com/en/support/solutions/articles/36000105446-issues-with-xiaomi-devices

**Engineering consequence (implemented):** the universal app treats this as
**operational guidance, not code** — no Xiaomi SDK, no hidden-API reads, no
OEM branching. If the platform blocks the boot start, the receiver catches
the rejection and logs it (see the implemented receiver); the user side of
the fix is the toggle screen above. On non-MIUI devices the same code has no
gate beyond AOSP's.

---

## Q5. Battery-optimization interplay (AOSP layer)

Source: https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start (exemptions list)

> "The user turns off battery optimizations for your app"

With `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` granted (our app already gates
this in onboarding) and the app neither force-stopped nor in the "restricted"
state (Q2.4), the boot-triggered FGS start itself is fine on AOSP-lineage
images. The app's existing onboarding lever stays the right mechanism.

---

## Q6. Real-world precedent code (audited in October 2026)

1. **dev-hwang/flutter_foreground_task — `RebootReceiver.kt`** — boot →
   `ContextCompat.startForegroundService`, catching
   `ForegroundServiceStartNotAllowedException` (written against the modern
   restrictions):
   https://github.com/Dev-hwang/flutter_foreground_task/blob/master/android/src/main/kotlin/com/pravera/flutter_foreground_task/service/RebootReceiver.kt
   ```kotlin
   if ((intent.action == Intent.ACTION_BOOT_COMPLETED ||
        intent.action == "android.intent.action.QUICKBOOT_POWERON") &&
       options.autoRunOnBoot) { return startForegroundService(context) }
   // … catch (e: ForegroundServiceStartNotAllowedException)
   ```
2. **christianrowlands/android-network-survey — `StartAtBootReceiver.java`**
   (networking app — closest analog to ours) — boot → FGS with a
   **WorkManager fallback for restriction crashes**:
   https://github.com/christianrowlands/android-network-survey/blob/develop/networksurvey/src/main/java/com/craxiom/networksurvey/StartAtBootReceiver.java
   ```java
   context.startForegroundService(startServiceIntent);
   // Fallback to WorkManager with a delay — added because of crashes:
   // 'ForegroundServiceStartNotAllowedException - startForegroundService()
   //  not allowed due to mAllowStartForeground false'
   ```
3. **rustdesk/rustdesk — `BootReceiver.kt`** — the version split we mirror:
   https://github.com/rustdesk/rustdesk/blob/master/flutter/android/app/src/main/kotlin/com/carriez/flutter_hbb/BootReceiver.kt
   ```kotlin
   if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
       context.startForegroundService(it)
   } else {
       context.startService(it)
   }
   ```
4. **dataSync usage in modern code is user-initiated, not boot-started**
   (consistent with the Q2.1 reality): immich's background worker uses
   `ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC` inside
   `setForegroundAsync` —
   https://github.com/immich-app/immich/blob/main/mobile/android/app/src/main/kotlin/app/alextran/immich/background/BackgroundWorker.kt ;
   microG GmsCore `InstallService` —
   https://github.com/microg/GmsCore/blob/master/vending-app/src/main/kotlin/com/android/vending/installer/InstallService.kt
5. **No public repo found** doing `BOOT_COMPLETED → dataSync FGS` that
   targets SDK 35+ and still works on A15+ — consistent with the platform
   block. Repos that boot-start (flutter_foreground_task, rustdesk,
   networksurvey) either predate targetSdk 35 or use non-blocked types.

---

## Q7. Emulator testing angle

### Q7.1 Boot delivery on AOSP-lineage AVDs

AOSP-lineage x86_64 AVDs deliver BOOT_COMPLETED to third-party manifest
receivers reliably (baseline OEM-free path). Known emulator quirk to expect
in the lab (file it before mis-diagnosing): on Android 15/16 emulators,
apps started from Android Studio **receive BOOT_COMPLETED on every app
launch** — Google issuetracker 425569862
(https://issuetracker.google.com/issues/425569862, reproducible on A15/A16
emulators AND a Galaxy s24 A15; NOT on A14) and Chromium's duplicate
(https://issues.chromium.org/issues/381281310). Our receiver is idempotent
under repeated ACTION_START, so duplicates are harmless.

No GSI/CTS-Verifier-specific BOOT_COMPLETED delivery differences were found
documented for APIs 34/35/37 (unconfirmed either way; GSI release notes
exist per API, e.g. https://developer.android.com/about/versions/17/gsi-release-notes).

### Q7.2 Test commands and knobs (all verified against official pages)

```sh
# Fire the boot broadcast WITHOUT rebooting (test-only; on real user-build
# devices this can throw SecurityException — use `adb reboot` for final
# evidence; on emulators it delivers):
adb shell am broadcast -a android.intent.action.BOOT_COMPLETED your-package-name

# Enable the A15+ boot-restriction against a lower-target app (platform-gated):
adb shell am compat enable FGS_BOOT_COMPLETED_RESTRICTIONS your-package-name

# Enable the dataSync time limit / tune its duration:
adb shell am compat enable FGS_INTRODUCE_TIME_LIMITS <pkg>
adb shell device_config put activity_manager data_sync_fgs_timeout_duration <ms>

# Standby-bucket / battery-restriction states:
adb shell am set-standby-bucket <pkg> restricted
adb shell cmd appops set <pkg> RUN_ANY_IN_BACKGROUND ignore

# Simulate the task-manager "user stopped the app":
adb shell cmd activity stop-app PACKAGE_NAME

# Android 17 LAN permission (targetSdk 37 apps on A17 platforms):
adb shell pm grant com.visioncamhealth android.permission.ACCESS_LOCAL_NETWORK

# Boot completion gating for scripts (any of):
#   sys.boot_completed / dev.bootcomplete / init.svc.bootanim
adb shell getprop sys.boot_completed

# Verify the service post-boot:
adb shell dumpsys activity services com.visioncamhealth
adb shell dumpsys activity services com.visioncamhealth/.zyre.ZreService
adb shell pidof com.visioncamhealth
adb shell ps -A | grep visioncam

# Diagnose failures in logcat:
#   ForegroundServiceDidNotStartInTimeException
#   ForegroundServiceStartNotAllowedException
#   RemoteServiceException ('A foreground service of type dataSync did not stop...')
```

sources: behavior-changes-15 (#fgs-boot-completed, #datasync-timeout),
behavior-changes-16, handle-user-stopping
(https://developer.android.com/develop/background-work/services/fgs/handle-user-stopping),
troubleshooting; emulator boot-progress props:
https://stackoverflow.com/questions/3634041/detect-when-android-emulator-is-fully-booted

### Q7.3 Image types / root availability in the lab

Google APIs images = userdebug (adb root available for dumpsys deep-dives);
Google Play images = user (no adb root); bare AOSP images = eng. Source:
https://chromium.googlesource.com/chromium/src/+show/9cbee243cbbf1d37b0f52fa1eb3e0a61a98488fc/docs/android_emulator.md

---

## Derived design decisions (implemented in this repo)

| Decision | Choice | Rationale (trace to evidence) |
|---|---|---|
| FGS type for ZreService | `connectedDevice` | Not on the boot block list (Q1.3); no 6 h timeout (vs dataSync, Q2.1); honest use-case fit (Q2.2); prerequisite `CHANGE_WIFI_MULTICAST_STATE` already needed |
| FGS permission | `FOREGROUND_SERVICE_CONNECTED_DEVICE` added to manifest | Per-type permission required from targetSdk 34 (Q1.2) |
| `dataSync` for ZreService? | NO | Blocked from BOOT_COMPLETED on A15+ platforms for targetSdk 35+ (Q1.3/Q2.1); 6 h/24 h timer would kill an "always-on" node (Q2.1) |
| Promotion order in `onStartCommand` | `startForeground(...)` synchronously FIRST, before assets/keys I/O | Q1.4 window (AOSP default 30 s, DeviceConfig-tunable) |
| Typed promotion guard | `Build.VERSION.SDK_INT >= 29` → typed overload with `ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE`; else plain `startForeground(id, notif)` | Typed overload is API 29+; manifest `foregroundServiceType` attr is ignored by pre-29 platforms. minSdk 24 must hold |
| Receiver start call | `startForegroundService` on API ≥ 26, else `startService` | `startForegroundService` exists from API 26 (precedent: rustdesk, Q6.3) |
| Receiver rejection handling | catch `IllegalStateException` (parent of `ForegroundServiceStartNotAllowedException`, API 31+) and log | Precedent: flutter_foreground_task catches the narrow type; the parent class keeps one catch path across versions (Q6.1) |
| Boot type on Android 13 (e.g. the Xiaomi) | unchanged code | A13 platform has no dataSync-from-boot check and no 6 h timer (Q1.5/Q2.1) — connectedDevice is simply well within the rules everywhere |
| `ACCESS_LOCAL_NETWORK` | declared in manifest; runtime grant (Activity `requestPermissions` hook or `adb pm grant` for lab installs) | Mandatory for targetSdk 37 apps on Android 17 platforms; beacons/ZMQ are LAN UDP/TCP (Q2.3) |
| Direct boot (`LOCKED_BOOT_COMPLETED`) | NOT used | Keys in credential-encrypted storage; pre-unlock buys nothing (Q3.2) |
| Receiver duplicate-fire robustness | ACTION_START is idempotent in the service (create returns −2 → reattach; pump single) | A15/A16 emulator delivers BOOT_COMPLETED on every app launch (Q7.1) |
| `onTimeout(startId, fgsType)` override | implemented to log + stop (defensive; never fires for connectedDevice) | Only time-limited types call it (Q2.1); mirrors MetricsService precedent |
| MIUI/HyperOS handling | operational guidance + user toggle; NO OEM code | Q4: no programmatic/adb path; universal-app requirement |
| Sticky restart | `START_STICKY` with null-intent start re-arming the node | "Always there" goal; survives post-boot kills where the platform allows restarts |

### What the implementation files do (map to this research)

- `app/src/main/java/com/visioncamhealth/zyre/ZreService.kt` — the service:
  synchronous promotion, guarded channel creation (API 26+ — also fixes a
  latent unguarded `NotificationChannel` usage in the pre-existing
  MetricsService on API 24/25), typed promotion on 29+, sticky restart,
  pump thread owns native teardown (`signalStop`/`stop` separation), Curve
  keys from assets with plain-mode refusal.
- `app/src/main/java/com/visioncamhealth/receiver/BootReceiver.kt` —
  version-split start call + rejection catch; keeps the upstream
  activity-launch attempt (harmless no-op on modern platforms; A10+
  background-activity restrictions block it — kept for its original intent
  on whatever skins still honor it, and now protected with try/catch).
- `app/src/main/AndroidManifest.xml` — service registration with
  `foregroundServiceType="connectedDevice"`; `FOREGROUND_SERVICE_CONNECTED_DEVICE`,
  `CHANGE_WIFI_MULTICAST_STATE`, `ACCESS_LOCAL_NETWORK` permissions.
- `app/src/main/assets/zre/device.{priv,pub}` — device Curve keys staged
  from the operator key store; git-excluded (`.gitignore`) so the private
  key never lands in VCS. **Production note (open item):** per-install key
  provisioning / Keystore-backed keys, not baked assets.

---

## Unconfirmed / flagged items (nothing here is assumed)

1. Exact historical `startForeground` timeout per OS release — docs say "a
   few seconds"; current AOSP `main` shows a 30 s default; folk comments
   mention ~5 s on older builds. Not pinned per release.
2. Any watchdog that blocks BOOT_COMPLETED **senders/receivers** beyond the
   documented type restrictions — none found; only receiver-duration
   logging exists in AOSP.
3. Any adb-only (non-root) way to flip MIUI/HyperOS Autostart — none found;
   circulating scripts require root.
4. GSI/CTS-Verifier-specific BOOT_COMPLETED delivery differences for APIs
   34/35/37 — nothing documented either way.
5. HyperOS "auto-disables autostart over time for apps without an
   Accessibility service" — single-sourced (microG helper repo), plausible,
   uncorroborated.
6. Play-policy acceptance for an always-on zre beacon FGS under
   `connectedDevice` — the type's static requirements are met; review
   outcome is inherently opaque (policy page: see Q2.2).

---

## Open work items for humans (from this research)

1. [ ] Runtime `ACCESS_LOCAL_NETWORK` request in the onboarding/permission UI
       for Android 17 devices (or confirm the manager targets ≤ A16).
2. [ ] Production key provisioning (per-install; Keystore or first-run keygen)
       to replace the staged assets pair.
3. [ ] If a Xiaomi-class device is a first-class target: the user-facing
       Autostart prompt.deep-link + a "will it run at boot?" diagnostics row
       using the hidden-API state reader (Play-review trade-off documented in Q4.2).
4. [ ] Play Console FGS-type declaration for `connectedDevice` before any
       Play distribution.
5. [ ] Field validation on a real Android 15/16/17 device: confirm the boot
       start, confirm connectedDevice runs past the point where dataSync
       would hit `onTimeout`, and confirm beacon reachability.
