#!/bin/sh
set -e

# Unified service entrypoint:
# - Start Joern HTTP server (internal port)
# - Wait until Joern accepts /query-sync
# - Start HTTP proxy
# - Supervise: if Joern exits, restart Joern + proxy (bounded)

JOERN_BIN="/opt/joern/joern-cli/joern"

JOERN_SERVER_HOST="${JOERN_SERVER_HOST:-127.0.0.0}"
JOERN_INTERNAL_PORT="${JOERN_INTERNAL_PORT:-${JOERN_SERVER_PORT:-18080}}"
JOERN_INTERNAL_HOST="${JOERN_INTERNAL_HOST:-127.0.0.1}"

PROXY_PORT="${PROXY_PORT:-${JOERN_PUBLISH_PORT:-8080}}"

XMX="${JOERN_JAVA_XMX:-8g}"
JOERN_READY_TIMEOUT_SEC="${JOERN_READY_TIMEOUT_SEC:-120}"
JOERN_MAX_RESTARTS="${JOERN_MAX_RESTARTS:-10}"
JOERN_RESTART_DELAY_SEC="${JOERN_RESTART_DELAY_SEC:-3}"

JOERN_IMPORT_SC="${JOERN_IMPORT_SC:-}"

JOERN_PID=""
PROXY_PID=""
WATCHDOG_PID=""
JOERN_RESTART_COUNT=0

JOERN_WATCHDOG_INTERVAL_SEC="${JOERN_WATCHDOG_INTERVAL_SEC:-5}"
JOERN_WATCHDOG_FAIL_THRESHOLD="${JOERN_WATCHDOG_FAIL_THRESHOLD:-3}"

if [ ! -x "$JOERN_BIN" ]; then
  echo "unified-entrypoint: joern binary not found at $JOERN_BIN" >&2
  exit 1
fi

echo "unified-entrypoint: JOERN host=$JOERN_SERVER_HOST internal_port=$JOERN_INTERNAL_PORT XMX=$XMX" >&2
echo "unified-entrypoint: proxy_port=$PROXY_PORT" >&2

if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
  export JOERN_AUTH_USERNAME="$JOERN_SERVER_AUTH_USERNAME"
  export JOERN_AUTH_PASSWORD="$JOERN_SERVER_AUTH_PASSWORD"
fi

stop_proxy() {
  if [ -n "$PROXY_PID" ] && kill -0 "$PROXY_PID" >/dev/null 2>&1; then
    kill "$PROXY_PID" >/dev/null 2>&1 || true
    wait "$PROXY_PID" >/dev/null 2>&1 || true
  fi
  PROXY_PID=""
}

stop_joern() {
  if [ -n "$JOERN_PID" ] && kill -0 "$JOERN_PID" >/dev/null 2>&1; then
    kill "$JOERN_PID" >/dev/null 2>&1 || true
    wait "$JOERN_PID" >/dev/null 2>&1 || true
  fi
  JOERN_PID=""
}

stop_watchdog() {
  if [ -n "$WATCHDOG_PID" ] && kill -0 "$WATCHDOG_PID" >/dev/null 2>&1; then
    kill "$WATCHDOG_PID" >/dev/null 2>&1 || true
    wait "$WATCHDOG_PID" >/dev/null 2>&1 || true
  fi
  WATCHDOG_PID=""
}

cleanup() {
  stop_watchdog
  stop_proxy
  stop_joern
}
trap cleanup INT TERM

_joern_import_args() {
  if [ -n "$JOERN_IMPORT_SC" ] && [ -f "$JOERN_IMPORT_SC" ]; then
    echo "--import" "$JOERN_IMPORT_SC"
  fi
}

start_joern() {
  if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
    # shellcheck disable=SC2046
    "$JOERN_BIN" \
      "-J-Xmx${XMX}" \
      "-J-XX:+UseContainerSupport" \
      --server \
      --server-host "$JOERN_SERVER_HOST" \
      --server-port "$JOERN_INTERNAL_PORT" \
      --server-auth-username "$JOERN_SERVER_AUTH_USERNAME" \
      --server-auth-password "$JOERN_SERVER_AUTH_PASSWORD" \
      $(_joern_import_args) &
  else
    # shellcheck disable=SC2046
    "$JOERN_BIN" \
      "-J-Xmx${XMX}" \
      "-J-XX:+UseContainerSupport" \
      --server \
      --server-host "$JOERN_SERVER_HOST" \
      --server-port "$JOERN_INTERNAL_PORT" \
      $(_joern_import_args) &
  fi
  JOERN_PID="$!"
  echo "unified-entrypoint: joern started pid=$JOERN_PID" >&2
}

wait_for_joern_ready() {
  url="http://${JOERN_INTERNAL_HOST}:${JOERN_INTERNAL_PORT}/query-sync"
  body='{"query":"val _health = 1"}'
  deadline=$(( $(date +%s) + JOERN_READY_TIMEOUT_SEC ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! kill -0 "$JOERN_PID" >/dev/null 2>&1; then
      echo "unified-entrypoint: joern process died during startup" >&2
      return 1
    fi
    if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
      if curl -sf -u "${JOERN_SERVER_AUTH_USERNAME}:${JOERN_SERVER_AUTH_PASSWORD}" \
        -H "Content-Type: application/json" -d "$body" "$url" >/dev/null 2>&1; then
        echo "unified-entrypoint: joern ready on $url" >&2
        return 0
      fi
    else
      if curl -sf -H "Content-Type: application/json" -d "$body" "$url" >/dev/null 2>&1; then
        echo "unified-entrypoint: joern ready on $url" >&2
        return 0
      fi
    fi
    sleep 1
  done
  echo "unified-entrypoint: timed out waiting for joern (${JOERN_READY_TIMEOUT_SEC}s)" >&2
  return 1
}

start_proxy() {
  export PROXY_PORT="$PROXY_PORT"
  export JOERN_INTERNAL_PORT="$JOERN_INTERNAL_PORT"
  export JOERN_INTERNAL_HOST="$JOERN_INTERNAL_HOST"
  export PYTHONPATH="/app:${PYTHONPATH:-}"
  uvicorn joern_server.app:app --host "${PROXY_HOST:-0.0.0.0}" --port "${PROXY_PORT}" &
  PROXY_PID="$!"

  if ! kill -0 "$PROXY_PID" >/dev/null 2>&1; then
    echo "unified-entrypoint: proxy process exited early (pid=$PROXY_PID)" >&2
    return 1
  fi
  echo "unified-entrypoint: proxy started pid=$PROXY_PID" >&2
  return 0
}

restart_stack() {
  JOERN_RESTART_COUNT=$(( JOERN_RESTART_COUNT + 1 ))
  if [ "$JOERN_RESTART_COUNT" -gt "$JOERN_MAX_RESTARTS" ]; then
    echo "unified-entrypoint: max joern restarts ($JOERN_MAX_RESTARTS) exceeded" >&2
    exit 1
  fi
  echo "unified-entrypoint: restarting joern+proxy (attempt $JOERN_RESTART_COUNT)" >&2
  stop_watchdog
  stop_proxy
  stop_joern
  sleep "$JOERN_RESTART_DELAY_SEC"
  start_joern
  wait_for_joern_ready || exit 1
  start_proxy || exit 1
  start_watchdog || exit 1
}

start_watchdog() {
  (
    fail_count=0
    url="http://${JOERN_INTERNAL_HOST}:${JOERN_INTERNAL_PORT}/query-sync"
    body='{"query":"val _health = 1"}'
    while true; do
      if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
        if curl -sf --connect-timeout 2 --max-time 5 \
          -u "${JOERN_SERVER_AUTH_USERNAME}:${JOERN_SERVER_AUTH_PASSWORD}" \
          -H "Content-Type: application/json" -d "$body" "$url" >/dev/null 2>&1; then
          fail_count=0
        else
          fail_count=$((fail_count + 1))
        fi
      else
        if curl -sf --connect-timeout 2 --max-time 5 \
          -H "Content-Type: application/json" -d "$body" "$url" >/dev/null 2>&1; then
          fail_count=0
        else
          fail_count=$((fail_count + 1))
        fi
      fi
      if [ "$fail_count" -ge "$JOERN_WATCHDOG_FAIL_THRESHOLD" ]; then
        echo "unified-entrypoint: watchdog: joern unresponsive (${fail_count} failures), triggering restart" >&2
        exit 1
      fi
      sleep "$JOERN_WATCHDOG_INTERVAL_SEC"
    done
  ) &
  WATCHDOG_PID="$!"
  echo "unified-entrypoint: watchdog started pid=$WATCHDOG_PID" >&2
  return 0
}

# --- bootstrap ---
start_joern
wait_for_joern_ready || exit 1
start_proxy || exit 1
start_watchdog || exit 1

# --- supervise until Joern exits permanently (max restarts) ---
while true; do
  if ! kill -0 "$JOERN_PID" >/dev/null 2>&1; then
    wait "$JOERN_PID" 2>/dev/null || true
    restart_stack
  fi
  if [ -n "$WATCHDOG_PID" ] && ! kill -0 "$WATCHDOG_PID" >/dev/null 2>&1; then
    wait "$WATCHDOG_PID" 2>/dev/null || true
    echo "unified-entrypoint: watchdog exited, restarting stack" >&2
    restart_stack
  fi
  if [ -n "$PROXY_PID" ] && ! kill -0 "$PROXY_PID" >/dev/null 2>&1; then
    echo "unified-entrypoint: proxy died, exiting" >&2
    exit 1
  fi
  sleep 2
done
