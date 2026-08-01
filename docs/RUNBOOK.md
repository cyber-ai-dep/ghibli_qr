# Running and Testing the System

> Operational how-to: setup, local run, Docker, calling the API, and testing. For
> *how the system works* (architecture, request flow, AI pipeline), see
> [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) — this document assumes that
> context and doesn't re-explain it.

---

## 1. Do you need ngrok? — No

A common assumption from other AI-pipeline projects is that you need a public tunnel
(ngrok, Cloudflare Tunnel, etc.) so a third-party AI provider can call your server
back. **This project does not need that.** BytePlus ARK's `images/generations`
endpoint is synchronous — the generated image URL comes back directly in the HTTP
response to the outbound call this server makes (see
[SYSTEM_ARCHITECTURE.md §6.3](SYSTEM_ARCHITECTURE.md#63-the-pendingtasks-future-shim-important-to-understand)).
Nothing external ever calls back into this server. `DOMAIN` in `.env` is only used to
build the URLs this server hands back to *its own* caller (so they can `GET` the
generated images from `/tmp/...`) — it does not need to be publicly reachable unless
the client calling `/v1/ghibli-qr` is on a different machine/network than this server.

- Testing from `curl`/Postman/your frontend **on the same machine**: `DOMAIN=http://localhost:30820` is fine.
- Testing from a phone, another machine, or a real frontend deployment: `DOMAIN` must
  be an address that client can actually reach (a LAN IP, a public IP, or a real
  domain) — but that's a normal reachability requirement, not a webhook/tunnel need.

## 2. Prerequisites

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) (`pip install uv`)
- A BytePlus ARK API key (`ARK_API_KEY`) — required, paid, real generation calls cost money
- Docker + Docker Compose v2, if running containerized
- `src/static/lock.png` must exist as a real PNG — required for Stage 2, already in the repo

## 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
DOMAIN=http://localhost:30820      # or your server's reachable address/port
ARK_API_KEY=<your-byteplus-ark-api-key>
```

Everything else in `.env.example` has a working default — see
[SYSTEM_ARCHITECTURE.md §2/§3.4/§6](SYSTEM_ARCHITECTURE.md) for what each variable
controls.

## 4. Run locally (no Docker)

```bash
uv sync                             # installs deps into .venv (incl. torch/open_clip — CPU wheels)
uv run uvicorn src.ghibli_portrait.main:app --host 0.0.0.0 --port 30820 --workers 1
```

- `--workers 1` is **not optional** — see
  [SYSTEM_ARCHITECTURE.md §6.3](SYSTEM_ARCHITECTURE.md#63-the-pendingtasks-future-shim-important-to-understand)
  (`pending_tasks` is an in-memory, single-process dict).
- First startup downloads CLIP weights (~578MB) if not already cached, and blocks
  startup on `_preload_clip()` in `main.py`'s lifespan — expect the first boot to take
  longer than subsequent ones.
- Swagger UI: `http://localhost:30820/docs`
- Health check: `curl http://localhost:30820/v1/health` → `{"success":true,"data":{"status":"healthy"},...}`

## 5. Run with Docker Compose (recommended for anything beyond local dev)

```bash
cp .env.example .env                # edit DOMAIN + ARK_API_KEY as above
docker-compose up -d --build
docker-compose logs -f ghibli-api   # watch startup — wait for "CLIP model preloaded at startup"
curl http://localhost:30820/v1/health
```

Notes on what the container does for you (from `Dockerfile`/`docker-compose.yml`):
- CLIP weights are **baked into the image at build time** (`open_clip.create_model_and_transforms(...)`
  run during `docker build`), so there's no first-request download penalty in a
  freshly built container the way there can be on bare-metal.
- `src/static/lock.png` is bind-mounted read-only into the container — editing it on
  the host takes effect without a rebuild.
- Generated images live in the named volume `ghibli_tmp` — they survive
  `docker-compose down`/`up` (but not `docker-compose down -v`).
- Host port defaults to `30820`; override with `HOST_PORT=8090 docker-compose up -d`.
- After **any code change**, you must rebuild: `docker-compose up -d --build` (a plain
  `restart` reuses the old image). After **any `.env` change**:
  `docker-compose down && docker-compose up -d` (`.env` is only read at container
  creation, not on restart).

## 6. Call the primary endpoint

```bash
curl -X POST http://localhost:30820/v1/ghibli-qr \
  -H "Content-Type: application/json" \
  -d '{
        "imgUrl": "https://images.pexels.com/photos/1563356/pexels-photo-1563356.jpeg",
        "url": "https://example.com/my-profile"
      }'
```

This makes **real, billed** calls to BytePlus ARK (two generations per request). The
response includes `data.resultUrls[0]` — fetch that over HTTP to view the final
image, and `data.qrValidation` to confirm the QR code is scannable and correct.

Cheaper individual pieces to test in isolation without paying for full generation:

```bash
# QR-lock composition only — pure local PIL, no AI call, free
curl -X POST http://localhost:30820/v1/qr-lock \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/test"}'

# Deterministic URL shortening — no AI, no download
curl "http://localhost:30820/v1/qr-url/?url=https://example.com/test"
```

## 7. Run the automated test suite

```bash
PYTHONPATH= uv run --group test pytest -q
```

- Fully mocked — **no network calls, no ARK charges**. Covers validation layers, QR
  generation/decode, skin-tone extraction, the ARK adapter contract, and the full
  `/v1/ghibli-qr` flow with fakes standing in for `httpx`/ARK.
- `tests/manual/` is intentionally excluded from this run
  (`pyproject.toml`: `addopts = "--ignore=tests/manual"`) because those scripts hit
  **live, paid** APIs by design — run them individually and deliberately:
  ```bash
  uv run python tests/manual/test_concurrent.py --count 3   # load test against a running server
  uv run python -m tests.manual.test_clip_validation_manual  # CLIP-only, no ARK cost
  ```

## 8. Quick troubleshooting checklist for a fresh setup

| Symptom | Likely cause / fix |
|---|---|
| Container starts then immediately exits | Check `docker-compose logs` — CLIP preload failure crashes startup intentionally (see `main.py` lifespan comment); usually a bad/missing `CLIP_CACHE_DIR` or no network at build time |
| `422 INVALID_IMAGE_URL` on every request | `imgUrl` must be a public `http(s)://` URL — `localhost`/private IPs are rejected by design (Layer 1) |
| `500 STAGE1_API_ERROR` / `STAGE2_API_ERROR` | Check `ARK_API_KEY` is set and valid; check the `detail` field for ARK's raw error |
| Frequent rate-limit retries in logs (`[GEN] Rate limit detected`) | Lower `GENERATION_CONCURRENCY_LIMIT` (ARK caps at ~10 concurrent/model/account) |
| Returned `resultUrls` not reachable from another machine | `DOMAIN` is set to `localhost` but the client is remote — set `DOMAIN` to an address that client can reach |
| `IsADirectoryError` / crash mentioning `lock.png` | `src/static/lock.png` missing or not a real PNG file |
| Health check passes but `/docs` 404s | Confirm you're hitting the container's mapped host port (`HOST_PORT`, default 30820), not the internal `8010` |

For debugging *pipeline logic* issues (not setup issues) — e.g. why a specific
request failed validation or QR checking — see
[SYSTEM_ARCHITECTURE.md §7.3 "How to debug issues"](SYSTEM_ARCHITECTURE.md#73-how-to-debug-issues).

---

## 9. Reading logs and diagnostics from a live deployment

### Request correlation

Every response carries an `X-Request-ID` header, and every log line that request
produced is tagged with the same id — including the deeper `[GEN]`, `[ARK]`, and
CLIP lines, which otherwise cannot be told apart when several generations run
concurrently.

```bash
curl -i -X POST "$DOMAIN/v1/ghibli-qr" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: probe-001' \
  -d '{"imgUrl":"https://…/photo.jpg","url":"https://example.com"}'
```

Supply your own id to correlate with an upstream caller's trace, or omit it and
read the generated one off the response. Values are accepted only if they match
`^[A-Za-z0-9._:-]{1,64}$` — anything else is replaced with a fresh id, because a
newline in that header would let a caller forge log lines.

```bash
docker compose logs ghibli-api | grep 'probe-001'
kubectl logs deploy/ghibli-api | grep 'probe-001'
```

### The diagnostics API

`/v1/diagnostics` is **internal operational tooling** — for developers, DevOps,
SREs and operators. It is not part of the public API contract.

The endpoints are **always registered and always visible in Swagger** (`/docs`), so
they are discoverable without a config change or restart. **Access is controlled
entirely by `DIAGNOSTICS_TOKEN`.** If that variable is unset, every request is
rejected with `401` — an absent secret denies access, it never grants it.

```bash
# .env  (or the k8s Secret)
DIAGNOSTICS_TOKEN=$(openssl rand -hex 32)
```

Authenticate with either header; `X-Diagnostics-Token` is preferred because it
cannot collide with the platform gateway's `Authorization` header:

```bash
TOKEN=<the value above>
H="X-Diagnostics-Token: $TOKEN"          # preferred
# H="Authorization: Bearer $TOKEN"       # also accepted
```

#### The one call that matters

```bash
curl -s -H "$H" "$DOMAIN/v1/diagnostics" | jq
```

That single response is the operational dashboard for this service:

| Section | What it answers |
|---|---|
| `service` | Which build is this? version, `gitCommit`, `environment`, hostname, pid, uptime |
| `health` | One rolled-up verdict: `healthy` / `degraded` / `unhealthy`, plus which check failed |
| `requests` | Total, active, and peak requests; counts by status class and endpoint; error rate; average duration |
| `concurrency` | Saturation of the CLIP / download / generation semaphores (`inUse`, `waiting`) |
| `pendingTasks` | In-flight generations and the age of the oldest |
| `models` | Did CLIP and QReader actually load in this process? |
| `memory` | Current and peak RSS — check against the container limit |
| `storage` | tmp file counts by prefix, bytes used, free disk |
| `config` | Effective configuration (secrets as presence + fingerprint only) |
| `rateLimiting` | Live state of the production rate limiter: `requestsInWindow`, `remainingCapacity`, `currentlyLimiting`, `windowResetInSeconds`, and observed 429 count |
| `recentLogs` | The latest entries inline, so one call usually explains an incident |

Narrow the embedded logs with `?logLimit=50&logLevel=WARNING`, or omit them with
`?logLimit=0`.

#### Focused queries

```bash
# Everything that went wrong recently
curl -s -H "$H" "$DOMAIN/v1/diagnostics/logs?level=WARNING&limit=50" | jq '.data.entries'

# The full trace of one request
curl -s -H "$H" "$DOMAIN/v1/diagnostics/logs?requestId=probe-001&order=asc" | jq '.data.entries'

# Aggregates: counts by level and logger, plus the last 10 errors
curl -s -H "$H" "$DOMAIN/v1/diagnostics/logs/stats" | jq

# Tail incrementally — feed the previous newestSeq back as sinceSeq
curl -s -H "$H" "$DOMAIN/v1/diagnostics/logs?sinceSeq=1840" | jq '.data.entries'

# Administrative: clear the buffer (stdout is unaffected)
curl -s -X DELETE -H "$H" "$DOMAIN/v1/diagnostics/logs" | jq
```

| Filter (on `/logs`) | Meaning |
|---|---|
| `level` | Minimum level, e.g. `WARNING` |
| `logger` | Case-insensitive substring of the logger name |
| `requestId` | Exact match — the whole trace of one request |
| `contains` | Case-insensitive substring of the message |
| `sinceSeconds` | Relative time window |
| `sinceSeq` | Only entries newer than this sequence number (tail polling) |
| `order` | `desc` (default, newest first) or `asc` |
| `limit` | 1–1000, default 100 |

### Operational caveats — read before relying on this

- **`401` on every call** means `DIAGNOSTICS_TOKEN` is unset or does not match.
  Check the startup log: it states explicitly whether a token was configured.
- **The buffer is in-memory and per-process.** It vanishes on restart or OOM-kill,
  which is precisely when you most want it. **stdout remains the system of record**;
  this API is a convenience, not durable log storage.
- **Secrets are redacted in the buffer, not in stdout.** `ARK_API_KEY`, bearer
  tokens, and URL query strings (signed-CDN credentials) are scrubbed before an
  entry is stored, and the snapshot reports only `arkApiKeyConfigured` plus a
  non-invertible fingerprint. Container logs keep full fidelity, because access to
  them is already controlled by the platform.
- **`gitCommit` is `null` unless the image was built with it.** `.dockerignore`
  excludes `.git/`, so pass it at build time:
  `docker build --build-arg GIT_COMMIT=$(git rev-parse --short=12 HEAD) .`
- **`DELETE /v1/diagnostics/logs` destroys evidence.** It is token-gated and its
  use is itself logged to stdout.
- **The buffer only holds what `LOG_LEVEL` lets through.** Debugging a specific
  request usually means setting `LOG_LEVEL=DEBUG` and restarting.
- **The endpoints are documented in public Swagger.** That is deliberate — they are
  discoverable but not accessible. Because they are advertised, treat the token as
  a real credential: rotate it, keep it in your secret manager, and consider an
  ingress source-range allowlist (`nginx.ingress.kubernetes.io/whitelist-source-range`)
  on `/v1/diagnostics` as defence in depth.
- With more than one replica, a request through the ingress reaches whichever pod
  the load balancer picked. Use `kubectl port-forward` to target a specific pod.

#### Investigating "clients are getting 429s"

`rateLimiting` in the snapshot reflects the actual limiter in `main.py` — it reads
that limiter's own sliding-window deque, so the numbers are its live state, not a
second counter kept in parallel.

```bash
curl -s -H "$H" "$DOMAIN/v1/diagnostics" | jq '.data.rateLimiting'
```

```jsonc
{
  "enabled": true,
  "backend": "in-memory sliding window (collections.deque, per-process)",
  "scope": "global — all callers share one window; not per-IP or per-key",
  "policy": "60 requests per 60s",
  "requestsInWindow": 60,        // what actually governs admission
  "remainingCapacity": 0,
  "utilization": 1.0,
  "currentlyLimiting": true,     // it is rejecting right now
  "windowResetInSeconds": 12.4,  // when capacity frees up
  "trackedEntries": 61,          // includes entries not yet pruned
  "expiredEntriesPendingPrune": 1,
  "rejectedResponsesObserved": 37
}
```

Reading this section is safe on a hot service: it takes O(1) length and endpoint
reads plus a scan that stops at the first non-expired entry, never mutates the
limiter, and makes no outbound calls.

Two fields need interpreting carefully:

- **`trackedEntries` vs `requestsInWindow`** — the limiter prunes expired entries
  lazily, only when a rate-limited path is hit. After traffic stops, entries linger.
  `requestsInWindow` excludes them and is the number that governs admission;
  `trackedEntries` is just what is physically held.
- **`rejectedResponsesObserved`** — the limiter itself keeps **no** rejection
  counter (its deque stores only *allowed* timestamps), so this is counted at the
  HTTP layer by the diagnostics middleware. It is exact rather than estimated,
  because `main.py`'s limiter is the only source of HTTP 429 in this service — the
  ARK 429 handled in `routes.py` is converted to a `500 STAGE1_API_ERROR` and never
  reaches the client as a 429. It resets on restart, like every other counter here.

Because the limiter is **global**, `currentlyLimiting: true` means *the service as a
whole* is at capacity — not one noisy client. There are no per-caller buckets to
inspect, and no client IPs are stored anywhere in the limiter.
