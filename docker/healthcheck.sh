#!/bin/sh
set -e

PORT="${JOERN_SERVER_PORT:-8080}"
# With unified container, Joern runs on an internal port and a proxy exposes /query-sync externally.
# Prefer the proxy/exposed publish port.
PORT="${PROXY_PORT:-${JOERN_PUBLISH_PORT:-$PORT}}"
URL="http://127.0.0.1:${PORT}/query-sync"
BODY='{"query":"val _health = 1"}'

if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
  curl -sf -u "${JOERN_SERVER_AUTH_USERNAME}:${JOERN_SERVER_AUTH_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "$BODY" \
    "$URL" >/dev/null
else
  curl -sf -H "Content-Type: application/json" -d "$BODY" "$URL" >/dev/null
fi