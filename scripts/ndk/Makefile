.PHONY: help push curve-keys curve-check curve-release curve-cross-repo-keys-check build build-abi log status verify pull launch-phone phone-log trial-beacon trial-gossip trial-dial clean \
        app-sync app-build app-wait app-log app-pull emu-install emu-release-install emu-launch emu-logcat emu-keys-stage emu-bootlog keys-xml

# Build host with Android SDK/NDK (override: make build BUILD_HOST=user@host)
# NOTE: every remote payload is wrapped in `bash -lc` because a login shell
# on this host may be fish, which rejects POSIX recipes (RE=..., \ line
# continuations). Keep that wrapper for any new target.
#
# Password-auth hosts: keep the password OUT of git. Create the ignored
# file scripts/ndk/lab.env with one line `BUILD_PASSWORD=...` (chmod 600),
# or export BUILD_PASSWORD in the environment, or use SSH keys.
-include lab.env
BUILD_HOST     ?= iam@10.0.89.26
BUILD_PASSWORD ?=

# Phone over wireless adb (serial as shown by `adb devices -l`), its IP,
# and this PC's IP as seen by the phone (gossip hub dial target)
PHONE_ADB      ?= adb-5TMB79S4GAPRUOHI-heNfnx._adb-tls-connect._tcp
PHONE_IP       ?= 10.0.89.13
HOST_IP        ?= 10.0.89.21
PEER_PORT      ?= 19870
HUB_PORT       ?= 19872

PYTHON ?= $(HOME)/devcode/.env/zre/bin/python3

SSH_OPTS := -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
SSH      := sshpass -p $(BUILD_PASSWORD) ssh $(SSH_OPTS) $(BUILD_HOST)
SCP      := sshpass -p $(BUILD_PASSWORD) scp $(SSH_OPTS)
# NOTE: no BUILD_HOST here — rsync appends the destination host itself;
# a second host argument lands inside the REMOTE command (observed:
# fish tried to exec "10.0.89.26").
RSYNC_SSH := sshpass -p $(BUILD_PASSWORD) ssh $(SSH_OPTS)
# The build host's login shell is fish: plain `rsync` resolves remote
# PATH issues only when invoked by absolute path (fish -c mangles the
# standard rsync/server bootstrap otherwise).
RSYNC_RSH := --rsync-path=/usr/bin/rsync -e "$(RSYNC_SSH)"

help:
	@echo "Static libzyre for Android (see README.md):"
	@echo "  make push            scp zc_peer.c + zre_static_all.sh to the build host"
	@echo "  make curve-keys      ensure <NAME>.priv/.pub (avedge format, Curve is always on)"
	@echo "  make curve-check     validate the pair (format + pub derives from priv)"
	@echo "  make curve-release   stage validated pair into dist/curve/ for the app"
	@echo "  make curve-cross-repo-keys-check   avedge toolchain cross-check (optional)"
	@echo "  make build           cross-build all ABIs on the build host"
	@echo "  make build-abi ABI=armeabi-v7a   build one ABI"
	@echo "  make log / status    tail remote build log / list artifacts"
	@echo "  make verify          readelf NEEDED check + artifact listing"
	@echo "  make pull            copy zc_peer-* into dist/"
	@echo "  make app-sync        rsync the app repo to the build host (SYNC_KEYFILES=0 to withhold signing keys)"
	@echo "  make app-build       assembleDebug+assembleRelease on the build host (detached)"
	@echo "  make app-wait        block until the build reports SUCCESS/FAILED"
	@echo "  make app-log         check the build log (expect BUILD SUCCESSFUL)"
	@echo "  make app-pull        pull built APKs into dist/app/"
	@echo "  make emu-install     install the debug APK on the emulator (EMU=emulator-5554)"
	@echo "  make emu-keys-stage  write the identity pair into the app's prefs, then relaunch (KEY_NAME=phone1)"
	@echo "  make emu-launch      open the app's MainActivity on the emulator"
	@echo "  make emu-logcat      the zre service's log lines"
	@echo "  make launch-phone ARGS_FILE='...' launch the phone peer via adb"
	@echo "  make phone-log       tail the phone's serial log"
	@echo "  make trial-beacon    phone <-> PC discovery + whisper + shout"
	@echo "  make trial-gossip    same via a ZGS hub hosted on this PC"
	@echo "  make trial-dial      direct TCP dial PC -> phone"
	@echo "  make clean           remove dist/"

# --- build host ------------------------------------------------------------

# --- Curve keys for the mesh (Curve is ALWAYS on) ---------------------------
# The APP never generates keys. Generation happens off-device (dashboard
# or these targets); the operator pastes the two 40-char Z85 strings into
# the app's settings sheet (Mesh discovery keys), and ZreService reads/
# stores that pair. dist/curve/ is the operator handoff material.

KEY_NAME ?= phone1
KEY_DIR  ?= $(HOME)/.avedge

curve-keys:
	@echo "Generating/ensuring a Curve pair for the Android app:"
	@echo "  name=$(KEY_NAME) dir=$(KEY_DIR) (avedge format: <name>.priv/<name>.pub, bare 40-char Z85)"
	$(PYTHON) curve_keys.py ensure --name $(KEY_NAME) --dir $(KEY_DIR)

curve-check:
	$(PYTHON) curve_keys.py check --name $(KEY_NAME) --dir $(KEY_DIR)

curve-release:
	$(PYTHON) curve_keys.py release --name $(KEY_NAME) --dir $(KEY_DIR) --out dist/curve
	@echo "Operator handoff (the app NEVER generates keys):"
	@echo "  - paste the pair text from dist/curve/<NAME>.{pub,priv} into the"
	@echo "    app's Settings -> Mesh discovery keys (see ANDROID_LIBZYRE.md 4.3)"
	@echo "  - future dashboards will serve the same two strings"
	@echo "  - <NAME>.pub is also what the PC/bind side uses (avedge: zcurve grant --name $(KEY_NAME))"

curve-cross-repo-keys-check:
	@echo "Cross-check against the operator toolchain (if present):"
	@if [ -x "$(HOME)/devcode/.env/avedge/bin/python3" ]; then \
	  cd $(HOME)/devcode/avedge && \
	  AVEDGE_KEY_DIR=$(KEY_DIR) $(HOME)/devcode/.env/avedge/bin/python3 -m src.zmqkit.zcurve check --name $(KEY_NAME) || exit 1; \
	elif [ -x "$(HOME)/.env/avedge/bin/python3" ]; then \
	  cd $(HOME)/devcode/avedge && \
	  AVEDGE_KEY_DIR=$(KEY_DIR) $(HOME)/.env/avedge/bin/python3 -m src.zmqkit.zcurve check --name $(KEY_NAME) || exit 1; \
	else echo "avedge env not found; skipping"; fi

push: curve-check
	@echo "Pushing sources to $(BUILD_HOST)..."
	$(SCP) ../../interop/zc_peer.c zre_static_all.sh host_verify.sh host_launch_phone.sh curve_keys.py $(BUILD_HOST):~/
	$(SSH) "bash -lc 'chmod +x ~/zre_static_all.sh ~/host_verify.sh ~/host_launch_phone.sh'"

build:
	@echo "Launching cross-build on $(BUILD_HOST)..."
	$(SSH) "bash -lc 'nohup bash ~/zre_static_all.sh > ~/zre_static_all.out 2>&1 < /dev/null & echo LAUNCHED'"

build-abi:
	@if [ -z "$(ABI)" ]; then echo "usage: make build-abi ABI=armeabi-v7a|arm64-v8a|x86_64"; exit 2; fi
	$(SSH) "bash -lc 'nohup bash ~/zre_static_all.sh $(ABI) > ~/zre_static_$(ABI).out 2>&1 < /dev/null & echo LAUNCHED'"

log:
	$(SSH) "bash -lc 'tail -n 20 ~/zre_static_all.out'"

status:
	$(SSH) "bash -lc 'tail -n 12 ~/zre_static_all.out; ls -la ~/zc-android/ 2>/dev/null'"

verify:
	$(SSH) "bash -lc '~/host_verify.sh'"

pull:
	@mkdir -p dist
	$(SCP) "$(BUILD_HOST):~/zc-android/zc_peer-*" dist/
	@ls -la dist/

# --- app (vision-cam-health) pipeline on the build host --------------------
#
# The workstation has no Android SDK; the build host has everything. These
# targets move the app source by rsync, launch gradle there (detached,
# self-terminating), then pull the APKs. The app's OWN Makefile (in the app
# repo) builds locally when that machine has the SDK+Studio.

APP_DIR       ?= $(HOME)/devcode/vision-cam-health
APP_HOST      ?= $(BUILD_HOST)
EMU           ?= emulator-5554
SYNC_KEYFILES ?= 1   # 0 = do NOT copy release.keystore / keystore.properties to the build host
ifeq ($(SYNC_KEYFILES),0)
APP_KEYOPT := --exclude=release.keystore --exclude=keystore.properties
else
APP_KEYOPT :=
endif

app-sync:
	@echo "Syncing $(APP_DIR) -> $(APP_HOST):$(APP_DIR)"
	$(SSH) "mkdir -p $(APP_DIR)"
	rsync -az --delete $(RSYNC_RSH) \
	  --exclude=.git --exclude=app/build --exclude=.gradle \
	  --exclude=local.properties --exclude=app/.cxx \
	  --filter='P release.keystore' --filter='P keystore.properties' \
	  $(APP_KEYOPT) \
	  $(APP_DIR)/ $(APP_HOST):$(APP_DIR)/
	@echo "(use 'make app-build' next)"

app-build:
	$(SSH) "bash -lc 'nohup env ZRE_ANDROID_PREFIX=\$$HOME/zre-android-build/prefix timeout 1500 bash -c \"cd $(APP_DIR) && ./gradlew --no-daemon :app:assembleDebug :app:assembleRelease\" > \$$HOME/zre-apk-build.log 2>&1 < /dev/null & echo APP_BUILD_LAUNCHED'"

app-wait: ## block until the detached app build reports SUCCESS/FAILED (max 5 min)
	@$(SSH) "bash -lc 'i=0; while [ \$$i -lt 60 ]; do if grep -qE \"BUILD (SUCCESSFUL|FAILED)\" \$$HOME/zre-apk-build.log 2>/dev/null; then grep -E \"BUILD (SUCCESSFUL|FAILED)\" \$$HOME/zre-apk-build.log | head -1; exit 0; fi; i=\$$((i+1)); sleep 5; done; echo \"APP BUILD TIMEOUT\"; exit 1'"

app-log:
	@$(SSH) "bash -lc 'grep -E \"BUILD (SUCCESSFUL|FAILED)\" \$$HOME/zre-apk-build.log | head -1; tail -n 5 \$$HOME/zre-apk-build.log'"
	@$(SSH) "bash -lc 'grep -q \"BUILD SUCCESSFUL\" \$$HOME/zre-apk-build.log'" || \
		{ echo "APP BUILD FAILED (see the log above)"; exit 1; }

app-pull:
	@mkdir -p dist/app
	$(SCP) "$(APP_HOST):$(APP_DIR)/app/build/outputs/apk/debug/app-debug.apk" "$$HOME/zre-apk-build.log" dist/app/ 2>/dev/null || true
	$(SCP) "$(APP_HOST):$(APP_DIR)/app/build/outputs/apk/release/app-release.apk" dist/app/ 2>/dev/null || true
	@ls -la dist/app/

emu-install:   ## install the pulled debug APK on the emulator (EMU=emulator-5554)
	$(SSH) "bash -lc 'export PATH=\$$HOME/Android/Sdk/platform-tools:\$$PATH; adb -s $(EMU) install -r -t $(APP_DIR)/app/build/outputs/apk/debug/app-debug.apk | tail -1'"

emu-release-install:
	$(SSH) "bash -lc 'export PATH=\$$HOME/Android/Sdk/platform-tools:\$$PATH; adb -s $(EMU) install -r -t $(APP_DIR)/app/build/outputs/apk/release/app-release.apk | tail -1'"

emu-launch:    ## open MainActivity on the emulator
	$(SSH) "bash -lc 'export PATH=\$$HOME/Android/Sdk/platform-tools:\$$PATH; adb -s $(EMU) shell am start -n com.visioncamhealth/.MainActivity'"

emu-logcat:    ## the zre service's log lines
	$(SSH) "bash -lc 'export PATH=\$$HOME/Android/Sdk/platform-tools:\$$PATH; adb -s $(EMU) logcat -d -s visioncamhealth-Zre:I | tail -20'"

emu-keys-stage: ## feed the app's identity prefs on the emulator, then relaunch (KEY_NAME=phone1|phone2; stand-in for the keys being pasted in settings)
	$(PYTHON) make_identity_xml.py --name $(KEY_NAME) --dir $(KEY_DIR) --out /tmp/zre_identity_feed.xml
	$(SCP) /tmp/zre_identity_feed.xml zre_feed.sh $(APP_HOST):/tmp/
	$(SSH) "bash -lc 'export PATH=\$$HOME/Android/Sdk/platform-tools:\$$PATH; adb -s $(EMU) push /tmp/zre_feed.sh /tmp/zre_identity_feed.xml /data/local/tmp/ >/dev/null; adb -s $(EMU) shell run-as com.visioncamhealth sh /data/local/tmp/zre_feed.sh zre_identity_feed.xml; adb -s $(EMU) shell am force-stop com.visioncamhealth; adb -s $(EMU) shell am start -n com.visioncamhealth/.MainActivity'"

keys-xml: ## write dist/curve/$(KEY_NAME).xml — the identity file format the dashboard/settings paste flow consumes
	@mkdir -p dist/curve
	$(PYTHON) make_identity_xml.py --name $(KEY_NAME) --dir $(KEY_DIR) --out dist/curve/$(KEY_NAME).xml

emu-bootlog:
	$(SSH) "bash -lc 'export PATH=\$$HOME/Android/Sdk/platform-tools:\$$PATH; adb -s $(EMU) logcat -d -s visioncamhealth-Zre:I | grep -E \"service start|refusing|generated|rekey|node up\" | tail -8'"


# --- phone trials (run phone_trial.py locally against the phone) -----------
#
# Launch pattern: the phone peer runs as a foreground adb shell wrapped in
# nohup on the build host (backgrounding inside `adb shell` hangs the
# session; see README "Known device constraints").

launch-phone:
	@echo "usage: make launch-phone ARGS_FILE='<file with one zc_peer command line>'"
	@test -f "$(ARGS_FILE)"
	base64 -w0 < "$(ARGS_FILE)" | $(SSH) "bash -lc '~/host_launch_phone.sh $(PHONE_ADB) --b64-stdin'"

phone-log:
	$(SSH) "bash -lc 'tail -n 25 ~/phone_adb.out'"

trial-beacon:
	@$(PYTHON) phone_trial.py build-args --trial-mode beacon --avedge phone1 --phone-ip $(PHONE_IP) --args-file args.txt
	@$(MAKE) launch-phone ARGS_FILE=args.txt
	@sleep 4
	$(PYTHON) phone_trial.py beacon --avedge --phone-ip $(PHONE_IP) --wait 40

trial-gossip:
	@$(PYTHON) phone_trial.py build-args --trial-mode gossip --avedge phone1 --phone-ip $(PHONE_IP) --host-ip $(HOST_IP) --peer-port $(PEER_PORT) --hub-port $(HUB_PORT) --args-file args.txt
	@$(MAKE) launch-phone ARGS_FILE=args.txt
	@sleep 2
	$(PYTHON) phone_trial.py gossip --avedge --hub-port $(HUB_PORT) --wait 40
	@$(MAKE) phone-log

trial-dial:
	@echo "NOTE: dial binds the phone's fixed inbox without beacons; use"; \
	echo "--endpoint (see README). trial-gossip covers the endpoint bind."
	@$(PYTHON) phone_trial.py build-args --trial-mode dial --avedge phone1 --phone-ip $(PHONE_IP) --peer-port $(PEER_PORT) --args-file args.txt
	@$(MAKE) launch-phone ARGS_FILE=args.txt
	@sleep 4
	$(PYTHON) phone_trial.py dial --avedge --phone-ip $(PHONE_IP) --peer-port $(PEER_PORT) --wait 40

clean:
	rm -rf dist args.txt
