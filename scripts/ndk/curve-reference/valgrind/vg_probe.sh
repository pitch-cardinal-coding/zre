#!/usr/bin/env bash
# compile + valgrind the PRODUCTION topology probe (t_probe): the pump
# thread blocks in nextEvent(); a carrier thread signalStops; main
# behaves like the Kotlin pump (returns, joins the carrier, stops,
# exits). On the VM it runs from ~/vgcheck.
#
# Expected clean verdict (final zre_jni.c, 2026-10-20):
#   probe_end: nextEvent returned after ~2000 ms (need < 5000)
#   All heap blocks were freed -- no leaks are possible
#   ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)
set -e
cd ~/vgcheck
export VG_PUB=$(head -c40 device.pub)
export VG_SEC=$(head -c40 device.priv)
export PKG_CONFIG_PATH="$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:$LD_LIBRARY_PATH"
gcc -O2 -g -o t_probe t_probe.c zre_jni.c \
  $(pkg-config --cflags --libs libzyre) -lczmq -lzmq -lsodium -lpthread -I jni-include
valgrind --leak-check=full --show-leak-kinds=definite --error-exitcode=42 \
  ./t_probe > vg_probe.out 2>&1
