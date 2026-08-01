#!/usr/bin/env bash
#
# Post-deploy smoke test for the Ghibli Portrait API.
#
# Portable on purpose: no jq, no python, no repo checkout required — only curl and
# a POSIX shell, so it runs unchanged on a bare VPS, inside the container, or from
# a debug pod in Kubernetes.
#
# Runnable from any directory — it reads nothing relative to itself.
#
# Usage:
#   ./smoke_test.sh                                   # defaults to http://127.0.0.1:30820
#   BASE_URL=http://ghibli-api:8010 ./smoke_test.sh   # from another container / pod
#   BASE_URL=https://api.example.com ./smoke_test.sh  # through a proxy
#
#   DIAGNOSTICS_TOKEN=<token> ./smoke_test.sh         # also exercises /v1/diagnostics
#   SKIP_GENERATION=1 ./smoke_test.sh                 # skip the one billed test (§4)
#   WAIT_SECONDS=90 ./smoke_test.sh                   # allow longer for a cold start
#
# If DIAGNOSTICS_TOKEN is not exported, the script looks for a .env in a few
# usual locations (ENV_FILE=... to point at one explicitly). That is only a
# convenience for running on the deployment host; it never fails if absent.
#
# Exit status is 0 only if every test passed, so this is safe to gate a rollout on.
#
# Cost note: exactly ONE test performs a real (billed) generation. Everything else
# is free — rejected images never reach the generation provider.

set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:30820}"
BASE_URL="${BASE_URL%/}"
DIAGNOSTICS_TOKEN="${DIAGNOSTICS_TOKEN:-}"
SKIP_GENERATION="${SKIP_GENERATION:-0}"
# Startup preloads the CLIP model, so a container is reachable but not yet
# answering for ~25s after `up`. Waiting here is what makes the script safe to
# run immediately after a deploy instead of failing on a cold start.
WAIT_SECONDS="${WAIT_SECONDS:-60}"

# Best-effort token discovery, so the caller does not have to be in the project
# directory. Explicit ENV_FILE wins; otherwise try the usual deploy paths.
if [ -z "$DIAGNOSTICS_TOKEN" ]; then
  for _f in "${ENV_FILE:-}" ./.env /root/ghibli_qr/.env /opt/ghibli_qr/.env /app/.env; do
    [ -n "$_f" ] && [ -r "$_f" ] || continue
    _t=$(sed -n 's/^DIAGNOSTICS_TOKEN=//p' "$_f" | head -1 | tr -d '\r"'"'"' ')
    if [ -n "$_t" ]; then DIAGNOSTICS_TOKEN="$_t"; DIAG_TOKEN_SRC="$_f"; break; fi
  done
fi

# A real single-person portrait (passes validation) and a bear (rejected).
HUMAN_IMG="${HUMAN_IMG:-https://images.pexels.com/photos/17651327/pexels-photo-17651327.jpeg}"
REJECT_IMG="${REJECT_IMG:-https://i.ibb.co/pjSfGq2Z/bear.jpg}"

PASS=0
FAIL=0

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else G=""; R=""; Y=""; B=""; N=""; fi

ok()   { PASS=$((PASS+1)); printf "  ${G}PASS${N}  %s\n" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf "  ${R}FAIL${N}  %s\n" "$1"; [ $# -gt 1 ] && printf "        %s\n" "$2"; }
skip() { printf "  ${Y}SKIP${N}  %s\n" "$1"; }
head_() { printf "\n${B}%s${N}\n" "$1"; }

# Extract a JSON string value without jq: "key":"value" -> value
jget() { printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1; }
# Extract the first element of a JSON string array: "key":["value",...] -> value
jarr() { printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\[[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1; }
has()  { printf '%s' "$1" | grep -q "$2"; }

printf "${B}Ghibli Portrait API — smoke test${N}\n"
printf "Target: %s\n" "$BASE_URL"

# ---------------------------------------------------------------------------
head_ "1. Health"
# ---------------------------------------------------------------------------
WAITED=0
while :; do
  BODY=$(curl -s --max-time 10 "$BASE_URL/v1/health" 2>/dev/null)
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/v1/health" 2>/dev/null)
  { [ "$CODE" = "200" ] && has "$BODY" '"status":"healthy"'; } && break
  [ "$WAITED" -ge "$WAIT_SECONDS" ] && break
  [ "$WAITED" -eq 0 ] && printf "  ...waiting for startup (CLIP preload), up to %ss\n" "$WAIT_SECONDS"
  sleep 3
  WAITED=$((WAITED+3))
done

if [ "$CODE" = "200" ] && has "$BODY" '"status":"healthy"'; then
  [ "$WAITED" -gt 0 ] && ok "GET /v1/health -> 200 healthy (ready after ${WAITED}s)" \
                      || ok "GET /v1/health -> 200 healthy"
else
  bad "GET /v1/health -> $CODE" "$BODY"
  printf "\n${R}Service is not reachable after %ss — stopping here.${N}\n" "$WAIT_SECONDS"
  printf "Check: container running (docker ps), BASE_URL correct for where you are\n"
  printf "running this, and the port reachable from here. Note 127.0.0.1 is NOT\n"
  printf "shared with other containers — from a container use http://ghibli-api:8010.\n"
  exit 1
fi

# ---------------------------------------------------------------------------
head_ "2. Free endpoints (no generation cost)"
# ---------------------------------------------------------------------------
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL/v1/qr-url/?url=https://example.com" 2>/dev/null)
[ "$CODE" = "200" ] && ok "GET /v1/qr-url/ -> 200" || bad "GET /v1/qr-url/ -> $CODE"

QR=$(curl -s --max-time 30 -X POST "$BASE_URL/v1/qr-lock" \
      -H 'Content-Type: application/json' \
      -d '{"url":"https://example.com/smoke"}' 2>/dev/null)
QR_URL=$(jget "$QR" "qrUrl")
if [ -n "$QR_URL" ]; then
  ok "POST /v1/qr-lock -> 200"
  # The returned URL must be fetchable FROM HERE — this is what catches a wrong
  # DOMAIN / proxy setup, since the API returns links to files it serves itself.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$QR_URL" 2>/dev/null)
  if [ "$CODE" = "200" ]; then
    ok "returned asset URL is fetchable ($QR_URL)"
  else
    bad "returned asset URL not fetchable -> $CODE" \
        "URL was: $QR_URL — the caller cannot open what the API handed back. Check DOMAIN / X-Forwarded-* headers."
  fi
else
  bad "POST /v1/qr-lock" "$QR"
fi

# ---------------------------------------------------------------------------
head_ "3. Input validation (free — rejected before any billed call)"
# ---------------------------------------------------------------------------
BODY=$(curl -s --max-time 60 -X POST "$BASE_URL/v1/ghibli-qr" \
        -H 'Content-Type: application/json' \
        -d "{\"imgUrl\":\"$REJECT_IMG\",\"url\":\"https://example.com/x\"}" 2>/dev/null)
if has "$BODY" 'NO_FACE_DETECTED' || has "$BODY" 'NOT_REAL_PHOTO'; then
  ok "non-human image rejected before generation"
else
  bad "non-human image was not rejected as expected" "$BODY"
fi

BODY=$(curl -s --max-time 20 -X POST "$BASE_URL/v1/ghibli-qr" \
        -H 'Content-Type: application/json' \
        -d '{"imgUrl":"http://localhost/x.jpg","url":"https://example.com/x"}' 2>/dev/null)
has "$BODY" 'INVALID_IMAGE_URL' \
  && ok "SSRF guard rejects localhost imgUrl" \
  || bad "SSRF guard did not reject localhost imgUrl" "$BODY"

# ---------------------------------------------------------------------------
head_ "4. Full generation (THE ONLY BILLED TEST — ~35-60s)"
# ---------------------------------------------------------------------------
if [ "$SKIP_GENERATION" = "1" ]; then
  skip "SKIP_GENERATION=1 — pipeline not exercised end to end"
else
  BODY=$(curl -s --max-time 240 -X POST "$BASE_URL/v1/ghibli-qr" \
          -H 'Content-Type: application/json' \
          -d "{\"imgUrl\":\"$HUMAN_IMG\",\"url\":\"https://example.com/smoke-test\"}" 2>/dev/null)

  if has "$BODY" '"success":true'; then
    ok "POST /v1/ghibli-qr -> success"

    has "$BODY" '"ok":true' \
      && ok "QR in the composite decodes back to the requested URL" \
      || bad "qrValidation.ok is not true" "$BODY"

    RESULT_URL=$(jarr "$BODY" "resultUrls")
    if [ -n "$RESULT_URL" ]; then
      CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$RESULT_URL" 2>/dev/null)
      SIZE=$(curl -s -o /dev/null -w '%{size_download}' --max-time 30 "$RESULT_URL" 2>/dev/null)
      if [ "$CODE" = "200" ] && [ "${SIZE:-0}" -gt 10000 ]; then
        ok "generated image downloads ($SIZE bytes) from $RESULT_URL"
      else
        bad "generated image not downloadable -> HTTP $CODE, $SIZE bytes" "URL: $RESULT_URL"
      fi
    else
      bad "no resultUrls in response" "$BODY"
    fi
  else
    bad "POST /v1/ghibli-qr did not succeed" "$BODY"
  fi
fi

# ---------------------------------------------------------------------------
head_ "5. Diagnostics"
# ---------------------------------------------------------------------------
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL/v1/diagnostics" 2>/dev/null)
[ "$CODE" = "401" ] \
  && ok "unauthenticated /v1/diagnostics -> 401 (fails closed)" \
  || bad "unauthenticated /v1/diagnostics -> $CODE (expected 401)"

if [ -z "$DIAGNOSTICS_TOKEN" ]; then
  skip "DIAGNOSTICS_TOKEN not found — authenticated checks not run"
  skip "  (export DIAGNOSTICS_TOKEN=... or ENV_FILE=/path/to/.env)"
else
  [ -n "${DIAG_TOKEN_SRC:-}" ] && printf "        token read from %s\n" "$DIAG_TOKEN_SRC"
  BODY=$(curl -s --max-time 30 -H "X-Diagnostics-Token: $DIAGNOSTICS_TOKEN" \
          "$BASE_URL/v1/diagnostics?logLimit=0" 2>/dev/null)
  if has "$BODY" '"success":true'; then
    ok "authenticated /v1/diagnostics -> 200"
    has "$BODY" '"clipLoaded":true'    && ok "CLIP model loaded"     || bad "CLIP model NOT loaded"
    has "$BODY" '"qreaderLoaded":true' && ok "QR reader loaded"      || bad "QR reader NOT loaded"
    has "$BODY" '"arkApiKeyConfigured":true' && ok "generation API key configured" \
                                             || bad "generation API key NOT configured"
    # Retention: final_* must be exempt from TTL cleanup, or delivered image URLs
    # start 404-ing once the TTL elapses.
    has "$BODY" '"persistFinalImages":true' \
      && ok "persistFinalImages=true — delivered images are never TTL-deleted" \
      || bad "persistFinalImages is NOT true" \
             "Final composites will be deleted after finalImageTtlHours, breaking URLs already returned to callers."
    printf "        reported domain: %s\n" "$(jget "$BODY" 'domain')"
  else
    bad "authenticated /v1/diagnostics failed" "$BODY"
  fi
fi

# ---------------------------------------------------------------------------
printf "\n${B}────────────────────────────${N}\n"
if [ "$FAIL" -eq 0 ]; then
  printf "${G}${B}ALL PASSED${N}  (%d checks)\n" "$PASS"
  exit 0
else
  printf "${R}${B}%d FAILED${N}, %d passed\n" "$FAIL" "$PASS"
  exit 1
fi
