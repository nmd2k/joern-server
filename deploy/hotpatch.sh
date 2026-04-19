#!/usr/bin/env bash
# hotpatch.sh — push code changes into running Joern containers without a full image rebuild.
#
# Use this when you have changed Python files only (joern_server/ or mcp-joern/) and
# want to apply the change immediately without waiting for a Docker image build.
#
# WARNING: hot-patched changes are NOT persistent. If a container is recreated from the
# image (e.g. autoheal restart, `docker compose up`), it will revert to the image version.
# Always follow up with a proper image rebuild (see deploy/README.md).
#
# Usage:
#   ./deploy/hotpatch.sh               # patch all running deploy-joern-* containers
#   ./deploy/hotpatch.sh joern-1       # patch a single named container
#   COMPOSE_PREFIX=prod ./deploy/hotpatch.sh   # override container name prefix

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_PREFIX="${COMPOSE_PREFIX:-deploy}"

# Files to sync into /app/ inside each container.
# Add more paths here as needed (directories are copied recursively).
SYNC_PATHS=(
  "joern_server"
  "mcp-joern/server.py"
  "mcp-joern/server_tools.py"
  "mcp-joern/server_tools.sc"
  "mcp-joern/common_tools.py"
)

patch_container() {
  local container="$1"

  if ! docker inspect "$container" &>/dev/null; then
    echo "  [skip] $container — not found"
    return
  fi

  echo "  [patch] $container"

  for rel_path in "${SYNC_PATHS[@]}"; do
    src="$REPO_ROOT/$rel_path"
    if [ ! -e "$src" ]; then
      continue
    fi
    docker cp "$src" "$container:/app/$rel_path" 2>/dev/null || true
  done

  # Restart the Python proxy (proxy.py) so it picks up joern_server/ changes.
  PROXY_PID=$(docker exec "$container" pgrep -f "joern_server/proxy.py" 2>/dev/null || true)
  if [ -n "$PROXY_PID" ]; then
    docker exec "$container" kill "$PROXY_PID" 2>/dev/null || true
    sleep 0.5
  fi
  docker exec -d "$container" sh -c "JOERN_INTERNAL_HOST=127.0.0.1 python3 /app/joern_server/proxy.py"

  # Restart the MCP server (server.py) so it picks up mcp-joern/ changes.
  MCP_PID=$(docker exec "$container" pgrep -f "server.py" 2>/dev/null || true)
  if [ -n "$MCP_PID" ]; then
    docker exec "$container" kill "$MCP_PID" 2>/dev/null || true
    sleep 0.5
  fi
  docker exec -d "$container" sh -c \
    "cd /app/mcp-joern && MCP_TRANSPORT=sse MCP_HOST=0.0.0.0 python3 server.py"

  echo "  [done]  $container"
}

if [ $# -gt 0 ]; then
  # Explicit container name(s) provided
  for name in "$@"; do
    patch_container "$name"
  done
else
  # Auto-discover all running deploy-joern-* containers
  containers=$(docker ps --format '{{.Names}}' | grep "^${COMPOSE_PREFIX}-joern-" | sort -V)
  if [ -z "$containers" ]; then
    echo "No running containers matching '${COMPOSE_PREFIX}-joern-*'. Is the stack up?"
    exit 1
  fi
  echo "Hot-patching containers (prefix=${COMPOSE_PREFIX}):"
  for c in $containers; do
    patch_container "$c"
  done
fi

echo ""
echo "Done. Verify with: curl http://localhost:8080/health"
echo ""
echo "REMINDER: hot-patch is NOT persistent. Run a full image rebuild before the next"
echo "          'docker compose up' or autoheal restart will revert your changes."
echo "          See deploy/README.md — 'Rebuilding the image'."
