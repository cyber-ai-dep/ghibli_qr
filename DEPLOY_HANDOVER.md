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

## 2. `DOMAIN` and how returned URLs are built

This service **returns image URLs that point back at itself** — `resultUrls`,
`stage1Url`, `qrUrl` are all served by this same process (a StaticFiles mount at
`/tmp`), so they have to be addresses the caller can actually open.

**Those URLs are derived per request, not from `DOMAIN`.** With
`PUBLIC_URL_FROM_REQUEST=true` (the default) the base address comes from the
request's `Host` / `X-Forwarded-Proto` / `X-Forwarded-Host` headers. One running
instance therefore serves all of these correctly at the same time, with no
configuration change:

| How the caller reaches it | What it gets back |
|---|---|
| `http://<ip>:30820` | `http://<ip>:30820/tmp/final_xxx.jpg` |
| in-cluster `http://ghibli-api:8010` | `http://ghibli-api:8010/tmp/final_xxx.jpg` |
| HTTPS Ingress `https://api.example.com` | `https://api.example.com/tmp/final_xxx.jpg` |

Moving from IP to domain, or from HTTP to HTTPS, needs **no change here**.

### Where `DOMAIN` still matters

Two places, and the second is the one that makes it a required value:

1. **Fallback** for the URLs above, used only when a request arrives with no
   usable `Host` header. Normal traffic never reaches this path.
2. **The base of the short URL** from `/v1/qr-url`, and — the important one —
   the URL that `/v1/qr-lock` **encodes into the QR image** when
   `shorten_url=true`. That gets scanned by a phone, so it must be reachable
   from the public internet. This is deliberately *not* request-derived: an
   in-cluster caller would otherwise get `http://ghibli-api:8010/abc12345`
   baked into the QR, which no phone can open.

So set `DOMAIN` to the address **end users** should reach (no trailing slash,
scheme included), not the address the calling service happens to use.

### At the TLS cutover

`DOMAIN` is the only value to revisit. Change it from `http://host:port` to
`https://host`. Image URLs switch to `https` on their own because they follow
the request; the QR payload does not, which is why this one needs editing.

Also confirm `FORWARDED_ALLOW_IPS=*` is set (it is, in the handover env file and
`k8s/configmap.yaml`). It is read by uvicorn, whose default only trusts
`127.0.0.1`, so without it uvicorn discards `X-Forwarded-Proto` from a proxy and
emits redirects with an `http://` `Location` — which browsers block as mixed
content. Verify after cutover:

```bash
curl -sI -H 'X-Forwarded-Proto: https' https://<host>/v1/qr-url | grep -i location
# must print https://, not http://
```

---

## 3. Running it

```bash
cp .env.production .env      # the file handed over with this repo, not .env.example
docker compose build
docker compose up -d
```

`.env.production` already carries every value verified working, including the two
secrets. Use it as-is — `.env.example` is a documented template for a fresh
environment, and following `cp .env.example .env` instead would silently drop
`BIND_ADDRESS` and `FORWARDED_ALLOW_IPS` (binding the service to loopback and
breaking HTTPS redirects).

Then verify with the smoke test, which checks 15 things including a real
generation and that the returned image URL opens:

```bash
BASE_URL=http://<host>:30820 ./scripts/smoke_test.sh
SKIP_GENERATION=1 BASE_URL=http://<host>:30820 ./scripts/smoke_test.sh   # zero-cost run
```

Exit code 0 means every check passed. Run it from the host, not inside the
container — the image has no `curl`.

Startup takes ~25s: the CLIP model is preloaded during startup so the first real
request does not pay that cost. The container reports `healthy` only after the
preload finishes.

Optional, to make the running build identifiable in diagnostics:

```bash
docker compose build --build-arg GIT_COMMIT=$(git rev-parse --short=12 HEAD)
```

### Running it on Kubernetes

The manifests in `k8s/` are a working reference, not a requirement — if you
deploy with your own charts and conventions, use them and take only the facts in
the table below. **These are properties of the image and the application, so
they apply to any manifest, including your own.**

| Fact | Value | Why it matters |
|---|---|---|
| Container port | `8010` | `EXPOSE`d by the image. |
| Health path | `GET /v1/health` | Liveness *and* readiness. Startup blocks on the CLIP preload, so a ready Pod is by definition live. |
| Startup time | 25–60s | Set `initialDelaySeconds` past this or the Pod is killed while loading. |
| Runtime UID/GID | `999` / `999` | Pinned numerically in the Dockerfile. **Required**: with `runAsNonRoot: true` — which the `restricted` Pod Security Standard mandates — the kubelet cannot verify a *named* user and refuses to start the container with `CreateContainerConfigError`. |
| `fsGroup` | `999` | The image writes generated images to `/app/src/static/tmp`. Most CSI drivers present a fresh volume as `root:root 0755`, and the mount covers the Dockerfile's `chown`. Without `fsGroup` the Pod goes **Ready and stays Ready** while every generation fails to write. |
| Storage | `ReadWriteOnce` PVC at `/app/src/static/tmp` | Delivered composites are never TTL-deleted (`PERSIST_FINAL_IMAGES=true`), because deleting them 404s URLs already handed out. An `emptyDir` destroys them on every restart — silently. |
| Replicas | `1`, strategy `Recreate` | `--workers 1` is a hard constraint: `pending_tasks`, the rate limiter, and every concurrency semaphore are per-process. Two Pods double the configured ceilings (120 req/60s instead of 60), and a RollingUpdate against an RWO volume can hang indefinitely on a Multi-Attach error. |
| Memory | request `2Gi`, limit `4Gi` | Measured, not estimated: a 40-request burst peaked at ~2.08GB, and a 2GB ceiling was OOM-killed mid-burst. ~1GB of that is the resident CLIP model. |
| CPU | request `1`, limit `2` | A floor, not a tuned figure. Raise together with `CLIP_CONCURRENCY_LIMIT`. |
| Private access | `ClusterIP`, no Ingress | The supported way. Do **not** use the app's `PRIVATE_MODE`/`ALLOWED_IPS` — they compare the direct TCP peer, which becomes the proxy in a cluster, and Pod IPs are not stable. |

Config comes from a ConfigMap + Secret via `envFrom`. Three keys in the handover
`.env` are read by `docker-compose.yml` and **not** by the application, so they
do nothing here: `BIND_ADDRESS`, `HOST_PORT`, `MEM_LIMIT`. Every other key works
identically in both environments.

Note that `config.py` calls `load_dotenv()` with `override=False`, so a real
environment variable always wins over a mounted `.env` file. If you supply both,
the ConfigMap silently takes precedence rather than conflicting visibly — pick
one.

```bash
kubectl apply -f k8s/          # ingress.yaml is under k8s/optional/ and is skipped
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

**Step 3 — full generation, and the returned URL actually opens** ← catches broken image delivery
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
Expect `200`. This step matters because a generation can succeed and still hand
back a URL nobody can open — the API returns `200 OK` either way, so the failure
is invisible from the API's side.

If it is not `200`, work through these in order — note that `DOMAIN` is *not*
the likely cause, since these URLs follow the request (§2):

| Symptom | Likely cause |
|---|---|
| `404` on the image, API healthy | Storage not mounted where the app writes. Under Kubernetes the PVC (`k8s/pvc.yaml`) must be mounted at `/app/src/static/tmp`; an `emptyDir` loses every delivered image on restart. |
| `404` only for some paths | Ingress path rule not forwarding `/tmp/` to this Service. |
| `502` / `504` | Ingress read timeout below the 35–60s a generation takes. `k8s/optional/ingress.yaml` sets 120s. |
| Connection refused at the address in the URL | Service port mismatch — the Service publishes `8010` to match every address in this document. |
| `http://` URL rejected by a browser on an HTTPS page | `FORWARDED_ALLOW_IPS` not set (§2). |
| `500` with `[Errno 13] Permission denied` from the generation call itself | The mounted volume is owned by another user while the process runs as UID `999`. Docker only applies image ownership to an **empty named volume** — a host bind mount, or a pre-existing volume from an older root-running image, arrives root-owned and unwritable. Costly, because the billed generation runs *before* the save. Fix under Compose: `docker run --rm -v <volume>:/v alpine chown -R 999:999 /v`; under Kubernetes: `fsGroup: 999`. Since this version the container refuses to start in that state and logs the cause, rather than failing per request. |

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
