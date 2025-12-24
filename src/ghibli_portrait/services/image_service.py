from typing import List

import requests

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import AspectRatio, Quality

imgURLs = List[str]


def get_ghibli(
    imgs: imgURLs,
    aspect_ratio: AspectRatio = AspectRatio._1_1,
    quality: Quality = Quality.BASIC,
) -> dict:
    s = Settings()
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {s.KIE_API_KEY}",
    }

    body = {
        "model": s.KIE_IMG_MODEL,
        "callBackUrl": s.CALL_BACK,
        "input": {
            "prompt": "Convert this image to Ghibli style art.",
            "image_urls": imgs,
            "aspect_ratio": aspect_ratio,
            "quality": quality,
        },
    }

    try:
        response = requests.post(s.KIE_CREATE_TASK_API, json=body, headers=header)
        return response.json()

    except Exception as e:
        raise
