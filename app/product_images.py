"""Persistent, validated product image storage.

Images are runtime data, not source files.  They live below Flask's instance
folder so a normal Git deployment does not replace them.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from flask import current_app

MAX_PRODUCT_IMAGE_BYTES = 4 * 1024 * 1024
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "jfif", "png", "webp"}


def product_image_directory() -> Path:
    folder = Path(current_app.instance_path) / "product-images"
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
    return key


def delete_product_image(key: str | None) -> None:
    """Delete one image key without ever accepting a path from the caller."""
    if not key:
        return
    safe_name = Path(str(key)).name
    if safe_name != key:
        return
    path = product_image_directory() / safe_name
    try:
        path.unlink()
    except FileNotFoundError:
        pass
