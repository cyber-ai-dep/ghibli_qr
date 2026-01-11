import requests

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import AspectRatio, Quality, ImgURLs, QwenImageSize


def generate_img(
    img_urls: ImgURLs,
    prompt: str,
    aspect_ratio: AspectRatio = AspectRatio._1_1,
    quality: Quality = Quality.BASIC,
) -> dict:
    s = Settings()
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {s.KIE_API_KEY}",
    }

    is_qwen_model = s.KIE_IMG_MODEL.startswith("qwen/")

    if is_qwen_model:
        image_url = img_urls[0] if isinstance(img_urls, list) else img_urls

        input_params = {
            "prompt": prompt if prompt else "Edit this image",
            "image_url": image_url,
        }

        if aspect_ratio:
            input_params["image_size"] = QwenImageSize.from_aspect_ratio(aspect_ratio).value

        body = {
            "model": s.KIE_IMG_MODEL,
            "callBackUrl": s.CALL_BACK,
            "input": input_params,
        }
    else:
        body = {
            "model": s.KIE_IMG_MODEL,
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

    except Exception as e:
        raise
