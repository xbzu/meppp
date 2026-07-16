from __future__ import annotations

import http.client
import json
import ssl
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlencode

from .parsing import (
    PROVIDER_X,
    PROVIDER_YOUTUBE,
    ExternalURLValidationError,
    ParsedExternalURL,
    canonicalize_author_url,
    canonicalize_x_oembed_url,
)

REQUEST_TIMEOUT_SECONDS = 4.0
MAX_RESPONSE_BYTES = 128 * 1024
MAX_TEXT_LENGTH = 1_000
MAX_TITLE_LENGTH = 500
MAX_AUTHOR_LENGTH = 200
MAX_SUCCESS_TTL = timedelta(hours=24)
MIN_SUCCESS_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    path: str


X_OEMBED_ENDPOINT = Endpoint("publish.x.com", "/oembed")
YOUTUBE_OEMBED_ENDPOINT = Endpoint("www.youtube.com", "/oembed")
OFFICIAL_ENDPOINTS = frozenset({X_OEMBED_ENDPOINT, YOUTUBE_OEMBED_ENDPOINT})


class ExternalMetadataError(RuntimeError):
    pass


class ExternalSourceUnavailable(ExternalMetadataError):
    pass


class OEmbedTransport(Protocol):
    def __call__(
        self,
        endpoint: Endpoint,
        parameters: dict[str, str],
        *,
        timeout: float,
        max_response_bytes: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ExternalMetadata:
    canonical_url: str
    author_name: str
    author_url: str
    title: str
    excerpt: str
    expires_after: timedelta


class _PostExcerptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._paragraph_depth = 0
        self._blocked_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag in {"script", "style", "template"}:
            self._blocked_depth += 1
        elif tag == "p" and self._blocked_depth == 0:
            self._paragraph_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self._blocked_depth:
            self._blocked_depth -= 1
        elif tag == "p" and self._paragraph_depth:
            self._paragraph_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._paragraph_depth and not self._blocked_depth:
            self.parts.append(data)


def _clean_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = " ".join(value.split())
    value = "".join(
        character for character in value if unicodedata.category(character) not in {"Cc", "Cs"}
    )
    return value[:maximum]


def excerpt_from_x_html(value: Any) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return ""
    parser = _PostExcerptParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return ""
    return _clean_text(" ".join(parser.parts), maximum=MAX_TEXT_LENGTH)


def _ttl_from_cache_age(value: Any) -> timedelta:
    try:
        seconds = int(value)
    except TypeError, ValueError:
        return MAX_SUCCESS_TTL
    requested = timedelta(seconds=max(seconds, 0))
    return min(max(requested, MIN_SUCCESS_TTL), MAX_SUCCESS_TTL)


def _request_json(
    endpoint: Endpoint,
    parameters: dict[str, str],
    *,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    if endpoint not in OFFICIAL_ENDPOINTS:
        raise ExternalMetadataError("拒绝访问非官方元数据端点")
    target = f"{endpoint.path}?{urlencode(parameters)}"
    connection = http.client.HTTPSConnection(
        endpoint.host,
        port=443,
        timeout=timeout,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Accept": "application/json",
                "User-Agent": "MEPPP external-metadata/1.0",
            },
        )
        response = connection.getresponse()
        if response.status in {400, 401, 404, 410}:
            raise ExternalSourceUnavailable("外部来源当前不可用")
        if 300 <= response.status < 400:
            raise ExternalMetadataError("官方元数据端点返回重定向，已拒绝跟随")
        if response.status != 200:
            raise ExternalMetadataError("官方元数据端点暂时不可用")
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
        if content_type not in {"application/json", "text/json"}:
            raise ExternalMetadataError("官方元数据响应类型无效")
        content_length = response.getheader("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_response_bytes:
                    raise ExternalMetadataError("官方元数据响应过大")
            except ValueError as error:
                raise ExternalMetadataError("官方元数据响应长度无效") from error
        body = response.read(max_response_bytes + 1)
        if len(body) > max_response_bytes:
            raise ExternalMetadataError("官方元数据响应过大")
    except ExternalMetadataError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise ExternalMetadataError("官方元数据请求失败") from error
    finally:
        connection.close()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExternalMetadataError("官方元数据响应不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise ExternalMetadataError("官方元数据响应结构无效")
    return payload


class OEmbedClient:
    def __init__(
        self,
        *,
        transport: OEmbedTransport = _request_json,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def _request(self, endpoint: Endpoint, parameters: dict[str, str]) -> dict[str, Any]:
        return self.transport(
            endpoint,
            parameters,
            timeout=self.timeout,
            max_response_bytes=self.max_response_bytes,
        )

    def fetch(self, parsed: ParsedExternalURL) -> ExternalMetadata:
        if parsed.provider == PROVIDER_X:
            return self._fetch_x(parsed)
        if parsed.provider == PROVIDER_YOUTUBE:
            return self._fetch_youtube(parsed)
        raise ExternalMetadataError("不支持的外部来源")

    def _fetch_x(self, parsed: ParsedExternalURL) -> ExternalMetadata:
        request_url = f"https://x.com/i/status/{parsed.external_id}"
        payload = self._request(
            X_OEMBED_ENDPOINT,
            {"url": request_url, "omit_script": "true", "dnt": "true"},
        )
        if payload.get("provider_name") != "X" or payload.get("type") != "rich":
            raise ExternalMetadataError("X oEmbed 响应身份无效")
        try:
            canonical_url = canonicalize_x_oembed_url(
                payload.get("url", ""),
                expected_id=parsed.external_id,
            )
            author_url = canonicalize_author_url(
                payload.get("author_url", ""),
                provider=PROVIDER_X,
            )
        except ExternalURLValidationError as error:
            raise ExternalMetadataError(str(error)) from error
        return ExternalMetadata(
            canonical_url=canonical_url,
            author_name=_clean_text(payload.get("author_name"), maximum=MAX_AUTHOR_LENGTH),
            author_url=author_url,
            title="",
            excerpt=excerpt_from_x_html(payload.get("html")),
            expires_after=_ttl_from_cache_age(payload.get("cache_age")),
        )

    def _fetch_youtube(self, parsed: ParsedExternalURL) -> ExternalMetadata:
        payload = self._request(
            YOUTUBE_OEMBED_ENDPOINT,
            {"url": parsed.canonical_url, "format": "json"},
        )
        if payload.get("provider_name") != "YouTube" or payload.get("type") != "video":
            raise ExternalMetadataError("YouTube oEmbed 响应身份无效")
        try:
            author_url = canonicalize_author_url(
                payload.get("author_url", ""),
                provider=PROVIDER_YOUTUBE,
            )
        except ExternalURLValidationError as error:
            raise ExternalMetadataError(str(error)) from error
        return ExternalMetadata(
            canonical_url=parsed.canonical_url,
            author_name=_clean_text(payload.get("author_name"), maximum=MAX_AUTHOR_LENGTH),
            author_url=author_url,
            title=_clean_text(payload.get("title"), maximum=MAX_TITLE_LENGTH),
            excerpt="",
            expires_after=MAX_SUCCESS_TTL,
        )
