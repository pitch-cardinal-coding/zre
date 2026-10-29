#!/usr/bin/env bash
# Launch the phone peer, run ON the build host (make push ships it to ~).
# Usage:
#   ~/host_launch_phone.sh <adb serial> <zc_peer args...>   (plain argv)
#   ... | ~/host_launch_phone.sh <adb serial> --b64-stdin   (base64 args)
#
# The peer runs as a FOREGROUND adb shell wrapped in a host-side nohup:
# backgrounding inside `adb shell` keeps the ssh session attached forever.
#
# The base64 path exists because z85 Curve keys contain shell
# metacharacters — including '$', '(' and '[' — that make, bash and the
# phone's mksh all try to interpret if the args ever appear unquoted.
# Encoding once at the sender and decoding here sidesteps every hop.
export PATH="$HOME/Android/Sdk/platform-tools:$PATH"
serial="$1"
shift
mode="${1:-}"
if [ "$mode" = "--b64-stdin" ]; then
	shift
	IFS=' ' read -ra argv <<< "$(base64 -d)"
else
	argv=("$@")
fi
adb -s "$serial" shell pkill zc_peer 2>/dev/null
sleep 1
# adb re-joins argv into one string for the device's mksh, so every element
# must be single-quoted again or z85 keys / other metacharacters break the
# phone-side parse. None of zc_peer's arguments contain a single quote.
nohup adb -s "$serial" shell "$(printf "'%s' " "${argv[@]}")" > "$HOME/phone_adb.out" 2>&1 < /dev/null &
echo PHONE_LAUNCHED
