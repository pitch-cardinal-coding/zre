#!/usr/bin/env bash
# compile + run valgrind on the zre_jni lifecycle harness (VM side).
# Canonical home: zre/scripts/ndk/valgrind-harness/. On the VM it runs
# from ~/vgcheck with the pinned libzyre/czmq/zmq/sodium in ~/.local.
#
# Expected clean verdict (final zre_jni.c, 2026-10-20):
#   VG-HARNESS DONE events=1
#   All heap blocks were freed -- no leaks are possible
#   ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
set -e
cd ~/vgcheck
export VG_PUB=$(head -c40 device.pub)
export VG_SEC=$(head -c40 device.priv)
export PKG_CONFIG_PATH="$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:$LD_LIBRARY_PATH"
INC=~/vgcheck/jni-include
gcc -O2 -g -o probe_env probe_env.c -I"$INC"
./probe_env
gcc -O2 -g -o test_zrejni test_zrejni.c zre_jni.c \
  $(pkg-config --cflags --libs libzyre) -lczmq -lzmq -lsodium -lpthread -I"$INC"
# ZRE teardown contract: a single-threaded caller must stop() BEFORE
# draining; stop() destroys the pipe, so the next nextEvent returns
# "{}" (the pinned zyre does NOT unblock recv on zyre_stop alone).
# The blocking-recv + cross-thread signalStop topology is vg_probe.sh.
valgrind --leak-check=full --show-leak-kinds=definite \
  --errors-for-leak-kinds=definite --error-exitcode=42 \
  ./test_zrejni > vg.out 2>&1
rc=$?
grep -E "VG-HARNESS|leaks are possible|definitely lost|ERROR SUMMARY" vg.out
exit $rc
