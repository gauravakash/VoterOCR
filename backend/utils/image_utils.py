"""Image encoding helpers — optimization that cuts LLM token cost ~50%.

JPEG @ 85 quality + 1600px max dimension is the sweet spot for ECI roll OCR:
text edges are preserved, payload size is half of PNG, and Gemini's vision
encoder accepts both formats equally.
"""

from __future__ import annotations

import base64
import os
from io import BytesIO

from PIL import Image


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def encode_image_optimized(img: Image.Image) -> tuple[str, str]:
    """Resize + JPEG-encode + base64. Returns (b64_string, media_type)."""
    img_format = (os.getenv("IMAGE_FORMAT") or "jpeg").lower()
    quality = _env_int("IMAGE_QUALITY", 85)
    max_dim = _env_int("IMAGE_MAX_DIMENSION", 1600)

    if max(img.size) > max_dim:
        img = img.copy()
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    buf = BytesIO()
    if img_format == "jpeg":
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        media_type = "image/jpeg"
    else:
        img.save(buf, format="PNG", optimize=True)
        media_type = "image/png"

    raw_bytes = buf.getvalue()
    b64 = base64.b64encode(raw_bytes).decode("utf-8")
    return b64, media_type


def estimated_image_kb(b64: str) -> float:
    """Approximate image bytes from a base64 string (base64 is ~33% larger)."""
    return len(b64) * 0.75 / 1024
