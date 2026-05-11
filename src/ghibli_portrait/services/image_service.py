import requests

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import AspectRatio, Quality, ImgURLs, QwenImageSize


def generate_img(
    img_urls: ImgURLs,
    prompt: str,
    aspect_ratio: AspectRatio = AspectRatio._1_1,
    quality: Quality = Quality.BASIC,
    model: str | None = None,
    negative_prompt: str | None = None,
) -> dict:
    s = Settings()
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {s.KIE_API_KEY}",
    }

    chosen_model = model or s.KIE_IMG_MODEL
    if not chosen_model:
        raise ValueError("KIE model is not configured. Set KIE_GHIBLI_MODEL/KIE_COMPOSE_MODEL or KIE_IMG_MODEL.")

    is_flux_kontext = chosen_model.startswith("flux-kontext")
    is_qwen_model = chosen_model.startswith("qwen/")

    if is_flux_kontext:
        # Flux Kontext fields go under KIE's standard "input" wrapper.
        # Designed for subject-consistent image editing — preserves identity while restyling.
        # Does NOT support negative_prompt.
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

        if aspect_ratio:
            input_params["image_size"] = QwenImageSize.from_aspect_ratio(aspect_ratio).value

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

    try:
        response = requests.post(s.KIE_CREATE_TASK_API, json=body, headers=header)
        return response.json()

    except Exception:
        raise
