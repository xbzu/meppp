from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_INPUT_FORMATS = ("JPEG", "PNG", "WEBP")
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_EDGE = 8_192
MAX_OUTPUT_EDGE = 2_560
OUTPUT_MIME_TYPE = "image/webp"
OUTPUT_QUALITY_STEPS = (82, 76, 70)

# Pillow consults this before decoding. Converting the warning into an exception
# below makes the lower threshold a hard limit rather than a log-only signal.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    content: bytes
    source_byte_size: int
    byte_size: int
    width: int
    height: int
    alt_text: str
    mime_type: str = OUTPUT_MIME_TYPE


def _read_limited(upload: BinaryIO, *, max_bytes: int) -> bytes:
    try:
        upload.seek(0)
        content = upload.read(max_bytes + 1)
    except (AttributeError, OSError, ValueError) as error:
        raise ValidationError("图片读取失败，请重新选择文件") from error
    if not content:
        raise ValidationError("图片文件不能为空")
    if len(content) > max_bytes:
        raise ValidationError(f"每张图片不能超过 {max_bytes // (1024 * 1024)} MB")
    return content


def _validate_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValidationError("图片尺寸无效")
    if width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE or width * height > MAX_IMAGE_PIXELS:
        raise ValidationError("图片尺寸过大，请选择像素更小的图片")


def _reject_animation(image: Image.Image) -> None:
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
        raise ValidationError("暂不支持动态图片，请上传单帧图片")


def _verify_source(content: bytes) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(content), formats=ALLOWED_INPUT_FORMATS) as image:
            _validate_dimensions(image)
            _reject_animation(image)
            image.verify()


def _encode_webp(content: bytes, *, max_bytes: int) -> tuple[bytes, int, int]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(content), formats=ALLOWED_INPUT_FORMATS) as source:
            _validate_dimensions(source)
            _reject_animation(source)
            source.load()
            oriented = ImageOps.exif_transpose(source)
            _validate_dimensions(oriented)
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            normalized = oriented.convert("RGBA" if has_alpha else "RGB")
            normalized.thumbnail(
                (MAX_OUTPUT_EDGE, MAX_OUTPUT_EDGE),
                resample=Image.Resampling.LANCZOS,
            )
            width, height = normalized.size

            for quality in OUTPUT_QUALITY_STEPS:
                output = BytesIO()
                normalized.save(
                    output,
                    format="WEBP",
                    quality=quality,
                    method=4,
                )
                encoded = output.getvalue()
                if len(encoded) <= max_bytes:
                    break
            else:
                raise ValidationError("图片处理后仍然过大，请选择更简单或更小的图片")

    with Image.open(BytesIO(encoded), formats=("WEBP",)) as proof:
        proof.load()
        if proof.size != (width, height) or proof.format != "WEBP":
            raise ValidationError("图片处理失败，请重新选择文件")
        if getattr(proof, "n_frames", 1) != 1:
            raise ValidationError("图片处理失败，请重新选择文件")
    return encoded, width, height


def process_image_upload(*, upload: BinaryIO, max_bytes: int, alt_text: str = "") -> ProcessedImage:
    content = _read_limited(upload, max_bytes=max_bytes)
    try:
        _verify_source(content)
        encoded, width, height = _encode_webp(content, max_bytes=max_bytes)
    except ValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        EOFError,
        OSError,
        SyntaxError,
        ValueError,
    ) as error:
        raise ValidationError("文件不是可安全处理的 JPG、PNG 或 WebP 图片") from error

    return ProcessedImage(
        content=encoded,
        source_byte_size=len(content),
        byte_size=len(encoded),
        width=width,
        height=height,
        alt_text=alt_text.strip(),
    )


def process_image_uploads(
    *,
    uploads: list[BinaryIO],
    alt_texts: list[str],
    max_bytes: int,
) -> list[ProcessedImage]:
    processed = []
    for position, upload in enumerate(uploads):
        alt_text = alt_texts[position] if position < len(alt_texts) else ""
        try:
            processed.append(
                process_image_upload(upload=upload, max_bytes=max_bytes, alt_text=alt_text)
            )
        except ValidationError as error:
            raise ValidationError(f"第 {position + 1} 张图片：{error.messages[0]}") from error
    return processed
