from pathlib import Path
from xml.etree import ElementTree

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from meppp.configuration.models import SiteConfiguration


class BrandAssetTests(SimpleTestCase):
    def static_path(self, relative_path: str) -> Path:
        located = finders.find(relative_path)
        self.assertIsInstance(located, str)
        return Path(located)

    def test_symbol_variants_are_font_free_vectors(self):
        for filename, expected_colors in (
            ("mark.svg", {"#0284c7", "#fff"}),
            ("mark-dark.svg", {"#38bdf8", "#16181c"}),
        ):
            with self.subTest(filename=filename):
                root = ElementTree.parse(self.static_path(f"web/img/{filename}")).getroot()
                elements = list(root.iter())
                tag_names = {element.tag.rsplit("}", 1)[-1] for element in elements}
                colors = {
                    value.lower()
                    for element in elements
                    for attribute, value in element.attrib.items()
                    if attribute == "fill"
                }

                self.assertNotIn("text", tag_names)
                self.assertNotIn("linearGradient", tag_names)
                self.assertNotIn("radialGradient", tag_names)
                self.assertNotIn("filter", tag_names)
                self.assertEqual(
                    sum(element.tag.rsplit("}", 1)[-1] == "path" for element in elements),
                    1,
                )
                self.assertEqual(
                    sum(element.tag.rsplit("}", 1)[-1] == "circle" for element in elements),
                    3,
                )
                self.assertEqual(colors, expected_colors)
                self.assertFalse(
                    any(
                        "font" in attribute.lower() or "font" in value.lower()
                        for element in elements
                        for attribute, value in element.attrib.items()
                    )
                )

    def test_raster_brand_assets_have_the_required_dimensions(self):
        expected_sizes = {
            "favicon-16.png": (16, 16),
            "favicon-32.png": (32, 32),
            "apple-touch-icon.png": (180, 180),
            "icon-192.png": (192, 192),
            "icon-512.png": (512, 512),
            "og-image.png": (1200, 630),
        }

        for filename, expected_size in expected_sizes.items():
            with self.subTest(filename=filename):
                with Image.open(self.static_path(f"web/img/{filename}")) as image:
                    image.load()
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, expected_size)

        for filename in ("favicon-16.png", "favicon-32.png"):
            with Image.open(self.static_path(f"web/img/{filename}")) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 0)


class BrandPageTests(TestCase):
    def setUp(self):
        SiteConfiguration.objects.create(
            pk=1,
            site_name="冒泡",
            tagline="来冒个泡 · 分享新鲜事，遇见同路人",
        )

    def test_public_head_and_html_lockup_use_the_configured_brand(self):
        response = self.client.get(reverse("web:login"))

        self.assertContains(
            response,
            '<meta name="description" content="来冒个泡 · 分享新鲜事，遇见同路人">',
        )
        self.assertContains(
            response,
            '<meta property="og:site_name" content="冒泡 meppp">',
        )
        self.assertContains(response, '<meta property="og:title" content="冒泡">')
        self.assertContains(
            response,
            '<meta property="og:description" content="来冒个泡 · 分享新鲜事，遇见同路人">',
        )
        self.assertContains(
            response,
            '<meta property="og:image" content="https://meppp.com/static/web/img/og-image.png">',
        )
        self.assertContains(
            response,
            '<meta name="twitter:card" content="summary_large_image">',
        )
        for expected_link in (
            '<link rel="icon" href="/static/web/img/mark.svg" type="image/svg+xml">',
            (
                '<link rel="icon" href="/static/web/img/favicon-32.png" '
                'type="image/png" sizes="32x32">'
            ),
            (
                '<link rel="icon" href="/static/web/img/favicon-16.png" '
                'type="image/png" sizes="16x16">'
            ),
            (
                '<link rel="apple-touch-icon" href="/static/web/img/apple-touch-icon.png" '
                'sizes="180x180">'
            ),
        ):
            self.assertContains(response, expected_link)

        self.assertContains(
            response,
            '<strong class="brand-name">冒泡</strong>',
            count=2,
        )
        self.assertContains(
            response,
            '<span class="brand-product" aria-hidden="true">meppp</span>',
            count=2,
        )

        home_response = self.client.get(reverse("web:home"))
        self.assertContains(
            home_response,
            '<span class="brand-lockup-copy sidebar-brand-copy">',
            count=2,
        )
        self.assertContains(
            home_response,
            '<strong class="brand-name">冒泡</strong>',
            count=3,
        )
        self.assertContains(
            home_response,
            '<span class="brand-product" aria-hidden="true">meppp</span>',
            count=3,
        )

    def test_policy_copy_tracks_site_configuration_instead_of_old_brand_text(self):
        for route_name in ("web:community-rules", "web:privacy-notice"):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertContains(response, "冒泡")
                self.assertNotContains(response, "首版 MEPPP")
                self.assertNotContains(response, "MEPPP 鼓励")
