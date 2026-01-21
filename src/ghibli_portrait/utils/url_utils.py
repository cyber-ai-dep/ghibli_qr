import uuid
from uuid import uuid5

from src.ghibli_portrait.api.responses import ShortURLData
from src.ghibli_portrait.config import Settings


def shorten(url: str):
    s = Settings()
    uid = uuid5(uuid.NAMESPACE_URL, url)

    short_code = str(uid)[: s.QR_SHORT_CODE_LENGTH]

    return ShortURLData(url=f"{s.DOMAIN}/{short_code}", code=short_code)
