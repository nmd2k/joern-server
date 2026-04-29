#!/bin/sh
set -e

# Unified service entrypoint:
# - Start Joern HTTP server (internal port)
# - Start a tiny HTTP proxy exposing /query-sync plus /health and /version
# - Start FastMCP (MCP-over-SSE) that forwards tool calls to Joern /query-sync

JOERN_BIN="/opt/joern/joern-cli/joern"

JOERN_SERVER_HOST="${JOERN_SERVER_HOST:-127.0.0.1}"
JOERN_INTERNAL_PORT="${JOERN_INTERNAL_PORT:-${JOERN_SERVER_PORT:-8081}}"

# External publish port (proxy listens here)
PROXY_PORT="${PROXY_PORT:-${JOERN_PUBLISH_PORT:-8080}}"

XMX="${JOERN_JAVA_XMX:-8g}"

MCP_HOST="${MCP_HOST:-0.0.0.0}"
MCP_PORT="${MCP_PORT:-9000}"

JOERN_IMPORT_SC="${JOERN_IMPORT_SC:-/app/mcp-joern/server_tools.sc}"
# Note: importing `server_tools_source.sc` alongside `server_tools.sc` can trigger
# Scala naming collisions (E161) because both define overlapping toplevel symbols.
# The unified container imports only `server_tools.sc` to keep startup reliable.
JOERN_IMPORT_SC_SOURCE="${JOERN_IMPORT_SC_SOURCE:-}"

if [ ! -x "$JOERN_BIN" ]; then
  echo "joern binary not found at $JOERN_BIN" >&2
  exit 1
fi

echo "unified-entrypoint: JOERN host=$JOERN_SERVER_HOST internal_port=$JOERN_INTERNAL_PORT XMX=$XMX" >&2
echo "unified-entrypoint: proxy_port=$PROXY_PORT mcp_host=$MCP_HOST mcp_port=$MCP_PORT" >&2

# Bridge env var naming mismatch between Joern container auth vars and MCP's joern_remote().
if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
  export JOERN_AUTH_USERNAME="$JOERN_SERVER_AUTH_USERNAME"
  export JOERN_AUTH_PASSWORD="$JOERN_SERVER_AUTH_PASSWORD"
fi

cleanup() {
  # shellcheck disable=SC2043
  for pid in "$JOERN_PID" "$PROXY_PID" "$MCP_PID"; do
    if [ -n "$pid" ] && kill "$pid" >/dev/null 2>&1; then
      true
    fi
  done
}
trap cleanup INT TERM

start_joern() {
  if [ -n "$JOERN_SERVER_AUTH_USERNAME" ] && [ -n "$JOERN_SERVER_AUTH_PASSWORD" ]; then
    "$JOERN_BIN" \
      "-J-Xmx${XMX}" \
      "-J-XX:+UseContainerSupport" \
      --server \
      --server-host "$JOERN_SERVER_HOST" \
      --server-port "$JOERN_INTERNAL_PORT" \
      --server-auth-username "$JOERN_SERVER_AUTH_USERNAME" \
      --server-auth-password "$JOERN_SERVER_AUTH_PASSWORD" \
      --import "$JOERN_IMPORT_SC" &
  else
    "$JOERN_BIN" \
      "-J-Xmx${XMX}" \
      "-J-XX:+UseContainerSupport" \
      --server \
      --server-host "$JOERN_SERVER_HOST" \
      --server-port "$JOERN_INTERNAL_PORT" \
      --import "$JOERN_IMPORT_SC" &
  fi
  JOERN_PID="$!"
}

start_proxy() {
  # proxy forwards to internal Joern HTTP on JOERN_INTERNAL_PORT
  export PROXY_PORT="$PROXY_PORT"
  export JOERN_INTERNAL_PORT="$JOERN_INTERNAL_PORT"
  export JOERN_INTERNAL_HOST="127.0.0.1"
  # Run the proxy as a standalone script to avoid importing `joern_server/__init__.py`
  # (which may depend on training code not present inside the unified image).
  python3 /app/joern_server/proxy.py &
  PROXY_PID="$!"

  # Fail fast if the proxy crashed (e.g. missing python module).
  if ! kill -0 "$PROXY_PID" >/dev/null 2>&1; then
    echo "unified-entrypoint: proxy process exited early (pid=$PROXY_PID)" >&2
    exit 1
  fi
}

start_mcp() {
  # MCP-over-SSE so external clients can connect via HTTP.
  export MCP_TRANSPORT="sse"
  export MCP_HOST="$MCP_HOST"
  export MCP_PORT="$MCP_PORT"
  # Route MCP through the proxy (not directly to REPL) so parse and query requests
  # benefit from CPGRegistry cache and LRU query cache.
  export HOST="127.0.0.1"
  export PORT="$PROXY_PORT"

  cd /app/mcp-joern
  python3 server.py &
  MCP_PID="$!"

  # Fail fast if MCP crashed (e.g. FastMCP startup/config issue).
  if ! kill -0 "$MCP_PID" >/dev/null 2>&1; then
    echo "unified-entrypoint: MCP process exited early (pid=$MCP_PID)" >&2
    exit 1
  fi
}

start_joern
start_proxy
start_mcp

# POSIX `wait` doesn't support `wait -n` in /bin/sh (dash/busybox).
# Keep the container alive as long as the Joern server is running.
wait "$JOERN_PID"