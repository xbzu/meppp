import unittest

from meppp.external.parsing import (
    PROVIDER_X,
    PROVIDER_YOUTUBE,
    ExternalURLValidationError,
    canonicalize_x_oembed_url,
    parse_external_url,
)


class ExternalURLParsingTests(unittest.TestCase):
    def test_x_variants_collapse_to_id_only_request_url(self):
        urls = (
            "https://x.com/jack/status/20",
            "https://twitter.com/jack/status/20?s=20",
            "https://mobile.twitter.com/jack/status/20/photo/1",
            "https://x.com/i/web/status/20",
            "https://x.com/i/status/20",
        )
        for url in urls:
            with self.subTest(url=url):
                parsed = parse_external_url(url)
                self.assertEqual(parsed.provider, PROVIDER_X)
                self.assertEqual(parsed.external_id, "20")
                self.assertEqual(parsed.canonical_url, "https://x.com/i/status/20")

    def test_x_oembed_canonical_must_keep_same_id(self):
        self.assertEqual(
            canonicalize_x_oembed_url(
                "https://x.com/jack/status/20",
                expected_id="20",
            ),
            "https://x.com/jack/status/20",
        )
        with self.assertRaises(ExternalURLValidationError):
            canonicalize_x_oembed_url(
                "https://x.com/jack/status/21",
                expected_id="20",
            )

    def test_youtube_variants_collapse_to_watch_url(self):
        video_id = "lJIrF4YjHfQ"
        urls = (
            f"https://youtu.be/{video_id}?si=tracking",
            f"https://www.youtube.com/watch?v={video_id}&t=12",
            f"https://youtube.com/shorts/{video_id}?si=tracking",
            f"https://m.youtube.com/live/{video_id}",
            f"https://www.youtube.com/embed/{video_id}",
        )
        for url in urls:
            with self.subTest(url=url):
                parsed = parse_external_url(url)
                self.assertEqual(parsed.provider, PROVIDER_YOUTUBE)
                self.assertEqual(parsed.external_id, video_id)
                self.assertEqual(
                    parsed.canonical_url,
                    f"https://www.youtube.com/watch?v={video_id}",
                )

    def test_rejects_ambiguous_or_non_official_urls(self):
        invalid = (
            "http://x.com/jack/status/20",
            "https://x.com.evil.test/jack/status/20",
            "https://user@x.com/jack/status/20",
            "https://x.com:444/jack/status/20",
            "https://x.com/jack/status/20#fragment",
            "https://x.com/jack/status/not-a-number",
            "https://x.com/not%2Fa%2Fhandle/status/20",
            "https://www.youtube.com/watch///?v=lJIrF4YjHfQ",
            "https://www.youtube.com/watch?v=lJIrF4YjHfQ&v=dQw4w9WgXcQ",
            "https://youtube.com.evil.test/watch?v=lJIrF4YjHfQ",
            "https://youtu.be/lJIrF4YjHfQ/extra",
            "file:///etc/passwd",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ExternalURLValidationError):
                parse_external_url(url)


if __name__ == "__main__":
    unittest.main()
