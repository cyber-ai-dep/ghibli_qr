"""
CLIP Zero-Shot Validation Service (standalone, not yet wired into the pipeline)

Zero-shot semantic classification of an image using CLIP embeddings, as a
candidate replacement for the heuristic pairing of "zero faces detected ->
probably an animal" MediaPipe inference plus the separate pixel-statistics
`_is_synthetic_face` check in validation_service.py.

This module is intentionally self-contained: it does NOT import anything
from validation_service.py, config.py, or any other part of the app, so it
can be exercised and evaluated entirely on its own (see
tests/test_clip_validation_service.py) before any decision is made to wire
it into the request pipeline. Nothing in the existing pipeline is touched by
this file.

Approach: embed the image once, compare cosine similarity against text
prompts for five classes, and take the highest-scoring class:

    "human"    (real photograph of ONE person)  -> pass
    "cartoon"  (cartoon / anime / game render)  -> reject
    "animal"   (photo of an animal)             -> reject
    "render"   (digital art / 3D render)        -> reject
    "multiple" (photo of two or more people)    -> reject

The "multiple" class exists because CLIP's single-person "human" prompts
alone cannot tell a solo portrait from a group photo — both are equally "a
real photograph of a human person." Framing "single person" vs "multiple
people" as its own contrastive pair gives CLIP a signal to actually
discriminate on, instead of asking one class to silently also mean "and
there's exactly one." This is what lets this module reject group photos
without any face-counting model (MediaPipe) at all.

One semantic decision instead of several stacked heuristics with gaps
between them — animals, game renders, and anime all score low on "real human
photo" in embedding space without a separate hand-tuned rule per case.

PROMPT ENSEMBLING: a single prompt per class ("a real photograph of a human
person") turned out to have a high false-rejection rate on real stock/studio
photography — professional color grading, shallow depth of field, and studio
lighting push a single generic prompt's embedding away from plain snapshots.
Each class below is instead a handful of phrasings; their text embeddings are
averaged and re-normalized before comparison, per the prompt-ensembling
technique from the CLIP paper (Radford et al. 2021, Appendix A) — this is the
standard, cheap way to close zero-shot CLIP's accuracy gap without a bigger
model or any fine-tuning.

Cost: ~50-350ms CPU per image once the model is warm (module-level singleton;
the first call pays a one-time model-load cost, plus a one-time weights
download on first run — see _MODEL_NAME/_PRETRAINED below). Ensembling adds
negligible cost since text embeddings are precomputed once at load time, not
per image.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PIL import Image

_logger = logging.getLogger("clip_validation_service")

# OpenAI's released CLIP checkpoints were trained with the QuickGELU activation;
# the plain "ViT-B-32" config defaults to standard GELU, which silently loads
# with mismatched activations (open_clip warns but does not error) and degrades
# embedding quality. The "-quickgelu" variant matches what "openai" weights expect.
_MODEL_NAME = "ViT-B-32-quickgelu"
_PRETRAINED = "openai"

_HUMAN_LABEL = "a real photograph of one human person"
_CARTOON_LABEL = "a cartoon, anime, or video game character"
_ANIMAL_LABEL = "a photo of an animal"
_RENDER_LABEL = "digital art or a 3D render"
_MULTIPLE_LABEL = "a photo of multiple people together"

# Canonical label -> ensemble of phrasings averaged into that label's text embedding.
# Kept intentionally varied in framing (candid/studio/portrait/phone-camera, different
# ethnicities/headwear called out explicitly) so the embedding isn't anchored to one
# photographic style — that anchoring is what caused the single-prompt false rejections.
_PROMPT_TEMPLATES: Dict[str, List[str]] = {
    _HUMAN_LABEL: [
        "a real photograph of one human person",
        "a candid photo of a single real person, alone",
        "an unedited photograph of one man or woman",
        "a solo studio portrait photograph of a real person",
        "a professional photograph of a single person's face",
        "a phone camera selfie of one person",
        "a photo of a real human face, not an illustration",
        "a photograph of a single person wearing a hijab or headscarf",
        "a photograph of one person with dark or light skin",
        "a glossy, heavily retouched beauty or fashion photograph of a single real person",
        "a smooth, soft studio-lit portrait photograph of one person with dramatic lighting",
        "a magazine-style editorial photograph of a single real person",
        "a stock photo of one real person with a watermark overlay",
    ],
    _CARTOON_LABEL: [
        "a cartoon, anime, or video game character",
        "an anime drawing of a person",
        "a hand-drawn cartoon illustration of a character",
        "a video game character render",
        "an animated movie character",
        "a comic book style illustration of a person",
    ],
    _ANIMAL_LABEL: [
        "a photo of an animal",
        "a photograph of a pet",
        "a wildlife photograph, not a human",
        "a close-up photo of an animal's face",
    ],
    _RENDER_LABEL: [
        "digital art or a 3D render",
        "a 3D rendered CGI character",
        "computer-generated digital artwork of a person",
        "a synthetic, AI-generated portrait",
    ],
    _MULTIPLE_LABEL: [
        "a photo of multiple people together",
        "a group photo of several people",
        "two or more people posing in one photo",
        "a family photo with several people in frame",
        "a photo of a crowd of people",
        "friends standing together in a group picture",
    ],
}

_LABELS: List[str] = list(_PROMPT_TEMPLATES.keys())

# Module-level singletons — CLIP model/preprocess/text-features load once,
# then every call reuses them. Lazy: initialized on first use.
_clip_model = None
_clip_preprocess = None
_text_features = None  # normalized, ensembled text embeddings, shape (len(_LABELS), dim)


def _load_clip() -> None:
    """Load the CLIP model/preprocess pipeline and precompute ensembled text embeddings, once."""
    global _clip_model, _clip_preprocess, _text_features
    if _clip_model is not None:
        return

    import torch
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        _MODEL_NAME, pretrained=_PRETRAINED
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(_MODEL_NAME)

    with torch.no_grad():
        label_features = []
        for label in _LABELS:
            templates = _PROMPT_TEMPLATES[label]
            tokens = tokenizer(templates)
            feats = model.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            mean_feat = feats.mean(dim=0)
            mean_feat = mean_feat / mean_feat.norm()
            label_features.append(mean_feat)
        text_features = torch.stack(label_features)

    _clip_model = model
    _clip_preprocess = preprocess
    _text_features = text_features
    _logger.info(
        "CLIP model loaded: %s (%s), %d classes ensembled from %d total prompts",
        _MODEL_NAME, _PRETRAINED, len(_LABELS),
        sum(len(v) for v in _PROMPT_TEMPLATES.values()),
    )


@dataclass
class ClipClassificationResult:
    """Raw result of zero-shot CLIP classification against `_LABELS`."""
    ok: bool
    label: str = ""
    scores: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None


def classify_image(img: Image.Image) -> ClipClassificationResult:
    """
    Zero-shot classify `img` against `_LABELS` using CLIP cosine similarity.

    Returns the highest-scoring label plus the full score distribution
    (softmax over cosine similarities at CLIP's standard temperature of 100).
    """
    try:
        _load_clip()

        import torch

        image_input = _clip_preprocess(img.convert("RGB")).unsqueeze(0)

        with torch.no_grad():
            image_features = _clip_model.encode_image(image_input)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            similarity = (100.0 * image_features @ _text_features.T).softmax(dim=-1)[0]

        scores = {label: float(similarity[i]) for i, label in enumerate(_LABELS)}
        top_label = max(scores, key=scores.get)

        return ClipClassificationResult(ok=True, label=top_label, scores=scores)

    except Exception as e:
        _logger.error("CLIP classification failed: %s", e)
        return ClipClassificationResult(ok=False, error=f"CLIP runtime error: {e}")


@dataclass
class ClipValidationResult:
    """Pass/reject decision derived from classify_image(), plus the raw scores."""
    ok: bool
    code: Optional[str] = None
    message: str = ""
    label: str = ""
    scores: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None


# Rejection codes chosen to match the existing V1 error contract documented in
# docs/usage.md, so that IF this is later wired in, responses stay compatible.
_REJECTION_CODE: Dict[str, str] = {
    _CARTOON_LABEL: "NOT_REAL_PHOTO",
    _ANIMAL_LABEL: "NO_FACE_DETECTED",
    _RENDER_LABEL: "NOT_REAL_PHOTO",
    _MULTIPLE_LABEL: "MULTIPLE_FACES",
}
_REJECTION_MESSAGE: Dict[str, str] = {
    _CARTOON_LABEL: (
        "Image appears to be a cartoon, anime, or video game character. "
        "Please provide a real human portrait photo."
    ),
    _ANIMAL_LABEL: (
        "No human face detected. Please provide a clear portrait photo of a person."
    ),
    _RENDER_LABEL: (
        "Image appears to be a 3D render or digital art. "
        "Please provide a real human portrait photo."
    ),
    _MULTIPLE_LABEL: (
        "Multiple people detected. Please provide a single-person portrait."
    ),
}


def validate_human_portrait(img: Image.Image, image_url: str = "") -> ClipValidationResult:
    """
    Standalone decision: does `img` look like a photo of exactly one real
    human person? Rejects animals, cartoons/anime/game renders, digital art /
    3D renders, and photos with multiple people — all via the same single
    zero-shot classification call, no MediaPipe / face-counting model involved.
    """
    result = classify_image(img)

    if not result.ok:
        _logger.error({"url": image_url, "error": result.error, "decision": "ERROR"})
        return ClipValidationResult(
            ok=False,
            code="CLIP_CLASSIFIER_FAILURE",
            message="Image classification is temporarily unavailable. Please try again.",
            error=result.error,
        )

    if result.label == _HUMAN_LABEL:
        _logger.info({"url": image_url, "scores": result.scores, "decision": "ACCEPT"})
        return ClipValidationResult(ok=True, label=result.label, scores=result.scores)

    _logger.info({
        "url": image_url,
        "scores": result.scores,
        "decision": "REJECT",
        "reason": result.label,
    })
    return ClipValidationResult(
        ok=False,
        code=_REJECTION_CODE.get(result.label, "NOT_REAL_PHOTO"),
        message=_REJECTION_MESSAGE.get(
            result.label,
            "Image does not appear to be a real human portrait photo.",
        ),
        label=result.label,
        scores=result.scores,
    )
