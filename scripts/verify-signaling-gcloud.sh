#!/usr/bin/env bash
set -euo pipefail

# Verify FireCloud signaling/relay production health (including optional Cloud Run checks).
# Usage:
#   FIRECLOUD_SIGNALING_URL=https://signal.example.com ./scripts/verify-signaling-gcloud.sh
#   ./scripts/verify-signaling-gcloud.sh https://signal.example.com

SIGNALING_URL="${1:-${FIRECLOUD_SIGNALING_URL:-}}"
SERVICE_NAME="${FIRECLOUD_SIGNALING_SERVICE:-firecloud-signal-relay}"
REGION="${FIRECLOUD_GCLOUD_REGION:-us-central1}"
PROJECT_ID="${FIRECLOUD_GCP_PROJECT_ID:-}"
TEST_ACCOUNT_ID="${FIRECLOUD_TEST_ACCOUNT_ID:-}"
TEST_ID_TOKEN="${FIRECLOUD_TEST_ID_TOKEN:-}"

if [[ -z "$SIGNALING_URL" ]] && command -v gcloud >/dev/null 2>&1; then
  if [[ -n "$PROJECT_ID" ]]; then
    gcloud config set project "$PROJECT_ID" >/dev/null
  fi
  SIGNALING_URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"
fi

if [[ -z "$SIGNALING_URL" ]]; then
  echo "ERROR: signaling URL not provided. Set FIRECLOUD_SIGNALING_URL or pass URL as arg." >&2
  exit 1
fi

SIGNALING_URL="${SIGNALING_URL%/}"
HOST="$(python3 - <<'PY' "$SIGNALING_URL"
import sys
from urllib.parse import urlparse
print(urlparse(sys.argv[1]).hostname or "")
PY
)"

if [[ -z "$HOST" ]]; then
  echo "ERROR: could not parse host from URL: $SIGNALING_URL" >&2
  exit 1
fi

echo "==> Verifying signaling endpoint: $SIGNALING_URL"
echo "==> Resolving host: $HOST"
if ! getent hosts "$HOST" >/dev/null 2>&1; then
  echo "ERROR: DNS resolution failed for $HOST" >&2
  exit 1
fi

echo "==> Checking /health"
HEALTH_JSON="$(curl -fsS "$SIGNALING_URL/health")"
python3 - <<'PY' "$HEALTH_JSON"
import json, sys
payload = json.loads(sys.argv[1])
if payload.get("status") != "ok":
    raise SystemExit("health status is not ok")
print("health: ok")
print(f"auth_mode: {payload.get('auth_mode')}")
print(f"durable_storage: {payload.get('durable_storage')}")
PY

if command -v gcloud >/dev/null 2>&1; then
  echo "==> Checking Cloud Run service URL"
  if [[ -n "$PROJECT_ID" ]]; then
    gcloud config set project "$PROJECT_ID" >/dev/null
  fi
  CLOUD_RUN_URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"
  if [[ -n "$CLOUD_RUN_URL" ]]; then
    CLOUD_RUN_URL="${CLOUD_RUN_URL%/}"
    if [[ "$CLOUD_RUN_URL" == "$SIGNALING_URL" ]]; then
      echo "cloud-run-url: matches"
    else
      echo "WARNING: Cloud Run URL differs"
      echo "  cloud-run: $CLOUD_RUN_URL"
      echo "  expected : $SIGNALING_URL"
    fi
  else
    echo "WARNING: unable to read Cloud Run service metadata"
  fi
fi

if [[ -n "$TEST_ACCOUNT_ID" && -n "$TEST_ID_TOKEN" ]]; then
  echo "==> Running authenticated peer API smoke test"
  DEVICE_ID="smoke-$(date +%s)"
  AUTH_HEADER="Authorization: Bearer $TEST_ID_TOKEN"
  ACCOUNT_HEADER="X-FireCloud-Account-Id: $TEST_ACCOUNT_ID"

  curl -fsS -X POST "$SIGNALING_URL/api/v1/peers/register" \
    -H "$AUTH_HEADER" \
    -H "$ACCOUNT_HEADER" \
    -H "Content-Type: application/json" \
    --data "{\"device_id\":\"$DEVICE_ID\",\"public_key\":\"smoke\",\"public_ip\":\"198.51.100.1\",\"public_port\":4001,\"account_id\":\"$TEST_ACCOUNT_ID\",\"role\":\"consumer\",\"available_storage\":0}" >/dev/null

  curl -fsS "$SIGNALING_URL/api/v1/peers?account_id=$TEST_ACCOUNT_ID" \
    -H "$AUTH_HEADER" \
    -H "$ACCOUNT_HEADER" >/dev/null

  curl -fsS -X DELETE "$SIGNALING_URL/api/v1/peers/$DEVICE_ID" \
    -H "$AUTH_HEADER" \
    -H "$ACCOUNT_HEADER" >/dev/null
  echo "api-smoke: ok"
else
  echo "==> Skipping authenticated API smoke test (set FIRECLOUD_TEST_ACCOUNT_ID and FIRECLOUD_TEST_ID_TOKEN)"
fi

echo "SIGNALING VERIFICATION PASSED"
