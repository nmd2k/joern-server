#!/bin/sh
set -e

JOERN="/opt/joern/joern-cli/joern"
HOST="${JOERN_SERVER_HOST:-0.0.0.0}"
PORT="${JOERN_SERVER_PORT:-8080}"
XMX="${JOERN_JAVA_XMX:-8g}"

if [ ! -x "$JOERN" ]; then
  echo "joern binary not found at $JOERN" >&2
  exit 1
fi

echo "joern-server-entrypoint: HOST=$HOST PORT=$PORT JOERN_JAVA_XMX=$XMX" >&2

if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
  exec "$JOERN" \
    "-J-Xmx${XMX}" \
    -J-XX:+UseContainerSupport \
    --server \
    --server-host "$HOST" \
    --server-port "$PORT" \
    --server-auth-username "$JOERN_SERVER_AUTH_USERNAME" \
    --server-auth-password "$JOERN_SERVER_AUTH_PASSWORD"
else
  exec "$JOERN" \
    "-J-Xmx${XMX}" \
    -J-XX:+UseContainerSupport \
    --server \
    --server-host "$HOST" \
    --server-port "$PORT"
fi