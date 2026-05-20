#!/bin/sh
set -e

# Unified service entrypoint:
# - Start Joern HTTP server (internal port)
# - Start HTTP proxy: /parse, /parse/repo, /query-sync, /graph/*, /health, /version

JOERN_BIN="/opt/joern/joern-cli/joern"

JOERN_SERVER_HOST="${JOERN_SERVER_HOST:-127.0.0.1}"
JOERN_INTERNAL_PORT="${JOERN_INTERNAL_PORT:-${JOERN_SERVER_PORT:-8081}}"

PROXY_PORT="${PROXY_PORT:-${JOERN_PUBLISH_PORT:-8080}}"

XMX="${JOERN_JAVA_XMX:-8g}"

# Optional Scala import script (disabled by default — clients use raw CPGQL via /query-sync).
JOERN_IMPORT_SC="${JOERN_IMPORT_SC:-}"

if [ ! -x "$JOERN_BIN" ]; then
  echo "joern binary not found at $JOERN_BIN" >&2
  exit 1
fi

echo "unified-entrypoint: JOERN host=$JOERN_SERVER_HOST internal_port=$JOERN_INTERNAL_PORT XMX=$XMX" >&2
echo "unified-entrypoint: proxy_port=$PROXY_PORT" >&2

if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
  export JOERN_AUTH_USERNAME="$JOERN_SERVER_AUTH_USERNAME"
  export JOERN_AUTH_PASSWORD="$JOERN_SERVER_AUTH_PASSWORD"
fi

cleanup() {
  for pid in "$JOERN_PID" "$PROXY_PID"; do
    if [ -n "$pid" ] && kill "$pid" >/dev/null 2>&1; then
      true
    fi
  done
}
trap cleanup INT TERM

_joern_import_args() {
  if [ -n "$JOERN_IMPORT_SC" ] && [ -f "$JOERN_IMPORT_SC" ]; then
    echo "--import" "$JOERN_IMPORT_SC"
  fi
}

start_joern() {
  # shellcheck disable=SC2046
  if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
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
    "$JOERN_BIN" \
      "-J-Xmx${XMX}" \
      "-J-XX:+UseContainerSupport" \
      --server \
      --server-host "$JOERN_SERVER_HOST" \
      --server-port "$JOERN_INTERNAL_PORT" \
      $(_joern_import_args) &
  fi
  JOERN_PID="$!"
}

start_proxy() {
  export PROXY_PORT="$PROXY_PORT"
  export JOERN_INTERNAL_PORT="$JOERN_INTERNAL_PORT"
  export JOERN_INTERNAL_HOST="127.0.0.1"
  python3 /app/joern_server/proxy.py &
  PROXY_PID="$!"

  if ! kill -0 "$PROXY_PID" >/dev/null 2>&1; then
    echo "unified-entrypoint: proxy process exited early (pid=$PROXY_PID)" >&2
    exit 1
  fi
}

start_joern
start_proxy

wait "$JOERN_PID"
