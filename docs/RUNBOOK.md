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
