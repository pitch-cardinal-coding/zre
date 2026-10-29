#!/usr/bin/env bash
# Static-link verification, run ON the build host (make push ships it to ~).
# Expectation: every zc_peer NEEDED list is bionic system libs only.
RE="$HOME/Android/Sdk/ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
for b in ~/zc-android/zc_peer-*; do
	echo "== $b"
	"$RE" -d "$b" | grep NEEDED
done
ls ~/zre-android-build/prefix/*/lib/*.a
