#!/bin/bash
# Starts and stops the live Trading Desk for the Dock app (tools/make_app.sh).
#   desk-control.sh start   -> prints the port the desk is on (starts it if needed)
#   desk-control.sh stop    -> stops the desk this app started
#   desk-control.sh status  -> prints the port, or nothing
# TD_DEMO=1 runs the demo instead (used by the self-test; never by the app).
set -u
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
PY=/usr/bin/python3
DATA="$HERE/data"
PIDFILE="$DATA/desk.pid"
PORTFILE="$DATA/desk.port"
LOG="$DATA/desk.log"
WANT='"demo": false'
EXTRA=""
if [ "${TD_DEMO:-0}" = "1" ]; then WANT='"demo": true'; EXTRA="--demo"; fi
mkdir -p "$DATA"

answers() { /usr/bin/curl -s -m 2 "http://127.0.0.1:$1/api/state" 2>/dev/null | /usr/bin/grep -q "$WANT"; }
busy() { /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  status)
    if alive && answers "$(cat "$PORTFILE" 2>/dev/null)"; then cat "$PORTFILE"; fi
    ;;
  start)
    # already running (from this app, or from start.command on a known port)?
    if alive && answers "$(cat "$PORTFILE" 2>/dev/null)"; then cat "$PORTFILE"; exit 0; fi
    for p in 8787 8788 8789 8790 8791; do
      if answers "$p"; then echo "$p"; exit 0; fi
    done
    PORT=""
    for p in 8787 8788 8789 8790 8791; do
      if ! busy "$p"; then PORT=$p; break; fi
    done
    if [ -z "$PORT" ]; then echo "Ports 8787 to 8791 are all in use. Close another local server and try again." >&2; exit 1; fi
    cd "$HERE" || exit 1
    echo "---- $(date '+%Y-%m-%d %H:%M:%S') starting on port $PORT" >>"$LOG"
    nohup "$PY" -m desk --no-browser --port "$PORT" $EXTRA </dev/null >>"$LOG" 2>&1 &
    echo $! >"$PIDFILE"
    echo "$PORT" >"$PORTFILE"
    for _ in $(seq 1 60); do
      if answers "$PORT"; then echo "$PORT"; exit 0; fi
      alive || break
      sleep 0.25
    done
    echo "The desk did not start. The last lines of data/desk.log say: $(tail -n 3 "$LOG" | tr '\n' ' ')" >&2
    exit 1
    ;;
  stop)
    if alive; then
      PID="$(cat "$PIDFILE")"
      kill "$PID" 2>/dev/null
      for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.25; done
      kill -9 "$PID" 2>/dev/null
    fi
    rm -f "$PIDFILE" "$PORTFILE"
    ;;
  *)
    echo "usage: desk-control.sh start|stop|status" >&2
    exit 2
    ;;
esac
