#!/system/bin/sh
# Emulator-side helper for the settings-flow validation: write the
# zre_identity prefs the same way the app's settings sheet does, from
# the pushed XML. Canonical home: zre/scripts/ndk/zre_feed.sh. Run (build host):
#   adb push zre_feed.sh /data/local/tmp/
#   adb shell run-as com.visioncamhealth sh /data/local/tmp/zre_feed.sh <xmlfile>
set -e
cd /data/data/com.visioncamhealth
cp "/data/local/tmp/$1" shared_prefs_"$1.xml" 2>/dev/null || true
mkdir -p shared_prefs
cp "/data/local/tmp/$1" shared_prefs/zre_identity.xml
wc -c shared_prefs/zre_identity.xml
