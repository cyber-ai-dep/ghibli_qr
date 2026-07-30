import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)


def _parse_ttl_hours(env_var: str, default: int) -> int:
    """Parse a positive-integer TTL (hours) from an env var.

    Returns the default and logs a warning when the value is missing,
    non-integer, or non-positive. Using a helper keeps individual
    Settings assignments clean and easy to extend with new prefixes later.
    """
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (ValueError, TypeError):
        _log.warning("%s has non-integer value %r — using default %dh", env_var, raw, default)
        return default
    if val <= 0:
        _log.warning("%s must be > 0, got %d — using default %dh", env_var, val, default)
        return default
    return val


class Settings:
    # QR Settings
    QR_VERSION = 1  # Ranges from 1 - 40
    QR_FILL_COLOR = "white"
    QR_BACK_COLOR = "#2a2d42"
    QR_SHORT_CODE_LENGTH = int(os.getenv('SHORT_CODE_LENGTH', 8))

    # QR lock proportional sizing ratios (relative to the lock image width)
    QR_LOCK_TARGET_WIDTH_RATIO = 0.28
    QR_LOCK_MIN_WIDTH_RATIO = 0.22
    QR_LOCK_MAX_WIDTH_RATIO = 0.32

    # Paths — static assets and the served temp directory.
    BASE_PATH = Path(__file__).parent.parent
    STATIC_PATH = BASE_PATH / 'static'
    LOCK_PATH = STATIC_PATH / 'lock.png'
    TMP_PATH = STATIC_PATH / 'tmp'

    # Optional: also save final images on THIS machine (not only served via URL).
    # When SAVE_OUTPUT_LOCAL is enabled, every final_ image is additionally written
    # to OUTPUT_DIR. The API response and the served /tmp URL are unchanged.
    SAVE_OUTPUT_LOCAL = os.getenv("SAVE_OUTPUT_LOCAL", "false").lower() in {"1", "true", "yes"}
    OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))

    # Real generation model per stage (BytePlus ARK / Seedream). Set in .env; this
    # exact value is the model sent to ARK AND reported in the response "model" field.
    GHIBLI_MODEL = os.getenv("GHIBLI_MODEL", "seedream-4-5-251128")    # Stage 1 (portrait → Ghibli)
    COMPOSE_MODEL = os.getenv("COMPOSE_MODEL", "seedream-4-5-251128")  # Stage 2 (Ghibli + QR composition)

    # Server Settings — public/base address used to build the returned image URLs.
    DOMAIN = os.getenv("DOMAIN")

    # Validation Settings
    # If enabled, requests without a detectable face are rejected before generation.
    REQUIRE_HUMAN_FACE = os.getenv("REQUIRE_HUMAN_FACE", "true").lower() in {"1", "true", "yes"}

    # Max concurrent CLIP classification operations (Stage 1 human-portrait gate).
    # CLIP inference is pinned to 1 torch thread per call (see
    # clip_validation_service._load_clip), so this sizes real parallelism —
    # start near the vCPU count with a small margin, and lower it further on a
    # shared host (e.g. a VPS also running the backend/DB) so a CLIP burst
    # cannot starve those other processes of every core.
    CLIP_CONCURRENCY_LIMIT = int(os.getenv("CLIP_CONCURRENCY_LIMIT", "4"))

    # Max concurrent image-generation submissions to the provider (BytePlus ARK).
    # Official BytePlus limit is 500 IPM/RPM (throughput, not a connection cap) —
    # see docs.byteplus.com/en/docs/ModelArk/1824718. This is I/O-bound (network
    # wait), not CPU-bound, so it can safely run higher than CLIP_CONCURRENCY_LIMIT
    # even on a CPU-constrained shared host. 24 measured (2026-07-22 load test):
    # cut avg pipeline time 113.7s -> 48.0s and queue-wait 59.9% -> 4.4% vs the
    # old default of 8, with zero 429s — 24 req/batch still stays far under the
    # 500/min ceiling. Raise further only after confirming with a fresh load test.
    GENERATION_CONCURRENCY_LIMIT = int(os.getenv("GENERATION_CONCURRENCY_LIMIT", "24"))

    # Max concurrent Layer-2 image downloads (user-provided imgUrl fetch).
    # Previously unbounded — a burst of simultaneous requests (accidental batch
    # call, misconfigured internal caller) would open unlimited outbound
    # connections at once. I/O-bound like generation, so this can stay generous;
    # it exists as an internal safety cap, not a public-facing rate limit.
    DOWNLOAD_CONCURRENCY_LIMIT = int(os.getenv("DOWNLOAD_CONCURRENCY_LIMIT", "24"))

    # Enable post-generation identity drift check. Disable when the model triggers
    # false positives consistently. Re-enable with a strong identity-preserving model.
    ENABLE_IDENTITY_CHECK = os.getenv("ENABLE_IDENTITY_CHECK", "false").lower() in {"1", "true", "yes"}

    # Optional app-level access control, independent of any network/firewall
    # setup. Default is fully open (matches today's behavior exactly, zero
    # risk). Flip PRIVATE_MODE=true + set ALLOWED_IPS to restrict every route
    # except /v1/health to a fixed set of caller IPs — no code change needed,
    # just a .env edit + container restart. See main.py's access-control
    # middleware for the enforcement and the /v1/health exemption rationale.
    PRIVATE_MODE = os.getenv("PRIVATE_MODE", "false").lower() in {"1", "true", "yes"}
    # Comma-separated allowed client IPs, only read when PRIVATE_MODE=true.
    ALLOWED_IPS = {ip.strip() for ip in os.getenv("ALLOWED_IPS", "").split(",") if ip.strip()}

    # Rate limiting on the two billed generation endpoints (/v1/ghibli,
    # /v1/ghibli-qr) — a safety net against runaway ARK cost from a bug, a
    # retry loop, or misconfiguration on the caller's side. This is DIFFERENT
    # from GENERATION_CONCURRENCY_LIMIT: the semaphore caps how many requests
    # run AT ONCE; this caps how many can be SUBMITTED over time, regardless
    # of concurrency. Default cap (60 req / 60s) sits well above the real
    # sustained throughput this service can produce (~24 concurrent requests
    # at ~48s avg each, per the 2026-07-22 load test, is roughly 30 req/min at
    # full capacity) while still catching a genuine runaway (thousands/min).
    # Enabled by default — unlike PRIVATE_MODE, an unset rate limit provides
    # zero protection, so "off by default" would defeat the point.
    RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes"}
    RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "60"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    # TTL for files in static/tmp/ — differentiated by filename prefix.
    # stage1_* and qrlock_* are intermediate assets; final_* are client deliverables.
    # All values must be positive integers (hours). Invalid values fall back to the default.
    STAGE1_TTL_HOURS: int = _parse_ttl_hours("STAGE1_TTL_HOURS", 2)
    QRLOCK_TTL_HOURS: int = _parse_ttl_hours("QRLOCK_TTL_HOURS", 2)
    FINAL_IMAGE_TTL_HOURS: int = _parse_ttl_hours("FINAL_IMAGE_TTL_HOURS", 24)
    # When true, final_ images (the Stage 2 Ghibli+QR composite — the actual
    # client deliverable) are never auto-deleted regardless of TTL. Defaults to
    # true: these are the one artifact this service must never silently lose;
    # intermediate stage1_/qrlock_ files keep their own short TTLs above.
    # Use explicit boolean — never encode "never delete" as a magic TTL value.
    PERSIST_FINAL_IMAGES: bool = os.getenv("PERSIST_FINAL_IMAGES", "true").lower() in {"1", "true", "yes"}
    PROMPT_PIC_TO_GHIBLI = (
        "Studio Ghibli hand-painted illustration of this exact person, based strictly on "
        "what is visibly present in the source photo — do not infer, guess, or add anything not seen. "
        "do not lighten, whiten, or tan-wash the skin under any circumstance. "

        "RACE AND ETHNICITY: Do not change this person's race or ethnicity in either direction. "
        "If the person is Black, they must remain visibly Black in the output — do not lighten their "
        "skin tone or shift their features toward a white or lighter-skinned appearance. If the person "
        "is white, they must remain visibly white in the output — do not darken their skin tone or shift "
        "their features toward a Black or darker-skinned appearance. This applies equally to every "
        "ethnicity shown in the source photo (e.g. South Asian, East Asian, Latino, Middle Eastern, "
        "Indigenous) — the person's real, visible race and ethnicity is not a stylistic choice and must "
        "never be swapped, blended, or ambiguated. "

        "SKIN TONE AND LIGHTING: First, identify this exact person's actual skin tone as shown in the "
        "source photo — it may be light, medium, tan, brown, or deep/dark brown. Whatever that tone is, "
        "preserve it exactly; do not shift it lighter or darker than what is shown. "
        "Any bright highlights on the nose, cheeks, forehead, or mouth area in the source photo are light "
        "REFLECTING off the skin, NOT a different, lighter skin color underneath — this is true regardless "
        "of whether the skin is light or dark. Stylize these highlights as a lighter SHADE of the person's "
        "OWN skin tone — brighten the value/luminance only, never shift the hue toward a different skin "
        "tone category (e.g. never toward tan or beige if the person has dark skin; never toward a darker "
        "tone if the person has light skin). The entire face must read as one consistent skin color family "
        "from darkest shadow to brightest highlight. Two-tone or patchy skin, or any highlight that looks "
        "like a different person's skin color, is a failure. Treat this the same way you would paint a "
        "highlight on any smooth material of a given color — like dark wood, tan leather, or pale marble — "
        "it gets lighter and shinier, it does not turn a different material or color. "

        "HIJAB: If this person is wearing a hijab or head covering, it is fabric that fully seals the "
        "hairline — there is no hair anywhere near the face because the covering physically blocks it "
        "from view, the same way a hood or helmet would. Do not draw a hairline, part, bangs, fringe, or "
        "loose strands at the forehead, temples, ears, or neck, even stylistically. The fabric meets the "
        "skin directly at the forehead and temples with a clean edge — treat the space where hair would "
        "normally be as covered fabric, not as an area to fill in with hair. "
        "If this person is NOT wearing a hijab or head covering in the original photo, do not add one — "
        "render the hair exactly as shown, fully visible, same length, style, and color as the original. "

        "Do not add facial hair, beard, mustache, or stubble unless clearly visible in the original photo. "
        "Preserve identical face, gender, skin tone, ethnicity, expression, pose, and clothing — only "
        "change the art style, not the person. "
        "Soft watercolor background, warm painterly colors, clean linework, cel-shaded lighting. "
        "Flat white background, no scenery, no shadows."
    )

    NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
        "two-tone skin, patchy skin, blotchy skin, mismatched skin patches, lighter skin patch on face, "
        "highlight rendered as different skin color, discolored skin, uneven skin tone, "
        "lightened skin, whitewashed skin, darkened skin, pale skin, skin tone shift, tan-wash, "
        "race change, race swap, changed race, altered ethnicity, ethnicity swap, "
        "black person made white, white person made black, whitewashing, blackwashing, "
        "racial features altered, different racial appearance, "
        "hair under hijab, hair peeking out, bangs, fringe, hairline showing under hijab, "
        "hair at temples, hair at forehead, loose strands near hijab, visible part line, "
        "added hijab, unwanted head covering, headscarf on person not wearing one, "
        "beard, mustache, stubble, unwanted facial hair, "
        "photorealistic, photograph, realistic lighting, camera photo, "
        "generic anime face, identity drift, beautification, face replacement, "
        "facial simplification, different person, altered hairstyle, altered expression"
    )
    PROMPT_GHIBLI_LOCK = (
        "Front-facing portrait photo pose, medium shot, subject centered and squared to camera, "
        "looking directly at viewer. Same Ghibli-style person from image 1, holding the QR lock "
        "from image 2 with both hands at chest height, lock centered, about one-third the image "
        "width, fully visible and sharp. Exactly two hands, both the person's own—no extra hands, "
        "no extra fingers, no side angle, no profile view. Preserve exact face, gender, skin tone, "
        "race, and ethnicity exactly as shown in image 1 — do not lighten, darken, or otherwise shift "
        "the skin tone or racial appearance in either direction. Preserve hair or hijab exactly as "
        "shown in image 1 (do not add or remove either), and clothing from image 1. "
        "Flat white background, no shadows."
    )

# Warn early so misconfiguration is visible in startup logs, not at first request.
if not Settings.DOMAIN:
    _log.warning(
        "DOMAIN env var is not set. Returned image URLs will be relative/unreachable — "
        "set DOMAIN to this server's reachable address (e.g. http://<host>:<port>)."
    )
if Settings.PERSIST_FINAL_IMAGES:
    _log.info("PERSIST_FINAL_IMAGES=true — final_ images will never be auto-deleted.")
else:
    _log.info("Final image retention TTL: %dh (set PERSIST_FINAL_IMAGES=true to keep indefinitely).", Settings.FINAL_IMAGE_TTL_HOURS)
if Settings.SAVE_OUTPUT_LOCAL:
    _log.info("SAVE_OUTPUT_LOCAL=true — final images are also saved to %s", Settings.OUTPUT_DIR)
if Settings.PRIVATE_MODE:
    if Settings.ALLOWED_IPS:
        _log.info("PRIVATE_MODE=true — only these IPs may call any route except /v1/health: %s", sorted(Settings.ALLOWED_IPS))
    else:
        _log.warning(
            "PRIVATE_MODE=true but ALLOWED_IPS is empty — every caller except /v1/health "
            "will get 403 Forbidden (fail-closed). Set ALLOWED_IPS if this isn't intended."
        )
