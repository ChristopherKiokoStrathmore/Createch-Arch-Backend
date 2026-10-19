"""Resize uploads, drop EXIF, and re-encode as WebP or JPEG."""

import io
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import serializers

MAX_EDGE = 2400
JPEG_QUALITY = 85
WEBP_QUALITY = 82


def _has_alpha(image):
    if image.mode in ("RGBA", "LA"):
        return True
    return image.mode == "P" and "transparency" in image.info


def _stem(uploaded):
    raw = Path(getattr(uploaded, "name", "") or "upload").stem
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))
    return cleaned or "upload"


def normalize_uploaded_image(uploaded):
    """Cap the long edge, transpose by EXIF, then save without metadata."""
    limit = getattr(settings, "ARCH_MAX_UPLOAD_BYTES", 8 * 1024 * 1024)
    size = getattr(uploaded, "size", None)
    if size is not None and size > limit:
        raise serializers.ValidationError(f"File exceeds the {limit} byte upload limit.")
    raw = uploaded.read()
    if len(raw) > limit:
        raise serializers.ValidationError(f"File exceeds the {limit} byte upload limit.")
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise serializers.ValidationError("Upload must be an image.") from exc

    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    if _has_alpha(image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        fmt, ext = "WEBP", ".webp"
        save_kwargs = {"quality": WEBP_QUALITY, "method": 4}
    else:
        if image.mode != "RGB":
            image = image.convert("RGB")
        fmt, ext = "JPEG", ".jpg"
        save_kwargs = {"quality": JPEG_QUALITY, "optimize": True}

    out = io.BytesIO()
    image.save(out, format=fmt, **save_kwargs)
    return ContentFile(out.getvalue(), name=f"{_stem(uploaded)}{ext}")
