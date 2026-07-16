from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from PIL import Image

from .models import MAX_VIDEO_UPLOAD_BYTES, VideoMimeType
from .video_processing import (
    MAX_PROBE_OUTPUT_BYTES,
    VideoProbe,
    _parse_probe_payload,
    _remux_video,
    _run_command,
    process_video_upload,
    verify_poster_bytes,
)


def probe_payload(
    *,
    format_name="mov,mp4,m4a,3gp,3g2,mj2",
    video_codec="h264",
    audio_codec="aac",
    duration="1.250",
    width=640,
    height=360,
):
    streams = [
        {
            "codec_type": "video",
            "codec_name": video_codec,
            "width": width,
            "height": height,
            "duration": duration,
            "disposition": {"attached_pic": 0},
        }
    ]
    if audio_codec is not None:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": audio_codec,
                "duration": duration,
            }
        )
    return {
        "format": {"format_name": format_name, "duration": duration},
        "streams": streams,
    }


class VideoProbeTests(SimpleTestCase):
    def test_accepts_only_the_supported_container_and_codec_pairs(self):
        mp4 = _parse_probe_payload(probe_payload())
        webm = _parse_probe_payload(
            probe_payload(format_name="matroska,webm", video_codec="vp9", audio_codec="opus")
        )

        self.assertEqual(mp4.mime_type, VideoMimeType.MP4)
        self.assertEqual(mp4.duration_ms, 1250)
        self.assertEqual(webm.mime_type, VideoMimeType.WEBM)
        self.assertEqual(webm.audio_codec, "opus")

    def test_accepts_video_without_an_audio_track(self):
        result = _parse_probe_payload(probe_payload(audio_codec=None))

        self.assertIsNone(result.audio_codec)

    def test_rejects_wrong_codecs_extra_tracks_and_attached_pictures(self):
        invalid_payloads = [
            probe_payload(video_codec="hevc"),
            probe_payload(audio_codec="mp3"),
            probe_payload(format_name="matroska,webm", video_codec="vp9", audio_codec="aac"),
            {
                **probe_payload(),
                "streams": [
                    *probe_payload()["streams"],
                    {"codec_type": "subtitle", "codec_name": "mov_text"},
                ],
            },
            {
                **probe_payload(),
                "streams": [
                    {
                        **probe_payload()["streams"][0],
                        "disposition": {"attached_pic": 1},
                    }
                ],
            },
            {
                **probe_payload(),
                "streams": [
                    {
                        **probe_payload()["streams"][0],
                        "disposition": "not-an-object",
                    }
                ],
            },
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                _parse_probe_payload(payload)

    def test_rejects_excessive_duration_dimensions_and_container_changes(self):
        invalid_cases = [
            (probe_payload(duration="300.001"), None),
            (probe_payload(width=8193), None),
            (probe_payload(width=8192, height=8192), None),
            (probe_payload(), VideoMimeType.WEBM),
        ]

        for payload, expected_mime_type in invalid_cases:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                _parse_probe_payload(payload, expected_mime_type=expected_mime_type)

    def test_command_runner_never_uses_a_shell_and_has_a_fixed_timeout(self):
        completed = subprocess.CompletedProcess(["ffprobe"], 0, stdout=b"{}", stderr=b"")
        with patch(
            "meppp.publishing.video_processing.subprocess.run", return_value=completed
        ) as run:
            result = _run_command(["ffprobe", "input"], timeout=7)

        self.assertIs(result, completed)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertEqual(
            set(run.call_args.kwargs["env"]),
            {"LANG", "LC_ALL", "PATH"},
        )

    def test_command_runner_converts_missing_binary_timeout_and_oversized_output(self):
        failures = [
            FileNotFoundError("missing"),
            subprocess.TimeoutExpired(["ffprobe"], 1),
        ]
        for failure in failures:
            with (
                self.subTest(failure=failure),
                patch("meppp.publishing.video_processing.subprocess.run", side_effect=failure),
                self.assertRaises(ValidationError),
            ):
                _run_command(["ffprobe"], timeout=1)

        oversized = subprocess.CompletedProcess(
            ["ffprobe"],
            0,
            stdout=b"x" * (MAX_PROBE_OUTPUT_BYTES + 1),
            stderr=b"",
        )
        with (
            patch("meppp.publishing.video_processing.subprocess.run", return_value=oversized),
            self.assertRaisesMessage(ValidationError, "检查结果异常"),
        ):
            _run_command(["ffprobe"], timeout=1)

    def test_remux_uses_copy_mode_and_strips_untrusted_metadata(self):
        probe = VideoProbe(
            mime_type=VideoMimeType.MP4,
            duration_ms=1000,
            width=64,
            height=48,
            video_codec="h264",
            audio_codec="aac",
        )
        with patch("meppp.publishing.video_processing._run_command") as run:
            _remux_video(Path("source"), Path("destination"), probe=probe)

        arguments = run.call_args.args[0]
        self.assertIn("copy", arguments)
        self.assertIn("-map_metadata", arguments)
        self.assertIn("-map_chapters", arguments)
        self.assertIn("-protocol_whitelist", arguments)
        self.assertIn("-format_whitelist", arguments)
        self.assertIn("-enable_drefs", arguments)
        self.assertIn("-use_absolute_path", arguments)
        self.assertEqual(arguments[-2:], ["mp4", "destination"])

    def test_oversized_upload_is_rejected_before_external_tools_run(self):
        upload = BytesIO(b"x" * (MAX_VIDEO_UPLOAD_BYTES + 1))
        with (
            patch("meppp.publishing.video_processing._run_command") as run,
            self.assertRaisesMessage(ValidationError, "20 MB"),
        ):
            process_video_upload(upload=upload)

        run.assert_not_called()

    def test_playlist_is_rejected_before_external_tools_run(self):
        upload = BytesIO(b"#EXTM3U\n#EXTINF:5,remote\nhttp://127.0.0.1/private-video.ts\n")
        with (
            patch("meppp.publishing.video_processing._run_command") as run,
            self.assertRaisesMessage(ValidationError, "MP4 或 WebM"),
        ):
            process_video_upload(upload=upload)

        run.assert_not_called()

    def test_crafted_mp4_prefix_cannot_turn_ffprobe_into_an_http_client(self):
        class BaitHandler(BaseHTTPRequestHandler):
            requests = 0

            def do_GET(self):
                type(self).requests += 1
                self.send_response(204)
                self.end_headers()

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), BaitHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            bait_url = f"http://127.0.0.1:{server.server_port}/private.ts"
            ftyp = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
            upload = BytesIO(ftyp + f"#EXTM3U\n{bait_url}\n".encode())
            with self.assertRaises(ValidationError):
                process_video_upload(upload=upload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(BaitHandler.requests, 0)


@skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are unavailable")
class VideoProcessingIntegrationTests(SimpleTestCase):
    def generate_video(self, destination: Path, *, webm: bool = False) -> None:
        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:r=10:d=0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.6",
            "-shortest",
            "-metadata",
            "title=untrusted-title",
        ]
        if webm:
            command.extend(("-c:v", "libvpx-vp9", "-c:a", "libopus"))
        else:
            command.extend(("-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"))
        command.append(str(destination))
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            shell=False,
            timeout=20,
        )
        if result.returncode != 0:
            self.skipTest("the installed FFmpeg cannot create this test codec")

    def assert_processed_video(self, *, webm: bool) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory, "source.webm" if webm else "source.mp4")
            self.generate_video(source_path, webm=webm)

            with source_path.open("rb") as upload:
                processed = process_video_upload(upload=upload)

            self.assertEqual(processed.byte_size, len(processed.content))
            self.assertLessEqual(processed.duration_ms, 5 * 60 * 1000)
            self.assertEqual(
                processed.mime_type,
                VideoMimeType.WEBM if webm else VideoMimeType.MP4,
            )
            poster_size = verify_poster_bytes(processed.poster_content)
            self.assertEqual(poster_size, (64, 48))
            with Image.open(BytesIO(processed.poster_content)) as poster:
                self.assertEqual(poster.format, "WEBP")

            remuxed_path = Path(temporary_directory, f"remuxed{processed.extension}")
            remuxed_path.write_bytes(processed.content)
            metadata = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_entries",
                    "format_tags=title",
                    str(remuxed_path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                shell=False,
                timeout=10,
            )
            self.assertNotIn("untrusted-title", json.loads(metadata.stdout))

    def test_processes_h264_aac_mp4_and_generates_a_clean_poster(self):
        self.assert_processed_video(webm=False)

    def test_processes_vp9_opus_webm_and_generates_a_clean_poster(self):
        self.assert_processed_video(webm=True)
