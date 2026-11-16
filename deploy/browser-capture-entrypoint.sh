#!/bin/sh
# Fetch-worker supervisor: browser controls remain inside the container network.
set -eu
umask 077
: "${BROWSER_CONTROL_TOKEN_FILE:?BROWSER_CONTROL_TOKEN_FILE is required}"
export DISPLAY="${DISPLAY:-:99}"
export BROWSER_CHROMIUM_PATH="${BROWSER_CHROMIUM_PATH:-/usr/bin/chromium}"
# A lock left by an unclean stop keeps the display from starting, which stops
# the capture worker in a restart loop. Nothing can hold this display yet in a
# container that has only just started, so the leftovers are safe to remove.
display_number="${DISPLAY#*:}"
display_number="${display_number%%.*}"
case "$display_number" in
    ''|*[!0-9]*) ;;
    *) rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}" ;;
esac
Xvfb "$DISPLAY" -screen 0 1365x1024x24 -nolisten tcp -ac >/tmp/leakscope-xvfb.log 2>&1 &
xvfb_pid=$!
node /opt/leakscope-browser/server.mjs &
browser_pid=$!
/app/entrypoint.sh "$@" &
worker_pid=$!
stop() {
    trap - TERM INT EXIT
    kill -TERM "$worker_pid" "$browser_pid" "$xvfb_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
    wait "$xvfb_pid" 2>/dev/null || true
}
trap 'stop; exit 143' TERM INT
trap stop EXIT
while kill -0 "$worker_pid" 2>/dev/null; do
    if ! kill -0 "$browser_pid" 2>/dev/null || ! kill -0 "$xvfb_pid" 2>/dev/null; then
        echo 'LeakScope browser controller or display exited; stopping capture worker' >&2
        exit 1
    fi
    sleep 2
done
wait "$worker_pid"
