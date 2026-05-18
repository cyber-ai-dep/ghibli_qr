import qrcode
from PIL import Image

from src.ghibli_portrait.config import Settings

_settings = Settings()


def get_qr(url: str, version: int = None) -> Image:
    s = _settings

    lock_img = Image.open(s.LOCK_PATH).convert("RGBA")
    lock_w, lock_h = lock_img.size

    qr = qrcode.QRCode(
        version,
        error_correction=qrcode.ERROR_CORRECT_L,
        box_size=10,
        border=0,
    )

    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(
        fill_color=s.QR_FILL_COLOR, back_color=s.QR_BACK_COLOR
    ).convert("RGBA")

    # Proportional QR sizing: target 28% of lock image width, clamped to [22%, 32%]
    target_side = int(lock_w * s.QR_LOCK_TARGET_WIDTH_RATIO)
    min_side = int(lock_w * s.QR_LOCK_MIN_WIDTH_RATIO)
    max_side = int(lock_w * s.QR_LOCK_MAX_WIDTH_RATIO)
    qr_side = max(min_side, min(max_side, target_side))

    qr_img = qr_img.resize((qr_side, qr_side), Image.LANCZOS)

    # Center horizontally; place at ~46% down (lower-torso/hand area), keeping a 2% bottom margin
    paste_x = (lock_w - qr_side) // 2
    paste_y = int(lock_h * 0.46)
    paste_y = min(paste_y, lock_h - qr_side - int(lock_h * 0.02))

    lock_img.paste(qr_img, (paste_x, paste_y), qr_img)

    # Convert to RGB before returning - Seedream does not accept RGBA
    return lock_img.convert("RGB")
