# FireCloud Signaling + Relay Service (Hardened)

This service provides WAN peer discovery, encrypted manifest sync, and relay-backed chunk transfer for FireCloud mobile clients.

## Security model

- **Firebase ID token required** on API and relay routes (`Authorization: Bearer <token>`).
- **Account scoping enforced**:
  - peer listing is account-scoped
  - manifest operations are owner-scoped
  - relay chunk cache access is account-scoped
- **Rate limiting** for read/write traffic.
- **Relay proxy path allowlist** (`/health`, `/info`, `/manifests`), so it is not an open proxy.
- **SSRF guardrails** block private/non-routable upstreams by default.

## API surface

- `POST /api/v1/peers/register`
- `POST /api/v1/peers/heartbeat`
- `GET /api/v1/peers`
- `DELETE /api/v1/peers/{device_id}`
- `POST /api/v1/manifests/upsert`
- `GET /api/v1/manifests?owner_id=...`
- `DELETE /api/v1/manifests/{file_id}?owner_id=...`
- `GET|POST|PUT|PATCH|DELETE /relay/p2p/{device_id}/{path...}`
  - `/chunks/{hash}` is relay-cached for mobile-data fallback

## Durable storage

By default, in-memory mode is used for local development.

For production, configure:

- `FIRECLOUD_STORAGE_BUCKET=<your-gcs-bucket>`
- `FIRECLOUD_REQUIRE_DURABLE_STORAGE=true`

When enabled, peers, manifests, and relay chunk cache metadata/data are stored in GCS.

## Local run

```bash
cd /home/rajashekharsunkara/Documents/firecloud/signal-relay-prototype
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# For local smoke/dev only:
export FIRECLOUD_AUTH_MODE=disabled
uvicorn main:app --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl http://127.0.0.1:8080/health
```

## Production Cloud Run deployment

```bash
cd /home/rajashekharsunkara/Documents/firecloud/signal-relay-prototype
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

Create bucket (once):

```bash
gsutil mb -l us-central1 gs://YOUR_SIGNAL_RELAY_BUCKET
```

Deploy:

```bash
gcloud run deploy firecloud-signal-relay \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --max-instances=5 \
  --set-env-vars FIRECLOUD_AUTH_MODE=required,FIRECLOUD_STORAGE_BUCKET=YOUR_SIGNAL_RELAY_BUCKET,FIRECLOUD_REQUIRE_DURABLE_STORAGE=true,FIRECLOUD_ALLOW_PRIVATE_UPSTREAMS=false
```

Get service URL:

```bash
RUN_URL=$(gcloud run services describe firecloud-signal-relay --region us-central1 --format='value(status.url)')
echo "$RUN_URL"
```

Use in FireCloud app:

- Signaling URL: `RUN_URL`
- Relay URL: `RUN_URL/relay`

**Important:** users must be signed in with Google in the mobile app so backend calls include Firebase ID tokens.
