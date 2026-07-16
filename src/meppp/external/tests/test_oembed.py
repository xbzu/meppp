import json
import unittest
from unittest.mock import patch

from meppp.external.oembed import (
    MAX_RESPONSE_BYTES,
    X_OEMBED_ENDPOINT,
    YOUTUBE_OEMBED_ENDPOINT,
    ExternalMetadataError,
    OEmbedClient,
    _request_json,
    excerpt_from_x_html,
)
from meppp.external.parsing import parse_external_url


class RecordingTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, endpoint, parameters, **options):
        self.calls.append((endpoint, parameters, options))
        return self.payload


class OEmbedClientTests(unittest.TestCase):
    def test_x_uses_fixed_endpoint_and_discards_html_after_plain_text(self):
        transport = RecordingTransport(
            {
                "provider_name": "X",
                "type": "rich",
                "url": "https://x.com/jack/status/20",
                "author_name": "jack",
                "author_url": "https://x.com/jack",
                "cache_age": "3153600000",
                "html": (
                    "<blockquote><p>Hello <script>alert(1)</script>"
                    '<a href="https://example.test">world</a></p> attribution</blockquote>'
                ),
            }
        )
        metadata = OEmbedClient(transport=transport).fetch(
            parse_external_url("https://x.com/jack/status/20")
        )
        endpoint, parameters, options = transport.calls[0]
        self.assertEqual(endpoint, X_OEMBED_ENDPOINT)
        self.assertEqual(parameters["url"], "https://x.com/i/status/20")
        self.assertEqual(parameters["omit_script"], "true")
        self.assertEqual(parameters["dnt"], "true")
        self.assertEqual(options["max_response_bytes"], MAX_RESPONSE_BYTES)
        self.assertEqual(metadata.canonical_url, "https://x.com/jack/status/20")
        self.assertEqual(metadata.excerpt, "Hello world")
        self.assertFalse(hasattr(metadata, "html"))

    def test_x_rejects_response_with_different_post_id(self):
        transport = RecordingTransport(
            {
                "provider_name": "X",
                "type": "rich",
                "url": "https://x.com/jack/status/21",
                "author_name": "jack",
                "author_url": "https://x.com/jack",
                "html": "<p>wrong</p>",
            }
        )
        with self.assertRaises(ExternalMetadataError):
            OEmbedClient(transport=transport).fetch(
                parse_external_url("https://x.com/jack/status/20")
            )

    def test_youtube_uses_official_oembed_and_retains_no_remote_media(self):
        transport = RecordingTransport(
            {
                "provider_name": "YouTube",
                "type": "video",
                "title": "Embed videos and playlists",
                "author_name": "YouTube Viewers",
                "author_url": "https://www.youtube.com/@YouTubeViewers",
                "thumbnail_url": "https://i.ytimg.com/vi/example/default.jpg",
                "html": "<iframe src='https://www.youtube.com/embed/example'></iframe>",
            }
        )
        metadata = OEmbedClient(transport=transport).fetch(
            parse_external_url("https://youtu.be/lJIrF4YjHfQ")
        )
        endpoint, parameters, _ = transport.calls[0]
        self.assertEqual(endpoint, YOUTUBE_OEMBED_ENDPOINT)
        self.assertEqual(
            parameters["url"],
            "https://www.youtube.com/watch?v=lJIrF4YjHfQ",
        )
        self.assertEqual(metadata.title, "Embed videos and playlists")
        self.assertFalse(hasattr(metadata, "thumbnail_url"))
        self.assertFalse(hasattr(metadata, "html"))

    def test_excerpt_parser_ignores_content_outside_first_post_paragraph(self):
        self.assertEqual(
            excerpt_from_x_html("<blockquote><p>post text</p> author text</blockquote>"),
            "post text",
        )


class _FakeResponse:
    def __init__(self, *, status=200, body=b"{}", content_type="application/json"):
        self.status = status
        self.body = body
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, amount):
        return self.body[:amount]


class _FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class DefaultTransportTests(unittest.TestCase):
    def test_redirect_is_not_followed(self):
        connection = _FakeConnection(_FakeResponse(status=302))
        with patch("meppp.external.oembed.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(ExternalMetadataError, "重定向"):
                _request_json(
                    X_OEMBED_ENDPOINT,
                    {"url": "https://x.com/i/status/20"},
                    timeout=1,
                    max_response_bytes=MAX_RESPONSE_BYTES,
                )
        self.assertEqual(len(connection.requests), 1)
        self.assertTrue(connection.closed)

    def test_oversized_response_is_rejected_before_json_decode(self):
        body = json.dumps({"value": "x" * 200}).encode()
        connection = _FakeConnection(_FakeResponse(body=body))
        with patch("meppp.external.oembed.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(ExternalMetadataError, "响应过大"):
                _request_json(
                    YOUTUBE_OEMBED_ENDPOINT,
                    {"url": "https://www.youtube.com/watch?v=lJIrF4YjHfQ"},
                    timeout=1,
                    max_response_bytes=64,
                )


if __name__ == "__main__":
    unittest.main()
