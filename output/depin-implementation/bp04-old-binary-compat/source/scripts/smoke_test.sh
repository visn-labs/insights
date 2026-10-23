#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_PORT="${VISN_SMOKE_PORT:-18080}"
TEST_DATA="$(mktemp -d /private/tmp/visn-phase0-smoke.XXXXXX)"
TEST_LOG="$TEST_DATA/service.log"
SERVICE_PID=""

cleanup() {
  if [[ -n "$SERVICE_PID" ]]; then
    kill "$SERVICE_PID" 2>/dev/null || true
    wait "$SERVICE_PID" 2>/dev/null || true
  fi
  echo "Smoke-test artifacts: $TEST_DATA"
}
trap cleanup EXIT

cd "$PROJECT_DIR"
VISN_BIND="127.0.0.1:$TEST_PORT" \
VISN_DATA_DIR="$TEST_DATA/data" \
VISN_GEMMA_TIMEOUT_SECS=1 \
cargo run >"$TEST_LOG" 2>&1 &
SERVICE_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$TEST_PORT/healthz" >/dev/null; then
    break
  fi
  sleep 0.25
done

curl -fsS "http://127.0.0.1:$TEST_PORT/healthz"
curl -fsS "http://127.0.0.1:$TEST_PORT/" | grep -q 'Visn Pipeline Lab'
curl -fsS "http://127.0.0.1:$TEST_PORT/app.js" | grep -q 'submitJob'
curl -fsS "http://127.0.0.1:$TEST_PORT/api/v1/capabilities" | grep -q 'memory + local uploads'
JOB_RESPONSE=$(curl -fsS -X POST "http://127.0.0.1:$TEST_PORT/api/v1/jobs" \
  -H 'content-type: application/json' \
  -d '{"name":"Smoke test","source":"sample","backend":"simulator","detector_fps":5,"gemma_enabled":false,"observations":[],"policy":{}}')
JOB_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$JOB_RESPONSE")

for _ in $(seq 1 40); do
  JOB_RESPONSE=$(curl -fsS "http://127.0.0.1:$TEST_PORT/api/v1/jobs/$JOB_ID")
  JOB_STATUS=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$JOB_RESPONSE")
  if [[ "$JOB_STATUS" == "completed" ]]; then
    break
  fi
  if [[ "$JOB_STATUS" == "failed" ]]; then
    echo "$JOB_RESPONSE"
    exit 1
  fi
  sleep 0.25
done

python3 -c '
import json, sys
job = json.load(sys.stdin)
assert job["status"] == "completed", job
result = job["result"]
assert result["observations_processed"] == 11, result
assert len(result["tracks"]) == 2, result
assert any(event["event_type"] == "line_crossed" for event in result["events"]), result
assert any(event["event_type"] == "restricted_zone_occupied" for event in result["events"]), result
assert result["gemma"]["requested"] is False, result
print("\nPhase 0 smoke test passed:", len(result["tracks"]), "tracks,", len(result["events"]), "events")
' <<<"$JOB_RESPONSE"
