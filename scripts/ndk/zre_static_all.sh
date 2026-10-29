#!/usr/bin/env bash
# Static cross-build of libzmq, czmq, zyre and zc_peer for Android (NDK).
#
# Run this ON a build host with the Android NDK + SDK cmake/ninja.
# Produces static archives (libsodium.a, libzmq.a, libczmq.a, libzyre.a)
# under $PFXROOT/prefix/<abi>/lib and a static zc_peer per ABI under
# $HOME/zc-android/. Verified with NDK r30 (30.0.16248370), API 24,
# libzmq @4649337, czmq @b669c7e, zyre @f716769 (v2.0.1-96), libsodium
# 1.0.20-stable. See README.md next to this script.
#
# Usage: zre_static_all.sh [abi...]     default: all three ABIs
# Static cross-build: libsodium -> libzmq -> czmq -> zyre -> zc_peer for Android NDK.
# Usage: zre_static_all.sh [abi...]   default: armeabi-v7a arm64-v8a x86_64
set -u
export ANDROID_NDK_ROOT="$HOME/Android/Sdk/ndk/30.0.16248370"
export ANDROID_SDK_ROOT="$HOME/Android/Sdk"
export PATH="$HOME/Android/Sdk/cmake/4.1.2/bin:$PATH"
CMAKE="$HOME/Android/Sdk/cmake/4.1.2/bin/cmake"
TOOLCHAIN="$ANDROID_NDK_ROOT/build/cmake/android.toolchain.cmake"
PREBUILT="$ANDROID_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin"
DEPS="$HOME/zre-android-deps"
PFXROOT="$HOME/zre-android-build"
OUT="$HOME/zc-android"
mkdir -p "$OUT"
log() { echo "[$(date +%T)] $*"; }

# --- sources (idempotent, PINNED) ---
# A fresh host (or a cleaned ~/zre-android-deps) must reproduce the SAME
# commits the verified build used — never whatever HEAD happens to be.
# Full clones + explicit checkout; the pins are the verified set in
# README.md.
mkdir -p "$DEPS"

pin() { # pin <repo-dir> <full-sha> <label>
  if ! git -C "$1" rev-parse --verify --quiet "$2^{commit}" >/dev/null; then
    git -C "$1" fetch --quiet origin "$2" 2>/dev/null ||
      git -C "$1" fetch --quiet --tags origin || return 1
  fi
  git -C "$1" checkout --quiet "$2" || return 1
  log "pinned $3 @$(git -C "$1" rev-parse --short HEAD)"
}

if [ ! -d "$DEPS/libsodium-src" ]; then
  wget -q -O "$DEPS/libsodium.tar.gz" \
    https://download.libsodium.org/libsodium/releases/libsodium-1.0.20-stable.tar.gz
  tar -xzf "$DEPS/libsodium.tar.gz" -C "$DEPS"
  for d in "$DEPS/libsodium-stable" "$DEPS/libsodium-1.0.20-stable" "$DEPS/libsodium-1.0.20"; do
    [ -d "$d" ] && mv "$d" "$DEPS/libsodium-src" && break
  done
fi
[ -d "$DEPS/libzmq" ] || git clone --quiet https://github.com/zeromq/libzmq.git "$DEPS/libzmq"
[ -d "$DEPS/czmq" ]   || git clone --quiet https://github.com/zeromq/czmq.git "$DEPS/czmq"
[ -d "$HOME/zyre" ]   || git clone --quiet https://github.com/zeromq/zyre.git "$HOME/zyre"
pin "$DEPS/libzmq" 46493370217ac135246617fa2f6ac819d8b61bfc libzmq || exit 1
pin "$DEPS/czmq"   b669c7eb60cb8f587071ac6ece7cd68d30ac4017 czmq   || exit 1
pin "$HOME/zyre"   f7167698e27416c896b8cf522db9ecb6a3616ca1 zyre   || exit 1

# curve_keygen fails in static-only android builds (zmq.h not staged yet) -> remove target
grep -q "curve_keygen removed" "$DEPS/libzmq/CMakeLists.txt" || DEPS="$DEPS" python3 -c '
import os
p = os.path.join(os.environ["DEPS"], "libzmq", "CMakeLists.txt")
s = open(p).read()
old = """if (ENABLE_CURVE)
  add_executable(curve_keygen tools/curve_keygen.cpp)
  target_link_libraries(curve_keygen libzmq)
  install(TARGETS curve_keygen RUNTIME DESTINATION bin)
endif()"""
assert old in s, "curve_keygen block not found"
open(p, "w").write(s.replace(old, "# curve_keygen removed: fails in static-only android builds (zmq.h not staged yet)"))
'

# NDK has no libstdc++; static consumers must use libc++_static.
fix_pc() {
  sed -i 's/-lstdc++/-lc++_static -lc++abi/g' "$PFX/lib/pkgconfig/"*.pc 2>/dev/null || :
}

# Android 11+ blocks RTM_GETLINK netlink for unprivileged processes
# (developer.android.com: getifaddrs()/RTM_GETLINK impacted; bionic
# getifaddrs then fails -> czmq zbeacon finds no interface). Force
# czmq's pure-ioctl interface loader (SIOCGIF*, no netlink) on Android.
grep -q "HAVE_GETIFADDRS) && !defined (__ANDROID__)" "$DEPS/czmq/src/ziflist.c" || \
  sed -i 's/#if defined (HAVE_GETIFADDRS)/#if defined (HAVE_GETIFADDRS) \&\& !defined (__ANDROID__)/' \
    "$DEPS/czmq/src/ziflist.c"

build_one() {
  local ARCH="$1"
  local COMP SOD_WRAPPER SOD_NAME
  case "$ARCH" in
    armeabi-v7a) COMP=armv7a-linux-androideabi24; SOD_WRAPPER=android-armv7-a.sh; SOD_NAME="libsodium-android-armv7-a" ;;
    arm64-v8a)   COMP=aarch64-linux-android24;    SOD_WRAPPER=android-armv8-a.sh; SOD_NAME="libsodium-android-armv8-a+crypto" ;;
    x86_64)      COMP=x86_64-linux-android24;     SOD_WRAPPER=android-x86_64.sh;  SOD_NAME="libsodium-android-westmere" ;;
    *) echo "bad arch $ARCH"; return 1 ;;
  esac
  local PFX="$PFXROOT/prefix/$ARCH"
  rm -rf "$PFX"; mkdir -p "$PFX"
  export PKG_CONFIG_PATH="$PFX/lib/pkgconfig"
  export PKG_CONFIG_LIBDIR="$PFX/lib/pkgconfig"
  unset PKG_CONFIG_SYSROOT_DIR || :

  # 1) libsodium - official dist-build script, full build, API 24 (staged once per ABI)
  local SOD_STAGE="$DEPS/libsodium-staged-$ARCH"
  if [ ! -f "$SOD_STAGE/lib/libsodium.a" ]; then
    log "$ARCH: libsodium (official $SOD_WRAPPER)"
    rm -rf "$DEPS/libsodium-src/$SOD_NAME"
    ( cd "$DEPS/libsodium-src" \
        && env ANDROID_NDK_HOME="$ANDROID_NDK_ROOT" NDK_PLATFORM=android-24 \
           LIBSODIUM_FULL_BUILD=1 sh "dist-build/$SOD_WRAPPER" \
           > "$HOME/libsodium-official-$ARCH.log" 2>&1 \
    ) || { log "$ARCH libsodium FAILED (~/libsodium-official-$ARCH.log)"; return 1; }
    local SOD_OUT="$DEPS/libsodium-src/$SOD_NAME"
    [ -f "$SOD_OUT/lib/libsodium.a" ] || { log "$ARCH: no libsodium.a in $SOD_OUT"; return 1; }
    rm -rf "$SOD_STAGE"
    mkdir -p "$SOD_STAGE"
    cp -r "$SOD_OUT/include" "$SOD_OUT/lib" "$SOD_STAGE/"
    log "$ARCH: libsodium staged"
  fi
  mkdir -p "$PFX/include" "$PFX/lib/pkgconfig"
  cp -r "$SOD_STAGE"/include/* "$PFX/include/"
  cp "$SOD_STAGE"/lib/libsodium.a "$PFX/lib/"
  cp "$SOD_STAGE"/lib/pkgconfig/*.pc "$PFX/lib/pkgconfig/" 2>/dev/null || :

  # 2) libzmq (static only, CURVE via libsodium)
  log "$ARCH: libzmq configure"
  "$CMAKE" -S "$DEPS/libzmq" -B "$PFXROOT/libzmq-$ARCH" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PFX" -DCMAKE_PREFIX_PATH="$PFX" \
    -DANDROID_ABI="$ARCH" -DANDROID_PLATFORM=android-24 \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH \
    -DBUILD_SHARED=OFF -DBUILD_STATIC=ON \
    -DWITH_LIBSODIUM=ON -DENABLE_CURVE=ON -DENABLE_WS=OFF \
    -DBUILD_TESTS=OFF -DBUILD_DOCS=OFF -DWITH_DOCS=OFF \
    -DWITH_PERF_TOOL=OFF -DENABLE_PRECOMPILED=OFF \
    -DCMAKE_EXE_LINKER_FLAGS="-L$PFX/lib" \
    -DCMAKE_C_STANDARD_LIBRARIES="-llog -lsodium -lc++_static -lc++abi" \
    || return 1
  log "$ARCH: libzmq build+install"
  "$CMAKE" --build "$PFXROOT/libzmq-$ARCH" --target install -j 8 || return 1
  fix_pc

  # 3) czmq (static only, drafts for zauth/zactor)
  log "$ARCH: czmq configure"
  "$CMAKE" -S "$DEPS/czmq" -B "$PFXROOT/czmq-$ARCH" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PFX" -DCMAKE_PREFIX_PATH="$PFX" \
    -DANDROID_ABI="$ARCH" -DANDROID_PLATFORM=android-24 \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH \
    -DCZMQ_BUILD_SHARED=OFF -DCZMQ_BUILD_STATIC=ON \
    -DENABLE_DRAFTS=ON \
    -DCMAKE_EXE_LINKER_FLAGS="-L$PFX/lib" \
    -DCMAKE_C_STANDARD_LIBRARIES="-llog -lsodium -lc++_static -lc++abi" \
    || return 1
  log "$ARCH: czmq build+install"
  "$CMAKE" --build "$PFXROOT/czmq-$ARCH" --target install -j 8 || return 1
  fix_pc

  # 4) zyre (static only; zyre sources need the czmq draft define for zauth etc.)
  log "$ARCH: zyre configure"
  "$CMAKE" -S "$HOME/zyre" -B "$PFXROOT/zyre-$ARCH" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PFX" -DCMAKE_PREFIX_PATH="$PFX" \
    -DANDROID_ABI="$ARCH" -DANDROID_PLATFORM=android-24 \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH \
    -DZYRE_BUILD_SHARED=OFF -DZYRE_BUILD_STATIC=ON \
    -DCMAKE_C_FLAGS="-DCZMQ_BUILD_DRAFT_API" \
    -DCMAKE_EXE_LINKER_FLAGS="-L$PFX/lib" \
    -DCMAKE_C_STANDARD_LIBRARIES="-llog -lsodium -lc++_static -lc++abi" \
    || return 1
  log "$ARCH: zyre build+install"
  "$CMAKE" --build "$PFXROOT/zyre-$ARCH" --target install -j 8 || return 1
  fix_pc

  # 5) zc_peer (static PIE, hardened)
  log "$ARCH: zc_peer"
  "$PREBUILT/$COMP-clang" -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE \
    -DZYRE_BUILD_DRAFT_API -DCZMQ_BUILD_DRAFT_API \
    -Wall -Wextra -Wformat=2 -Wformat-security -Werror \
    -I"$PFX/include" -o "$OUT/zc_peer-$ARCH" "$HOME/zc_peer.c" \
    -L"$PFX/lib" -Wl,-z,relro,-z,now \
    -lzyre -lczmq -lzmq -lsodium -lc++_static -lc++abi -lc -ldl -lm -llog -pie || return 1
  "$PREBUILT/llvm-strip" "$OUT/zc_peer-$ARCH"
  log "$ARCH: NEEDED:"
  "$PREBUILT/llvm-readelf" -d "$OUT/zc_peer-$ARCH" | grep NEEDED || true
  ls -la "$OUT/zc_peer-$ARCH"
}

RC=0
TARGETS="${*:-armeabi-v7a arm64-v8a x86_64}"
for A in $TARGETS; do
  if build_one "$A"; then log "$A OK"; else log "$A FAIL"; RC=1; fi
done
log "ALL_DONE RC=$RC"
exit $RC
