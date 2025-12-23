import qrcode
from PIL import Image

from src.ghibli_portrait.config import Settings


def get_qr(url: str, version: int = None) -> Image:
    s = Settings()

    lock_img = Image.open(s.LOCK_PATH).convert("RGBA")

    qr = qrcode.QRCode(
        version or s.QR_VERSION,
        error_correction=qrcode.ERROR_CORRECT_M,
        box_size=10,
        border=0,
    )

    qr.add_data(url)

    qr.make(fit=True)
    qr_img = qr.make_image(
        fill_color=s.QR_FILL_COLOR, back_color=s.QR_BACK_COLOR
    ).convert("RGBA")

    qr_img = qr_img.resize((s.QR_SIDE_LEN, s.QR_SIDE_LEN), Image.LANCZOS)
    lock_img.paste(qr_img, s.QR_PASTE_COORDINATES, qr_img)

    return lock_img
