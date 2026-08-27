#!/usr/bin/env bash
# py-spy-watch-tests.sh — Watch zre test processes during pytest runs.
# Monitors RSS memory and captures py-spy stack dumps for memory/performance analysis.
#
# Usage:
#   ./py-spy-watch-tests.sh [interval_s] [out_file] [dump_interval_s]
#   (defaults: 0.25 s, /tmp/zre-py-spy-watch.log, 60 s)

set -u

INTERVAL="${1:-0.25}"
OUT="${2:-/tmp/zre-py-spy-watch.log}"
DUMP_EVERY="${3:-60}"
PYSPY="${PYSPY:-$(dirname "$(command -v python3)")/py-spy}"

SUDO=()
if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    SUDO=(sudo -n env "PATH=$PATH")
else
    echo "WARN: passwordless sudo unavailable — logging RSS only (dumps skipped)." >&2
fi

declare -A FIRST_RSS LAST_DUMP

echo "=== zre-py-spy-watch started $(date -Iseconds) interval=${INTERVAL}s dump_every=${DUMP_EVERY}s ===" > "$OUT"

find_pid() {
    local pattern="$1" pid
    # Match any process containing pattern; prefer python but accept any
    for pid in $(pgrep -f "$pattern" 2>/dev/null); do
        # Verify pid exists and has VmRSS
        if [ -f "/proc/$pid/status" ]; then
            echo "$pid"
            return
        fi
    done
}

rss_kb() {
    awk '/^VmRSS:/ {print $2}' "/proc/$1/status" 2>/dev/null
}

log_rss_line() {
    local label="$1" pid="$2" rss first delta sign=""
    rss=$(rss_kb "$pid")
    if [ -z "$rss" ]; then
        echo "$(date +%H:%M:%S) $label pid=$pid RSS unavailable"
        return
    fi
    first=${FIRST_RSS[$label]:-}
    if [ -z "$first" ]; then
        FIRST_RSS[$label]=$rss
        first=$rss
    fi
    delta=$((rss - first))
    [ "$delta" -ge 0 ] && sign="+"
    echo "$(date +%H:%M:%S) $label pid=$pid rss=${rss}kB (${sign}${delta} kB since start)"
}

maybe_dump() {
    local label="$1" pid="$2" now
    [ ${#SUDO[@]} -gt 0 ] || return 0
    if [ -z "$PYSPY" ] || [ ! -x "$PYSPY" ]; then
        return 0
    fi
    now=$(date +%s)
    if [ -z "${LAST_DUMP[$label]:-}" ] || (( now - LAST_DUMP[$label] >= DUMP_EVERY )); then
        {
            echo "--- dump $label pid=$pid $(date +%H:%M:%S) ---"
            "${SUDO[@]}" "$PYSPY" dump --pid "$pid" 2>&1
        } >> "$OUT"
        LAST_DUMP[$label]=$now
    fi
}

# Patterns to match zre test processes — include pytest itself
PATTERNS=("test_zre" "test_zyre" "pytest" "zre" "node.py")

while true; do
    found_any=0
    for pattern in "${PATTERNS[@]}"; do
        PID=$(find_pid "$pattern")
        if [ -n "$PID" ]; then
            found_any=1
            log_rss_line "$pattern" "$PID" >> "$OUT"
            maybe_dump "$pattern" "$PID"
        fi
    done
    if [ "$found_any" -eq 0 ]; then
        echo "----- $(date +%H:%M:%S) no watched processes found, waiting... -----" >> "$OUT"
    fi
    sleep "$INTERVAL"
done
