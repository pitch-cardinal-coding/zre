#!/usr/bin/env bash
# run_tests_with_monitor.sh — Run zre tests with memory/performance monitoring
#
# Usage:
#   ./run_tests_with_monitor.sh [pytest_args...]
#
# This starts the py-spy monitor in the background, runs pytest, then stops the monitor.

set -euo pipefail

MONITOR_SCRIPT="$(dirname "$0")/py-spy-watch-tests.sh"
MONITOR_LOG="/tmp/zre-py-spy-watch.log"
MONITOR_PID=""

# Resolve python3 from the active environment (override via PYTHON_BIN=...).
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
if [ ! -x "$PYTHON_BIN" ]; then
    echo "python3 not found in PATH" >&2
    exit 1
fi

# Use py-spy from the same environment as the test interpreter, not a system install.
PYSPY="$(dirname "$PYTHON_BIN")/py-spy"
if [ ! -x "$PYSPY" ]; then
    echo "WARN: py-spy not found next to python3 ($PYSPY); dumps skipped, RSS still logged" >&2
    PYSPY=""
fi
export PYSPY

cleanup() {
    if [ -n "$MONITOR_PID" ] && kill -0 "$MONITOR_PID" 2>/dev/null; then
        echo "Stopping monitor (PID: $MONITOR_PID)..."
        kill "$MONITOR_PID" 2>/dev/null || true
        wait "$MONITOR_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

echo "=== Starting zre test monitor ==="
"$MONITOR_SCRIPT" 0.25 "$MONITOR_LOG" 30 &
MONITOR_PID=$!
echo "Monitor started (PID: $MONITOR_PID), logging to $MONITOR_LOG"

# Give monitor time to start
sleep 1

echo "=== Running zre tests ==="
cd "$(dirname "$0")"
"$PYTHON_BIN" -m pytest tests/ -v "$@"
TEST_RESULT=$?

echo "=== Tests completed with exit code $TEST_RESULT ==="
echo "Monitor log available at: $MONITOR_LOG"

# Show summary of monitor log
if [ -f "$MONITOR_LOG" ]; then
    echo "=== Monitor log summary ==="
    tail -20 "$MONITOR_LOG" || true
fi

exit $TEST_RESULT
