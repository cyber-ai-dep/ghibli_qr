# Quick Setup — Ghibli Portrait API (Docker)

Fast, copy-paste guide to run the API in Docker. For full details see
[README.md](README.md).

---

## 1. Prerequisites

- Docker 20.10+ and Docker Compose v2 — verify: `docker-compose --version`
- A **BytePlus ARK (Seedream) API key**
- `src/static/lock.png` present as a real PNG (bundled in the repo)

> No ngrok / public callback needed — the BytePlus ARK generation API is
> **synchronous** (the result comes back inline).

---

## 2. Configure environment

```bash
git clone <repo-url>
cd ghibli_qr
cp .env.example .env
```

Edit `.env` and set:

```env
DOMAIN=http://<this-server-address>:30820   # base address for returned image URLs, no trailing slash
ARK_API_KEY=your_ark_api_key
```

Everything else has safe defaults. `DOMAIN` is only used to build the URLs
returned to clients (set it to wherever this server is reachable —
`http://localhost:30820` locally, or `http://<public-ip>:30820` on a server).

---

## 3. Build and run

```bash
docker-compose up -d --build
```

First build takes ~3–5 min. Then verify:

```bash
curl http://localhost:30820/v1/health
# {"success":true,"data":{"status":"healthy"}, ...}
```

- Swagger UI: `http://localhost:30820/docs`

---

## 4. Smoke test the full pipeline

```bash
curl -X POST http://localhost:30820/v1/ghibli-qr \
  -H 'Content-Type: application/json' \
  -d '{"imgUrl":"https://your-host/real_portrait.jpg","url":"https://example.com"}'
```

Use a real human portrait. Heavily compressed images can trip the
`NOT_REAL_PHOTO` filter.

---

## 5. Everyday commands

| Command | When |
|---|---|
| `docker-compose up -d --build` | After any code change or `git pull` |
| `docker-compose down && docker-compose up -d` | After editing `.env` |
| `docker-compose logs -f ghibli-api` | Live logs |
| `docker-compose ps` | Container + health status |
| `HOST_PORT=8090 docker-compose up -d` | Run on a different host port |

---

## 6. Local image saving (optional)

To also keep a copy of every final image on the machine (besides the served URL):

```env
SAVE_OUTPUT_LOCAL=true
OUTPUT_DIR=output
```

---

## 7. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `git pull` changes don't take effect | Use `docker-compose up -d --build`, not `restart` |
| `.env` changes ignored | `docker-compose down && docker-compose up -d` |
| Port already in use | `HOST_PORT=8090 docker-compose up -d` |
| Stage fails: `IsADirectoryError ... lock.png` | `src/static/lock.png` must be a real PNG, not an empty dir |
| `STAGE1_API_ERROR` / rate-limit | Lower `GENERATION_CONCURRENCY_LIMIT` (ARK allows ≤10 concurrent/model) |
| Container `unhealthy` | `docker-compose logs ghibli-api` |

---

## 8. Required asset: `lock.png`

Stage 2 composes the QR onto `src/static/lock.png` (read via `config.LOCK_PATH`).
It must exist as a PNG before the first `docker-compose up`. Verify:

```bash
file src/static/lock.png   # → PNG image data
```
