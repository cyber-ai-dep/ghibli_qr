import json
import logging

import httpx

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import AspectRatio, Quality, ImgURLs

_log = logging.getLogger(__name__)
_settings = Settings()


async def generate_img(
    img_urls: ImgURLs,
    prompt: str,
    aspect_ratio: AspectRatio = AspectRatio._1_1,
    quality: Quality = Quality.BASIC,
    model: str | None = None,
    negative_prompt: str | None = None,
) -> dict:
    s = _settings
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {s.KIE_API_KEY}",
    }

    chosen_model = model or s.KIE_IMG_MODEL
    if not chosen_model:
        raise ValueError("KIE model is not configured. Set KIE_GHIBLI_MODEL/KIE_COMPOSE_MODEL or KIE_IMG_MODEL.")

    is_flux_kontext = chosen_model.startswith("flux-kontext")
    is_qwen_model = chosen_model.startswith("qwen/")

    if is_flux_kontext:
        # Flux Kontext: subject-consistent style transfer, no negative_prompt support.
        image_url = img_urls[0] if isinstance(img_urls, list) else img_urls
        body = {
            "model": chosen_model,
            "callBackUrl": s.CALL_BACK,
            "input": {
                "prompt": prompt,
                "inputImage": image_url,
                "aspectRatio": aspect_ratio if isinstance(aspect_ratio, str) else getattr(aspect_ratio, "value", "1:1"),
                "outputFormat": "jpeg",
                "safetyTolerance": 2,
            },
        }

    elif is_qwen_model:
        image_url = img_urls[0] if isinstance(img_urls, list) else img_urls

        input_params = {
            "prompt": prompt if prompt else "Edit this image",
            "image_url": image_url,
            "guidance_scale": s.STAGE1_GUIDANCE_SCALE,
            "num_inference_steps": s.STAGE1_NUM_INFERENCE_STEPS,
            "acceleration": s.STAGE1_ACCELERATION,
            "seed": s.KIE_SEED,
            "image_size": s.KIE_IMAGE_SIZE,
            "output_format": s.KIE_OUTPUT_FORMAT,
        }

        if negative_prompt:
            input_params["negative_prompt"] = negative_prompt

        # Fidelity controls — maximize identity preservation (silently ignored if unsupported)
        input_params["image_strength"] = s.STAGE1_IMAGE_STRENGTH
        input_params["denoise"] = s.STAGE1_DENOISE
        input_params["fidelity"] = s.STAGE1_FIDELITY
        input_params["reference_strength"] = s.STAGE1_REFERENCE_STRENGTH
        input_params["preserve_identity"] = True
        input_params["preserve_face"] = True

        body = {
            "model": chosen_model,
            "callBackUrl": s.CALL_BACK,
            "input": input_params,
        }

    else:
        body = {
            "model": chosen_model,
            "callBackUrl": s.CALL_BACK,
            "input": {
                "prompt": prompt,
                "image_urls": img_urls,
                "aspect_ratio": aspect_ratio,
                "quality": quality,
            },
        }

    _log.debug("KIE REQUEST >>> %s", json.dumps(body, default=str))

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(s.KIE_CREATE_TASK_API, json=body, headers=headers)
    result = response.json()
    _log.debug("KIE RESPONSE <<< %s", json.dumps(result, default=str))
    return result
