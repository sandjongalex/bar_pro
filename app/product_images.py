"""Persistent, validated product image storage and optimized POS thumbnails.

Images are runtime data, not source files. They live below Flask's instance
folder so a normal Git deployment does not replace them.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PRODUCT_IMAGE_BYTES = 4 * 1024 * 1024
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "jfif", "png", "webp"}
POS_THUMB_MAX_SIZE = (320, 320)
POS_THUMB_QUALITY = 78


def product_image_directory() -> Path:
    folder = Path(current_app.instance_path) / "product-images"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def product_thumbnail_directory() -> Path:
    folder = product_image_directory() / "thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _extension(filename: str) -> str:
    if "." not in filename:
        return ""
    ext = filename.rsplit(".", 1)[1].lower().strip()
    # JFIF is a JPEG interchange format. Store JPEG/JFIF files under the
    # canonical .jpg extension after validating the actual JPEG signature.
    return "jpg" if ext in {"jpeg", "jfif"} else ext


def _looks_like_image(data: bytes, extension: str) -> bool:
    if extension == "jpg":
        return len(data) >= 3 and data[:3] == b"\xff\xd8\xff"
    if extension == "png":
        return len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n"
    if extension == "webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _safe_key(key: str | None) -> str | None:
    if not key:
        return None
    safe_name = Path(str(key)).name
    return safe_name if safe_name == str(key) else None


def product_thumbnail_name(key: str | None) -> str | None:
    safe_name = _safe_key(key)
    if not safe_name:
        return None
    return f"{Path(safe_name).stem}.pos.webp"


def ensure_product_thumbnail(key: str | None) -> str | None:
    """Create or reuse a compact WebP thumbnail for POS/catalog grids.

    The source image key is immutable (a new upload gets a new UUID), so the
    generated file can be cached aggressively by browsers. Existing product
    images are optimized lazily the first time they are requested.
    """
    safe_name = _safe_key(key)
    thumb_name = product_thumbnail_name(safe_name)
    if not safe_name or not thumb_name:
        return None

    source = product_image_directory() / safe_name
    if not source.is_file():
        return None

    target = product_thumbnail_directory() / thumb_name
    try:
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return thumb_name
    except OSError:
        pass

    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail(POS_THUMB_MAX_SIZE, Image.Resampling.LANCZOS)

            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            image.save(
                temp,
                format="WEBP",
                quality=POS_THUMB_QUALITY,
                method=4,
                optimize=True,
            )
        temp.replace(target)
        return thumb_name
    except (UnidentifiedImageError, OSError, ValueError):
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        return None


def save_product_image(upload) -> str | None:
    """Validate and persist an uploaded image, returning its opaque storage key."""
    if not upload or not getattr(upload, "filename", ""):
        return None

    extension = _extension(upload.filename)
    if extension not in {"jpg", "png", "webp"}:
        raise ValueError("INVALID_IMAGE_TYPE")

    data = upload.stream.read(MAX_PRODUCT_IMAGE_BYTES + 1)
    if not data:
        raise ValueError("INVALID_IMAGE")
    if len(data) > MAX_PRODUCT_IMAGE_BYTES:
        raise ValueError("IMAGE_TOO_LARGE")
    if not _looks_like_image(data, extension):
        raise ValueError("INVALID_IMAGE")

    key = f"{uuid.uuid4().hex}.{extension}"
    path = product_image_directory() / key
    path.write_bytes(data)

    # Build the lightweight POS copy immediately for new uploads. If an older
    # or malformed image cannot be decoded, the original remains available and
    # the route will gracefully fall back to it.
    ensure_product_thumbnail(key)
    return key


def delete_product_image(key: str | None) -> None:
    """Delete one image and its generated thumbnail safely."""
    safe_name = _safe_key(key)
    if not safe_name:
        return

    path = product_image_directory() / safe_name
    try:
        path.unlink()
    except FileNotFoundError:
        pass

    thumb_name = product_thumbnail_name(safe_name)
    if thumb_name:
        try:
            (product_thumbnail_directory() / thumb_name).unlink()
        except FileNotFoundError:
            pass
