"""
Image validation service for WildLensAI.

Enforces security and format constraints on user-uploaded wildlife images
using Pillow (PIL):
- Formats allowed: JPEG, PNG, WebP
- Maximum file size: 5 MB
- Rejects: Corrupted files, empty files, decompression bombs, oversized dimensions.
"""

import io
from PIL import Image


class ImageValidationError(ValueError):
    """Raised when an uploaded file fails image safety, dimension, or format checks."""
    pass


MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_DIMENSION = 4096
MAX_PIXELS = 25_000_000

MIME_TYPE_MAP = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def validate_and_process_image(file_obj):
    """
    Validate uploaded image file using Pillow.

    Args:
        file_obj: Django UploadedFile, FieldFile, or file-like object.

    Returns:
        tuple[bytes, str]: (raw_image_bytes, mime_type)

    Raises:
        ImageValidationError: If the file is invalid, corrupt, or unsafe.
    """
    if not file_obj:
        raise ImageValidationError("No image file was provided.")

    # Read bytes from file object or use directly if already bytes/bytearray
    if isinstance(file_obj, (bytes, bytearray)):
        raw_bytes = bytes(file_obj)
    elif hasattr(file_obj, "read"):
        try:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            raw_bytes = file_obj.read()
        except Exception as e:
            raise ImageValidationError(f"Could not read uploaded file: {e}") from e
    else:
        raise ImageValidationError(f"Unsupported image file object type: {type(file_obj).__name__}")

    # Check empty file
    if not raw_bytes or len(raw_bytes) == 0:
        raise ImageValidationError("The uploaded file is empty (0 bytes).")

    # Check file size limit (5 MB)
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        size_mb = len(raw_bytes) / (1024 * 1024)
        raise ImageValidationError(
            f"Image size ({size_mb:.2f} MB) exceeds maximum allowed limit of 5.0 MB."
        )

    # Validate image data with Pillow
    byte_stream = io.BytesIO(raw_bytes)

    # Protect against decompression bomb attacks
    old_max_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    try:
        with Image.open(byte_stream) as img:
            # 1. Check format
            detected_format = (img.format or "").upper()
            if detected_format == "JPG":
                detected_format = "JPEG"

            if detected_format not in ALLOWED_FORMATS:
                raise ImageValidationError(
                    f"Unsupported image format: '{detected_format or 'Unknown'}'. "
                    f"Allowed formats are: {', '.join(sorted(ALLOWED_FORMATS))}."
                )

            # 2. Check pixel dimensions
            width, height = img.size
            if width <= 0 or height <= 0:
                raise ImageValidationError("Invalid image dimensions detected.")

            if width * height > MAX_PIXELS:
                raise ImageValidationError(
                    f"Image resolution ({width}x{height}) exceeds maximum safe limit."
                )

            if width > MAX_DIMENSION or height > MAX_DIMENSION:
                raise ImageValidationError(
                    f"Image dimensions ({width}x{height}) exceed maximum allowed bound of {MAX_DIMENSION}px."
                )

            # 3. Verify image integrity
            try:
                img.verify()
            except Exception as e:
                raise ImageValidationError(f"Corrupted or malformed image data: {e}") from e

            # Re-read and transpose to catch truncated image blocks
            byte_stream.seek(0)
            with Image.open(byte_stream) as img_check:
                try:
                    img_check.load()
                except Exception as e:
                    raise ImageValidationError(f"Image decompression verification failed: {e}") from e

            mime_type = MIME_TYPE_MAP.get(detected_format, "image/jpeg")

    except Image.DecompressionBombError as e:
        raise ImageValidationError(f"Image rejected as potential decompression bomb: {e}") from e
    except ImageValidationError:
        raise
    except Exception as e:
        raise ImageValidationError(f"Failed to process image file: {e}") from e
    finally:
        Image.MAX_IMAGE_PIXELS = old_max_pixels
        if hasattr(file_obj, "seek"):
            try:
                file_obj.seek(0)
            except Exception:
                pass

    return raw_bytes, mime_type
