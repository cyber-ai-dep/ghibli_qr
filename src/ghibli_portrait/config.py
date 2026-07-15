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
    # Reject if more than this number of faces are detected (set to 0 to disable the limit).
    MAX_FACES = int(os.getenv("MAX_FACES", "1"))
    # Minimum face area ratio (face_bbox_area / image_area). Helps reject tiny/far faces.
    MIN_FACE_AREA_RATIO = float(os.getenv("MIN_FACE_AREA_RATIO", "0.03"))
    # Max concurrent MediaPipe face-detection operations. MediaPipe is no longer
    # called from the request path (see CLIP_CONCURRENCY_LIMIT below) — kept
    # defined, unreferenced, alongside the MediaPipe code it used to size.
    MAX_MEDIAPIPE_CONCURRENCY = int(os.getenv("MAX_MEDIAPIPE_CONCURRENCY", "15"))

    # Max concurrent CLIP classification operations (Stage 1 human-portrait gate).
    # CLIP inference is pinned to 1 torch thread per call (see
    # clip_validation_service._load_clip), so this sizes real parallelism —
    # start near the vCPU count with a small margin (e.g. 6 on an 8 vCPU host).
    CLIP_CONCURRENCY_LIMIT = int(os.getenv("CLIP_CONCURRENCY_LIMIT", "4"))

    # Max concurrent image-generation submissions to the provider (BytePlus ARK).
    # ARK allows up to 10 concurrent requests per model per primary account; staying
    # below that avoids queueing/429. 8 leaves headroom; lower it if you hit rate limits.
    GENERATION_CONCURRENCY_LIMIT = int(os.getenv("GENERATION_CONCURRENCY_LIMIT", "8"))

    # Enable post-generation identity drift check. Disable when the model triggers
    # false positives consistently. Re-enable with a strong identity-preserving model.
    ENABLE_IDENTITY_CHECK = os.getenv("ENABLE_IDENTITY_CHECK", "false").lower() in {"1", "true", "yes"}

    # TTL for files in static/tmp/ — differentiated by filename prefix.
    # stage1_* and qrlock_* are intermediate assets; final_* are client deliverables.
    # All values must be positive integers (hours). Invalid values fall back to the default.
    STAGE1_TTL_HOURS: int = _parse_ttl_hours("STAGE1_TTL_HOURS", 2)
    QRLOCK_TTL_HOURS: int = _parse_ttl_hours("QRLOCK_TTL_HOURS", 2)
    FINAL_IMAGE_TTL_HOURS: int = _parse_ttl_hours("FINAL_IMAGE_TTL_HOURS", 24)
    # When true, final_ images are never auto-deleted regardless of TTL.
    # Use explicit boolean — never encode "never delete" as a magic TTL value.
    PERSIST_FINAL_IMAGES: bool = os.getenv("PERSIST_FINAL_IMAGES", "false").lower() in {"1", "true", "yes"}
    PROMPT_PIC_TO_GHIBLI = (
        "Studio Ghibli hand-painted illustration of this exact person, based strictly on "
        "what is visibly present in the source photo — do not infer, guess, or add anything not seen. "
        "do not lighten, whiten, or tan-wash the skin under any circumstance. "

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
        "race change, altered ethnicity, "
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
        "hair or hijab exactly as shown in image 1 (do not add or remove either), and clothing "
        "from image 1. Flat white background, no shadows."
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
