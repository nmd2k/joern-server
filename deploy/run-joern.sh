#!/usr/bin/env bash
# Run upstream Joern image with HTTP API only (no MCP, no helper script import by default).
# Run from the repo root so the tree can be mounted as /app.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

JOERN_SERVER_HOST="${JOERN_SERVER_HOST:-0.0.0.0}"
JOERN_SERVER_PORT="${JOERN_SERVER_PORT:-16162}"
JOERN_PUBLISH_PORT="${JOERN_PUBLISH_PORT:-$JOERN_SERVER_PORT}"
JOERN_JAVA_XMX="${JOERN_JAVA_XMX:-8g}"
JOERN_MEMORY_LIMIT="${JOERN_MEMORY_LIMIT:-12g}"
JOERN_IMAGE="${JOERN_IMAGE:-ghcr.io/joernio/joern:master}"
JOERN_IMPORT_SC="${JOERN_IMPORT_SC:-}"
JOERN_SERVER_AUTH_USERNAME="${JOERN_SERVER_AUTH_USERNAME-joern}"
JOERN_SERVER_AUTH_PASSWORD="${JOERN_SERVER_AUTH_PASSWORD-joern}"

extra=()
if [[ -n "$JOERN_SERVER_AUTH_USERNAME" && -n "$JOERN_SERVER_AUTH_PASSWORD" ]]; then
  extra+=(--server-auth-username "$JOERN_SERVER_AUTH_USERNAME" --server-auth-password "$JOERN_SERVER_AUTH_PASSWORD")
fi
if [[ -n "$JOERN_IMPORT_SC" && -f "$JOERN_IMPORT_SC" ]]; then
  extra+=(--import "$JOERN_IMPORT_SC")
fi

docker run --rm -d \
  --memory="${JOERN_MEMORY_LIMIT}" \
  -v /tmp:/tmp \
  -v "${REPO_ROOT}:/app:rw" \
  -p "${JOERN_PUBLISH_PORT}:${JOERN_SERVER_PORT}" \
  "$JOERN_IMAGE" \
  /opt/joern/joern-cli/joern \
  "-J-Xmx${JOERN_JAVA_XMX}" \
  --server \
  --server-host "$JOERN_SERVER_HOST" \
  --server-port "$JOERN_SERVER_PORT" \
  "${extra[@]}"
