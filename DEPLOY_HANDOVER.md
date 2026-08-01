# Deployment Handover — Ghibli Portrait API

Everything the receiving team needs to run this service on a **new server with a
new domain**. Written to be self-contained: no prior context with this codebase
is assumed.

---

## 1. What must be set in `.env` (nothing else needs editing)

Copy `.env.example` to `.env` and set the values below. **No code, Dockerfile, or
docker-compose changes are required for a new server** — every deployment-specific
value is an environment variable.

| Variable | Required? | What to set it to |
|---|---|---|
| `DOMAIN` | **YES — must change** | The address **callers use to reach this service**. See §2 — getting this wrong is the single most common deployment failure. |
| `ARK_API_KEY` | **YES** | The BytePlus ARK key (provided separately — never commit it). |
| `DIAGNOSTICS_TOKEN` | **YES** | Provided separately alongside this document. Without it, `/v1/diagnostics` returns 401 for everyone (fails closed by design). |
| `ENVIRONMENT` | Recommended | `production` — appears in the diagnostics snapshot so prod is distinguishable from staging. |
| `HOST_PORT` | Optional | Host-side port published by docker-compose. Default `30820`. The container port is always `8010`. |
| `MEM_LIMIT` | Optional | Container memory ceiling, default `4g`. Do not drop below `4g` — `2g` was measured to get OOM-killed under a concurrent burst. |
| Everything else | No | Tuned defaults; see `.env.example` for the reasoning behind each. |

---

## 2. `DOMAIN` — the one value that breaks things if wrong

This service **returns image URLs that point back at itself**:

```
POST /v1/ghibli-qr  →  { "resultUrls": ["<DOMAIN>/tmp/final_xxx.jpg"] }
```

Those files are served by this same process (a StaticFiles mount at `/tmp`).
So `DOMAIN` must be an address **the calling client can actually open**.

If `DOMAIN` is wrong, the API still returns `200 OK` with a `resultUrls` array —
but every URL in it is unreachable for the caller. The failure is silent from the
API's point of view, which is why it is worth verifying explicitly (§4, step 3).

Pick the value that matches the deployment shape:

| Deployment shape | `DOMAIN` value |
|---|---|
| Public/reverse-proxied behind a domain | `https://api.yourdomain.com` |
| Direct on a server, callers reach it by IP:port | `http://<server-ip>:30820` |
| Kubernetes, caller is another pod in the cluster | `http://<service-name>.<namespace>.svc.cluster.local:8010` |

No trailing slash. Use `https://` whenever the caller reaches it over TLS —
the scheme is copied verbatim into every returned URL.

---

## 3. Running it

```bash
cp .env.example .env
# edit .env — set DOMAIN, ARK_API_KEY, DIAGNOSTICS_TOKEN, ENVIRONMENT

docker compose build
docker compose up -d
```

Startup takes ~25s: the CLIP model is preloaded during startup so the first real
request does not pay that cost. The container reports `healthy` only after the
preload finishes.

Optional, to make the running build identifiable in diagnostics:

```bash
docker compose build --build-arg GIT_COMMIT=$(git rev-parse --short=12 HEAD)
```

---

## 4. Post-deploy verification (run in this order)

Replace `<BASE>` with the service address and `<TOKEN>` with `DIAGNOSTICS_TOKEN`.

**Step 1 — the process is up**
```bash
curl -s <BASE>/v1/health
```
Expect: `{"success":true,"data":{"status":"healthy"},...}`

**Step 2 — models loaded and config correct**
```bash
curl -s -H "X-Diagnostics-Token: <TOKEN>" <BASE>/v1/diagnostics
```
Check in the response:
- `health.status` = `"healthy"`
- `models.clipLoaded` = `true`, `models.qreaderLoaded` = `true`
- `config.arkApiKeyConfigured` = `true`
- `config.domain` = the value you set (verify it is not a leftover)

**Step 3 — full generation, and the returned URL actually opens** ← catches a wrong `DOMAIN`
```bash
curl -s -X POST <BASE>/v1/ghibli-qr \
  -H "Content-Type: application/json" \
  -d '{"imgUrl":"https://images.pexels.com/photos/17651327/pexels-photo-17651327.jpeg","url":"https://example.com/test"}'
```
Expect `"success":true` and `"qrValidation":{"ok":true,...}`. Takes 35–60s
(two sequential calls to the external generation API — most of that is network wait,
not CPU).

Then take `resultUrls[0]` from the response and open it **from the machine that
will really be calling this API**:
```bash
curl -s -o /dev/null -w "%{http_code}\n" "<resultUrls[0]>"
```
Expect `200`. Anything else means `DOMAIN` is wrong for this caller — fix `.env`
and `docker compose up -d` (no rebuild needed).

**Step 4 — validation rejects bad input (free, no generation cost)**
```bash
curl -s -X POST <BASE>/v1/ghibli-qr \
  -H "Content-Type: application/json" \
  -d '{"imgUrl":"https://i.ibb.co/pjSfGq2Z/bear.jpg","url":"https://example.com/x"}'
```
Expect `422` with `"code":"NO_FACE_DETECTED"` — input validation runs before any
billed call, so rejected images cost nothing.

**Step 5 — resources**
```bash
docker stats ghibli-api-v1 --no-stream
```
Expect ~1–1.5GB idle against the 4GB limit. Roughly 1GB of that is the CLIP model,
resident for the process lifetime by design.

---

## 5. Operational notes

**Single worker is mandatory.** `--workers 1` is pinned in the Dockerfile. The
in-process `pending_tasks` registry and all concurrency limiters are per-process,
so a second worker would double the effective rate/concurrency ceilings and break
result delivery. To scale, run more replicas (each with `--workers 1`), and note
that per-replica limits then multiply — see the ceiling on the external
generation API before increasing replica count.

**Diagnostics access.** `/v1/diagnostics` is always registered and visible in
Swagger; access is controlled solely by `DIAGNOSTICS_TOKEN`. An unset token denies
every request. Secrets never appear in its output — API keys are reported as a
presence boolean plus a non-invertible fingerprint.

**Rate limiting.** Enabled by default: 60 requests/60s on the two generation
endpoints only, as a cost safety net. Health checks and the QR utility routes are
never rate-limited. Tunable via `RATE_LIMIT_*` in `.env`.

**Restricting access.** If the service should not be publicly reachable, close the
port at the network layer (firewall / security group / ClusterIP-only). Do **not**
rely on the app-level `PRIVATE_MODE` + `ALLOWED_IPS` for that: it compares the
direct TCP peer address, which becomes the proxy's address once anything sits in
front of the service, and pod IPs are not stable in Kubernetes.

**Storage.** Generated images are written to a Docker volume at
`/app/src/static/tmp`. `PERSIST_FINAL_IMAGES=true` (the default) means final
composites are never auto-deleted, so that directory grows without bound — worth a
periodic check, or move delivery to object storage if volume becomes a concern.
Intermediate files (`stage1_`, `qrlock_`) are cleaned on their own TTLs.

---

## 6. Handled separately (not in this repo)

- `ARK_API_KEY` — the paid generation credential.
- `DIAGNOSTICS_TOKEN` — provided with this handover.

Neither is committed; `.env` is gitignored. Rotate both if they are ever exposed.
