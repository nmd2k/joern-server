#!/usr/bin/env bash
# Live drain test: one replica drains while HAProxy serves new sessions without client errors.
#
# Requires:
#   - At least 3 healthy joern replicas visible through HAProxy (drain removes 1; need 2 left)
#   - JOERN_ENABLE_DRAIN_TEST=1 in deploy/.env (staging only)
#   - HAProxy recreated after haproxy.cfg retry-on 503 change
#
# Usage (from repo root):
#   docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d --scale joern=3
#   ./scripts/test_drain_live.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export NEURALATLAS_RUN_DRAIN_TESTS=1
export NEURALATLAS_LIVE_JOERN_URL="${NEURALATLAS_LIVE_JOERN_URL:-http://127.0.0.1:8080}"
export NEURALATLAS_DRAIN_FLOOD_REQUESTS="${NEURALATLAS_DRAIN_FLOOD_REQUESTS:-40}"
VIP="${NEURALATLAS_LIVE_JOERN_URL}"

info() { echo "[drain-test] $*"; }
fail() { echo "[drain-test] ERROR: $*" >&2; exit 1; }

is_haproxy_html_503() {
  local file="$1"
  grep -qi '<html' "$file" 2>/dev/null && grep -qi '503' "$file" 2>/dev/null
}

wait_for_vip() {
  local tries="${1:-60}"
  local i=1
  while (( i <= tries )); do
    local code
    code="$(curl -s -o /tmp/drain_health.json -w '%{http_code}' "${VIP}/health" || true)"
    if [[ "$code" == "200" ]] && grep -q '"ok"' /tmp/drain_health.json 2>/dev/null; then
      info "VIP healthy (attempt ${i}/${tries})"
      return 0
    fi
    if is_haproxy_html_503 /tmp/drain_health.json; then
      info "waiting for backends (${i}/${tries}) — HAProxy 503 (replicas starting or all down)"
    else
      info "waiting for VIP (${i}/${tries}) — HTTP ${code}"
    fi
    sleep 2
    (( i++ )) || true
  done
  fail "VIP not healthy after ${tries} attempts. Last response:
$(head -c 400 /tmp/drain_health.json 2>/dev/null || echo '(empty)')"
}

count_healthy_replicas() {
  docker ps --filter 'name=deploy-joern' --filter 'health=healthy' -q 2>/dev/null | wc -l | tr -d ' '
}

# HAProxy may see fewer backends than docker (restarts, drain health 503, DNS lag).
count_haproxy_backends() {
  python3 - <<'PY'
import os, uuid, httpx

base = os.environ.get("NEURALATLAS_LIVE_JOERN_URL", "http://127.0.0.1:8080").rstrip("/")
seen: set[str] = set()
for _ in range(24):
    aff = f"probe-{uuid.uuid4().hex}"
    try:
        r = httpx.post(
            f"{base}/query-sync",
            json={"query": "val _health = 1"},
            headers={"X-Affinity-Key": aff},
            timeout=10.0,
        )
        if r.status_code == 200:
            name = r.headers.get("X-Served-By", "")
            if name:
                seen.add(name)
    except Exception:
        pass
print(len(seen))
if seen:
    print(" ".join(sorted(seen)), end="")
PY
}

wait_for_haproxy_backends() {
  local min="${1:-2}"
  local tries="${2:-60}"
  local i=1
  while (( i <= tries )); do
    local probe
    probe="$(count_haproxy_backends)"
    local count="${probe%%$'\n'*}"
    local names="${probe#*$'\n'}"
    if [[ "${count}" -ge "${min}" ]]; then
      info "HAProxy backends ready: ${count} (${names})"
      return 0
    fi
    info "waiting for HAProxy backends (${count}/${min}, attempt ${i}/${tries})${names:+ — saw ${names}}"
    sleep 3
    (( i++ )) || true
  done
  fail "Need at least ${min} joern backends visible through HAProxy (not just docker healthy).
  HAProxy returns messy HTML 503 when no backend is available:
    <html><body><h1>503 Service Unavailable</h1> No server is available...
  Scale and wait for all replicas, then recreate haproxy if you changed haproxy.cfg:
    docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d --scale joern=3
    docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d joern-haproxy --force-recreate"
}

info "=== Live drain test via ${VIP} ==="
wait_for_vip 60

info "Checking drain test hook (GET /debug/drain/enabled)..."
hook_code="$(curl -s -o /tmp/drain_hook.json -w '%{http_code}' "${VIP}/debug/drain/enabled" || true)"
if is_haproxy_html_503 /tmp/drain_hook.json; then
  fail "HAProxy returned HTML 503 (no backend). Scale joern replicas and wait for health."
fi
if [[ "$hook_code" != "200" ]]; then
  fail "/debug/drain/enabled returned HTTP ${hook_code}: $(head -c 200 /tmp/drain_hook.json)"
fi
enabled="$(python3 -c 'import json; print(json.load(open("/tmp/drain_hook.json")).get("enabled", False))' 2>/dev/null || echo false)"
if [[ "$enabled" != "True" && "$enabled" != "true" ]]; then
  fail "JOERN_ENABLE_DRAIN_TEST is not enabled. Set JOERN_ENABLE_DRAIN_TEST=1 in deploy/.env and redeploy joern."
fi
info "Drain test hook enabled."

replicas="$(count_healthy_replicas)"
info "Healthy joern replicas (docker): ${replicas}"
if [[ "${replicas}" -lt 2 ]]; then
  fail "Need at least 2 healthy joern replicas for this test (found ${replicas}).
  HAProxy cannot redispatch when only one backend exists.
  Run: docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d --scale joern=3"
fi

wait_for_haproxy_backends 3 60

info "Running pytest (quiet output; use -v by passing -- -v)..."
python3 -m pytest tests/integration/test_drain_haproxy.py -m integration -q --tb=line "$@"
info "=== PASSED ==="
