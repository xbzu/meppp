from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

PROVIDER_X = "x"
PROVIDER_YOUTUBE = "youtube"
MAX_SOURCE_URL_LENGTH = 2_048

_X_HOSTS = frozenset(
    {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }
)
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_YOUTU_BE_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_X_STATUS_PATH = re.compile(
    r"^/(?:i/(?:web/)?status|[A-Za-z0-9_]{1,30}/status)/(?P<external_id>[0-9]{1,20})"
    r"(?:/(?:photo|video)/[1-4])?/?$",
    re.ASCII,
)
_X_RESPONSE_PATH = re.compile(
    r"^/(?P<username>[A-Za-z0-9_]{1,30})/status/(?P<external_id>[0-9]{1,20})/?$",
    re.ASCII,
)
_YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$", re.ASCII)
_YOUTUBE_PATH = re.compile(
    r"^/(?:shorts|live|embed)/(?P<external_id>[A-Za-z0-9_-]{11})/?$",
    re.ASCII,
)


class ExternalURLValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedExternalURL:
    provider: str
    external_id: str
    canonical_url: str


def _split_https_url(value: str):
    if not isinstance(value, str):
        raise ExternalURLValidationError("外部来源必须是 URL")
    value = value.strip()
    if not value or len(value) > MAX_SOURCE_URL_LENGTH:
        raise ExternalURLValidationError("外部来源 URL 长度无效")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ExternalURLValidationError("外部来源 URL 包含控制字符")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ExternalURLValidationError("外部来源 URL 格式无效") from error
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise ExternalURLValidationError("外部来源必须使用 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ExternalURLValidationError("外部来源 URL 不允许包含用户信息")
    if port not in {None, 443}:
        raise ExternalURLValidationError("外部来源 URL 只允许 443 端口")
    if parsed.fragment:
        raise ExternalURLValidationError("外部来源 URL 不允许包含片段")
    return parsed


def _parse_x(parsed) -> ParsedExternalURL | None:
    if parsed.hostname not in _X_HOSTS:
        return None
    match = _X_STATUS_PATH.fullmatch(parsed.path)
    if match is None:
        raise ExternalURLValidationError("只支持 X 的公开 Post 链接")
    external_id = match.group("external_id")
    return ParsedExternalURL(
        provider=PROVIDER_X,
        external_id=external_id,
        canonical_url=f"https://x.com/i/status/{external_id}",
    )


def _youtube_id_from_url(parsed) -> str | None:
    if parsed.hostname in _YOUTU_BE_HOSTS:
        parts = [part for part in parsed.path.split("/") if part]
        return parts[0] if len(parts) == 1 else None
    if parsed.hostname not in _YOUTUBE_HOSTS:
        return None
    if parsed.path in {"/watch", "/watch/"}:
        values = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
        return values[0] if len(values) == 1 else None
    match = _YOUTUBE_PATH.fullmatch(parsed.path)
    return match.group("external_id") if match else None


def _parse_youtube(parsed) -> ParsedExternalURL | None:
    recognised_host = parsed.hostname in _YOUTUBE_HOSTS | _YOUTU_BE_HOSTS
    if not recognised_host:
        return None
    external_id = _youtube_id_from_url(parsed)
    if external_id is None or _YOUTUBE_VIDEO_ID.fullmatch(external_id) is None:
        raise ExternalURLValidationError("只支持 YouTube 的单个视频链接")
    return ParsedExternalURL(
        provider=PROVIDER_YOUTUBE,
        external_id=external_id,
        canonical_url=f"https://www.youtube.com/watch?v={external_id}",
    )


def parse_external_url(value: str) -> ParsedExternalURL:
    parsed = _split_https_url(value)
    result = _parse_x(parsed) or _parse_youtube(parsed)
    if result is None:
        raise ExternalURLValidationError("当前只支持 X 和 YouTube 链接")
    return result


def canonicalize_x_oembed_url(value: str, *, expected_id: str) -> str:
    parsed = _split_https_url(value)
    if parsed.hostname not in _X_HOSTS:
        raise ExternalURLValidationError("X oEmbed 返回了非官方 URL")
    match = _X_RESPONSE_PATH.fullmatch(parsed.path)
    if match is None or match.group("external_id") != expected_id:
        raise ExternalURLValidationError("X oEmbed 返回了不匹配的 Post")
    username = match.group("username")
    return f"https://x.com/{username}/status/{expected_id}"


def canonicalize_author_url(value: str, *, provider: str) -> str:
    if not value:
        return ""
    if provider not in {PROVIDER_X, PROVIDER_YOUTUBE}:
        raise ExternalURLValidationError("不支持的作者来源平台")
    parsed = _split_https_url(value)
    allowed_hosts = _X_HOSTS if provider == PROVIDER_X else _YOUTUBE_HOSTS
    if parsed.hostname not in allowed_hosts:
        raise ExternalURLValidationError("oEmbed 返回了非官方作者 URL")
    if parsed.query:
        raise ExternalURLValidationError("oEmbed 作者 URL 不允许包含查询参数")
    path = parsed.path.rstrip("/") or "/"
    canonical_host = "x.com" if provider == PROVIDER_X else "www.youtube.com"
    return f"https://{canonical_host}{path}"
