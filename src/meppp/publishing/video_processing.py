from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

from .models import (
    MAX_VIDEO_DURATION_MS,
    MAX_VIDEO_POSTER_BYTES,
    MAX_VIDEO_UPLOAD_BYTES,
    VideoMimeType,
)

FFPROBE_BINARY = "ffprobe"
FFMPEG_BINARY = "ffmpeg"
FFPROBE_TIMEOUT_SECONDS = 8
FFMPEG_REMUX_TIMEOUT_SECONDS = 20
FFMPEG_POSTER_TIMEOUT_SECONDS = 12
MAX_PROBE_OUTPUT_BYTES = 256 * 1024
MAX_COMMAND_ERROR_BYTES = 64 * 1024
MAX_VIDEO_EDGE = 8_192
MAX_VIDEO_PIXELS = 33_554_432
MAX_POSTER_EDGE = 1_280
MAX_POSTER_BYTES = MAX_VIDEO_POSTER_BYTES

MP4_VIDEO_CODECS = frozenset({"h264"})
MP4_AUDIO_CODECS = frozenset({"aac"})
WEBM_VIDEO_CODECS = frozenset({"vp8", "vp9", "av1"})
WEBM_AUDIO_CODECS = frozenset({"opus", "vorbis"})
INPUT_PROTOCOL_WHITELIST = "file,pipe"


@dataclass(frozen=True, slots=True)
class VideoProbe:
    mime_type: str
    duration_ms: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None

    @property
    def extension(self) -> str:
        return ".mp4" if self.mime_type == VideoMimeType.MP4 else ".webm"


@dataclass(frozen=True, slots=True)
class ProcessedVideo:
    content: bytes
    poster_content: bytes
    source_byte_size: int
    byte_size: int
    duration_ms: int
    width: int
    height: int
    mime_type: str

    @property
    def extension(self) -> str:
        return ".mp4" if self.mime_type == VideoMimeType.MP4 else ".webm"


def _read_limited(upload: BinaryIO) -> bytes:
    try:
        upload.seek(0)
        content = upload.read(MAX_VIDEO_UPLOAD_BYTES + 1)
    except (AttributeError, OSError, ValueError) as error:
        raise ValidationError("视频读取失败，请重新选择文件") from error
    if not content:
        raise ValidationError("视频文件不能为空")
    if len(content) > MAX_VIDEO_UPLOAD_BYTES:
        raise ValidationError("视频不能超过 20 MB")
    return content


def _container_mime_type(content: bytes) -> str:
    if len(content) >= 16 and content[4:8] == b"ftyp":
        box_size = int.from_bytes(content[:4], "big")
        if 16 <= box_size <= len(content):
            return VideoMimeType.MP4
    if content.startswith(b"\x1aE\xdf\xa3"):
        return VideoMimeType.WEBM
    raise ValidationError("只支持 MP4 或 WebM 视频文件")


def _safe_input_arguments(mime_type: str) -> list[str]:
    if mime_type == VideoMimeType.MP4:
        return [
            "-protocol_whitelist",
            INPUT_PROTOCOL_WHITELIST,
            "-format_whitelist",
            "mov",
            "-f",
            "mov",
            "-enable_drefs",
            "0",
            "-use_absolute_path",
            "0",
        ]
    if mime_type == VideoMimeType.WEBM:
        return [
            "-protocol_whitelist",
            INPUT_PROTOCOL_WHITELIST,
            "-format_whitelist",
            "matroska,webm",
            "-f",
            "matroska,webm",
        ]
    raise ValidationError("视频封装格式无效")


def _run_command(arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout,
            close_fds=True,
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            },
        )
    except FileNotFoundError as error:
        raise ValidationError("视频处理服务暂不可用") from error
    except subprocess.TimeoutExpired as error:
        raise ValidationError("视频处理超时，请选择更短或更小的视频") from error
    except OSError as error:
        raise ValidationError("视频处理服务暂不可用") from error

    if len(result.stdout) > MAX_PROBE_OUTPUT_BYTES or len(result.stderr) > MAX_COMMAND_ERROR_BYTES:
        raise ValidationError("视频检查结果异常")
    if result.returncode != 0:
        raise ValidationError("视频无法安全处理，请确认格式和编码")
    return result


def _positive_decimal(value) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("视频时长信息无效") from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValidationError("视频时长信息无效")
    return parsed


def _positive_dimension(value, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"视频{label}信息无效")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"视频{label}信息无效") from error
    if parsed <= 0:
        raise ValidationError(f"视频{label}信息无效")
    return parsed


def _parse_probe_payload(payload: dict, *, expected_mime_type: str | None = None) -> VideoProbe:
    if not isinstance(payload, dict):
        raise ValidationError("视频检查结果无效")
    format_details = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_details, dict) or not isinstance(streams, list) or not streams:
        raise ValidationError("视频检查结果无效")
    if len(streams) > 2 or any(not isinstance(stream, dict) for stream in streams):
        raise ValidationError("视频只能包含一个画面轨和至多一个音轨")

    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(video_streams) != 1 or len(audio_streams) > 1:
        raise ValidationError("视频只能包含一个画面轨和至多一个音轨")
    if len(video_streams) + len(audio_streams) != len(streams):
        raise ValidationError("视频不能包含字幕、数据或其他附加轨道")

    video_stream = video_streams[0]
    disposition = video_stream.get("disposition") or {}
    if not isinstance(disposition, dict):
        raise ValidationError("视频检查结果无效")
    if disposition.get("attached_pic"):
        raise ValidationError("文件不包含可播放的视频画面")
    video_codec = str(video_stream.get("codec_name", "")).lower()
    audio_codec = str(audio_streams[0].get("codec_name", "")).lower() if audio_streams else None
    format_names = {
        name.strip().lower()
        for name in str(format_details.get("format_name", "")).split(",")
        if name.strip()
    }

    if "mp4" in format_names and video_codec in MP4_VIDEO_CODECS:
        mime_type = VideoMimeType.MP4
        if audio_codec is not None and audio_codec not in MP4_AUDIO_CODECS:
            raise ValidationError("MP4 音轨只支持 AAC")
    elif "webm" in format_names and video_codec in WEBM_VIDEO_CODECS:
        mime_type = VideoMimeType.WEBM
        if audio_codec is not None and audio_codec not in WEBM_AUDIO_CODECS:
            raise ValidationError("WebM 音轨只支持 Opus 或 Vorbis")
    else:
        raise ValidationError("只支持 H.264 MP4 或 VP8、VP9、AV1 WebM 视频")

    if expected_mime_type is not None and mime_type != expected_mime_type:
        raise ValidationError("视频封装格式发生了意外变化")

    width = _positive_dimension(video_stream.get("width"), label="宽度")
    height = _positive_dimension(video_stream.get("height"), label="高度")
    if width > MAX_VIDEO_EDGE or height > MAX_VIDEO_EDGE or width * height > MAX_VIDEO_PIXELS:
        raise ValidationError("视频分辨率过大")

    duration_values = []
    for candidate in (
        format_details.get("duration"),
        *(stream.get("duration") for stream in streams),
    ):
        if candidate not in (None, "", "N/A"):
            duration_values.append(_positive_decimal(candidate))
    if not duration_values:
        raise ValidationError("无法确认视频时长")
    duration_ms = int((max(duration_values) * 1000).to_integral_value(rounding=ROUND_CEILING))
    if duration_ms > MAX_VIDEO_DURATION_MS:
        raise ValidationError("视频时长不能超过 5 分钟")

    return VideoProbe(
        mime_type=mime_type,
        duration_ms=duration_ms,
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


def probe_video_path(path: Path, *, expected_mime_type: str) -> VideoProbe:
    result = _run_command(
        [
            FFPROBE_BINARY,
            "-v",
            "error",
            *_safe_input_arguments(expected_mime_type),
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-i",
            os.fspath(path),
        ],
        timeout=FFPROBE_TIMEOUT_SECONDS,
    )
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError("视频检查结果无效") from error
    return _parse_probe_payload(payload, expected_mime_type=expected_mime_type)


def _remux_video(source: Path, destination: Path, *, probe: VideoProbe) -> None:
    format_name = "mp4" if probe.mime_type == VideoMimeType.MP4 else "webm"
    arguments = [
        FFMPEG_BINARY,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *_safe_input_arguments(probe.mime_type),
        "-i",
        os.fspath(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-map_metadata:s",
        "-1",
        "-map_chapters",
        "-1",
    ]
    if probe.mime_type == VideoMimeType.MP4:
        arguments.extend(("-movflags", "+faststart"))
    arguments.extend(("-f", format_name, os.fspath(destination)))
    _run_command(arguments, timeout=FFMPEG_REMUX_TIMEOUT_SECONDS)


def _extract_poster_frame(video_path: Path, frame_path: Path, *, mime_type: str) -> None:
    _run_command(
        [
            FFMPEG_BINARY,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *_safe_input_arguments(mime_type),
            "-i",
            os.fspath(video_path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-vf",
            (
                f"scale=w=min({MAX_POSTER_EDGE}\\,iw):"
                f"h=min({MAX_POSTER_EDGE}\\,ih):force_original_aspect_ratio=decrease"
            ),
            "-c:v",
            "png",
            "-threads",
            "1",
            "-f",
            "image2",
            os.fspath(frame_path),
        ],
        timeout=FFMPEG_POSTER_TIMEOUT_SECONDS,
    )


def _encode_poster(frame_path: Path) -> bytes:
    try:
        with Image.open(frame_path, formats=("PNG",)) as source:
            source.load()
            if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
                raise ValidationError("视频海报必须是单帧图片")
            normalized = source.convert("RGB")
            output = BytesIO()
            normalized.save(output, format="WEBP", quality=82, method=4)
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ValidationError("视频海报生成失败") from error

    content = output.getvalue()
    if not content or len(content) > MAX_POSTER_BYTES:
        raise ValidationError("视频海报生成失败")
    verify_poster_bytes(content)
    return content


def verify_poster_bytes(content: bytes) -> tuple[int, int]:
    if not content or len(content) > MAX_POSTER_BYTES:
        raise ValidationError("视频海报文件无效")
    try:
        with Image.open(BytesIO(content), formats=("WEBP",)) as image:
            image.load()
            valid = (
                image.format == "WEBP"
                and getattr(image, "n_frames", 1) == 1
                and image.width > 0
                and image.height > 0
                and image.width <= MAX_POSTER_EDGE
                and image.height <= MAX_POSTER_EDGE
                and not image.getexif()
            )
            dimensions = image.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ValidationError("视频海报文件无效") from error
    if not valid:
        raise ValidationError("视频海报文件无效")
    return dimensions


def verify_poster_path(path: Path) -> tuple[int, int]:
    try:
        if path.stat().st_size > MAX_POSTER_BYTES:
            raise ValidationError("视频海报文件无效")
        content = path.read_bytes()
    except OSError as error:
        raise ValidationError("视频海报文件无效") from error
    return verify_poster_bytes(content)


def process_video_upload(*, upload: BinaryIO) -> ProcessedVideo:
    content = _read_limited(upload)
    expected_mime_type = _container_mime_type(content)
    try:
        with tempfile.TemporaryDirectory(prefix="meppp-video-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            source_path = temporary_root / "source.upload"
            source_path.write_bytes(content)
            source_path.chmod(0o600)

            source_probe = probe_video_path(
                source_path,
                expected_mime_type=expected_mime_type,
            )
            output_path = temporary_root / f"video{source_probe.extension}"
            _remux_video(source_path, output_path, probe=source_probe)
            if not output_path.is_file():
                raise ValidationError("视频安全处理失败")
            output_size = output_path.stat().st_size
            if output_size <= 0 or output_size > MAX_VIDEO_UPLOAD_BYTES:
                raise ValidationError("处理后的视频超过 20 MB")

            output_probe = probe_video_path(
                output_path,
                expected_mime_type=source_probe.mime_type,
            )
            frame_path = temporary_root / "poster.png"
            _extract_poster_frame(
                output_path,
                frame_path,
                mime_type=output_probe.mime_type,
            )
            poster_content = _encode_poster(frame_path)
            output_content = output_path.read_bytes()
    except ValidationError:
        raise
    except OSError as error:
        raise ValidationError("视频安全处理失败") from error

    if len(output_content) != output_size:
        raise ValidationError("视频安全处理失败")
    return ProcessedVideo(
        content=output_content,
        poster_content=poster_content,
        source_byte_size=len(content),
        byte_size=output_size,
        duration_ms=output_probe.duration_ms,
        width=output_probe.width,
        height=output_probe.height,
        mime_type=output_probe.mime_type,
    )
