from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from .image_processing import MAX_OUTPUT_EDGE, process_image_upload


def image_bytes(
    *,
    image_format: str = "JPEG",
    size: tuple[int, int] = (80, 48),
    mode: str = "RGB",
    exif=None,
    save_all: bool = False,
    append_images=None,
) -> bytes:
    output = BytesIO()
    image = Image.new(mode, size, (30, 90, 60, 180) if mode == "RGBA" else (30, 90, 60))
    options = {}
    if exif is not None:
        options["exif"] = exif
    if save_all:
        options.update(save_all=True, append_images=append_images or [], duration=100, loop=0)
    image.save(output, format=image_format, **options)
    return output.getvalue()


class ImageProcessingTests(SimpleTestCase):
    def test_jpeg_png_and_webp_are_reencoded_as_single_webp(self):
        inputs = (
            ("JPEG", "camera.php.jpg", "text/html", "RGB"),
            ("PNG", "..\\unsafe.png", "image/svg+xml", "RGBA"),
            ("WEBP", "photo.double.webp.exe", "application/octet-stream", "RGB"),
        )
        for image_format, name, content_type, mode in inputs:
            with self.subTest(image_format=image_format):
                upload = SimpleUploadedFile(
                    name,
                    image_bytes(image_format=image_format, mode=mode),
                    content_type=content_type,
                )
                processed = process_image_upload(
                    upload=upload,
                    max_bytes=5 * 1024 * 1024,
                    alt_text="  现场照片  ",
                )

                self.assertEqual(processed.mime_type, "image/webp")
                self.assertEqual(processed.alt_text, "现场照片")
                with Image.open(BytesIO(processed.content), formats=("WEBP",)) as proof:
                    proof.load()
                    self.assertEqual(proof.format, "WEBP")
                    self.assertEqual(getattr(proof, "n_frames", 1), 1)

    def test_exif_orientation_is_applied_and_metadata_and_trailing_data_are_removed(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "private-device-note"
        source = image_bytes(size=(80, 40), exif=exif) + b"TRAILING-PRIVATE-PAYLOAD"

        processed = process_image_upload(
            upload=SimpleUploadedFile("oriented.jpg", source, content_type="image/jpeg"),
            max_bytes=5 * 1024 * 1024,
        )

        self.assertEqual((processed.width, processed.height), (40, 80))
        self.assertNotIn(b"private-device-note", processed.content)
        self.assertNotIn(b"TRAILING-PRIVATE-PAYLOAD", processed.content)
        with Image.open(BytesIO(processed.content)) as proof:
            self.assertFalse(proof.getexif())
            self.assertNotIn("icc_profile", proof.info)

    def test_large_edge_is_resized_to_output_contract(self):
        processed = process_image_upload(
            upload=SimpleUploadedFile(
                "wide.png",
                image_bytes(image_format="PNG", size=(3000, 100)),
                content_type="image/png",
            ),
            max_bytes=5 * 1024 * 1024,
        )

        self.assertEqual(processed.width, MAX_OUTPUT_EDGE)
        self.assertLess(processed.height, 100)

    def test_empty_spoofed_truncated_and_oversized_files_are_rejected(self):
        cases = (
            SimpleUploadedFile("empty.jpg", b"", content_type="image/jpeg"),
            SimpleUploadedFile("spoof.jpg", b"<svg><script>x</script></svg>"),
            SimpleUploadedFile("truncated.png", image_bytes(image_format="PNG")[:20]),
            SimpleUploadedFile("large.jpg", b"x" * 129),
        )
        for upload in cases:
            with self.subTest(name=upload.name), self.assertRaises(ValidationError):
                process_image_upload(upload=upload, max_bytes=128)

    def test_animated_webp_is_rejected(self):
        second = Image.new("RGB", (20, 20), "red")
        animated = image_bytes(
            image_format="WEBP",
            size=(20, 20),
            save_all=True,
            append_images=[second],
        )

        with self.assertRaisesMessage(ValidationError, "动态图片"):
            process_image_upload(
                upload=SimpleUploadedFile("animated.webp", animated),
                max_bytes=5 * 1024 * 1024,
            )

    def test_decompression_bomb_warning_is_a_hard_error(self):
        compact = image_bytes(image_format="PNG", size=(9, 7))

        with (
            patch.object(Image, "MAX_IMAGE_PIXELS", 50),
            self.assertRaises(ValidationError),
        ):
            process_image_upload(
                upload=SimpleUploadedFile("bomb.png", compact),
                max_bytes=5 * 1024 * 1024,
            )
