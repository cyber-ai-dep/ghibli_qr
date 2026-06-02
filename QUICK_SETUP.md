# Quick Setup — Ghibli Portrait API (Docker)

Fast, copy-paste guide to run the API in Docker. For full architecture and API
details see [README.md](README.md).

---

## 1. Prerequisites

- Docker 20.10+ and Docker Compose v2 — verify: `docker-compose --version`
- A **public HTTPS URL** for KIE webhooks (production domain or ngrok for local)
- A **KIE.ai API key**

---

## 2. Configure environment

```bash
git clone https://github.com/cyber-ai-dep/ghibli_qr
cd ghibli_qr
cp .env.example .env
```

Edit `.env` and set the two required values:

```env
DOMAIN=https://your-public-url            # NO trailing slash
KIE_API_KEY=your_kie_api_key
```

Models are already set in `.env.example`:
- `KIE_GHIBLI_MODEL` — Stage 1 (portrait → Ghibli)
- `KIE_COMPOSE_MODEL=seedream/4.5-edit` — Stage 2 (Ghibli + QR)

> Every other variable has a safe default. `.env`, `.env.example`, and the code
> read the **same keys** — keep them in sync when adding new settings.

---

## 3. (Local only) Expose the server with ngrok

KIE delivers results via webhook to `{DOMAIN}/v1/ghibli/callback`, so `DOMAIN`
must be reachable from the internet. The container publishes host port **30820**:

```bash
ngrok http 30820
```

Copy the `https://....ngrok-free.app` URL into `DOMAIN` in `.env`.
**The ngrok URL and `DOMAIN` must match exactly**, or every task times out.

---

## 4. Build and run

```bash
docker-compose up -d --build
```

First build takes ~3–5 min (base image + dependencies). Then verify:

```bash
curl http://localhost:30820/v1/health
# {"success":true,"data":{"status":"healthy"}, ...}
```

- Swagger UI: `http://localhost:30820/docs`
- Via ngrok: `https://<your-ngrok>.ngrok-free.app/docs`

---

## 5. Smoke test the full pipeline

```bash
curl -X POST http://localhost:30820/v1/ghibli-qr \
  -H 'Content-Type: application/json' \
  -d '{"imgUrl":"https://your-host/real_portrait.jpg","url":"https://example.com"}'
```

Use a **real, uncompressed human portrait** — heavily compressed images (e.g.
WhatsApp exports) can trip the `NOT_REAL_PHOTO` synthetic-image filter.

To fire all bundled test images at once:

```bash
uv run python test_concurrent.py            # full Ghibli+QR pipeline
uv run python test_concurrent.py --endpoint /v1/ghibli   # Stage 1 only (faster)
```

---

## 6. Everyday commands

| Command | When to use |
|---|---|
| `docker-compose up -d --build` | **After any code change or `git pull`** — rebuilds the image |
| `docker-compose down && docker-compose up -d` | After editing `.env` |
| `docker-compose restart` | Restart process only — does **not** reload code or `.env` |
| `docker-compose logs -f ghibli-api` | Live logs |
| `docker-compose ps` | Container + health status |
| `docker-compose exec ghibli-api bash` | Shell inside the container |
| `HOST_PORT=8090 docker-compose up -d` | Run on a different host port |

> Code is baked into the image at build time (`COPY . .`). `restart` keeps the
> old image — always use `up -d --build` to load new code.

---

## 7. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `git pull` changes don't take effect | Use `docker-compose up -d --build`, not `restart` |
| `.env` changes ignored | `docker-compose down && docker-compose up -d` |
| Port already in use | `HOST_PORT=8090 docker-compose up -d` |
| Crash loop: `No route to host` to github on startup | Stale compose network — `docker-compose down && docker-compose up -d` recreates it. First boot downloads the QR-detector model and needs internet. |
| Pipeline times out at Stage 1 | `DOMAIN` not reachable by KIE — test externally: `curl https://<DOMAIN>/v1/health` |
| Stage 2 fails: `IsADirectoryError ... lock.png` | The lock overlay asset is missing. Ensure `src/ghibli_portrait/static/lock.png` is a **real PNG file**, not an empty directory (Docker creates an empty dir at the bind-mount source if the file is absent). |
| Container `unhealthy` | `docker-compose logs ghibli-api` |

---

## 8. Required asset: `lock.png`

Stage 2 composes the QR code onto a lock-screen template at
`src/ghibli_portrait/static/lock.png`. This **must exist as a PNG file** before
the first `docker-compose up` — `docker-compose.yml` bind-mounts it read-only,
and Docker will auto-create an empty **directory** in its place if the file is
missing, which breaks Stage 2. Verify with:

```bash
file src/ghibli_portrait/static/lock.png   # → PNG image data
```
