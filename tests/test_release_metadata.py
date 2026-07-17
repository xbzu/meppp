from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from meppp import __version__

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TAG_PATTERN = re.compile(r"v(?P<base>\d+\.\d+\.\d+)-rc\.(?P<rc>\d+)")


def package_version_for_release_tag(value: str) -> str:
    match = RELEASE_TAG_PATTERN.fullmatch(value)
    if match is None:
        raise AssertionError(f"invalid release tag: {value}")
    return f"{match.group('base')}rc{match.group('rc')}"


class ReleaseMetadataTests(unittest.TestCase):
    def test_release_metadata_uses_one_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        package_version = project["project"]["version"]
        self.assertEqual(__version__, package_version)

        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        locked_meppp = next(package for package in lock["package"] if package["name"] == "meppp")
        self.assertEqual(locked_meppp["version"], package_version)

        env_text = (ROOT / ".env.example").read_text()
        image_match = re.search(r"^MEPPP_IMAGE=meppp:(v\S+)$", env_text, re.MULTILINE)
        self.assertIsNotNone(image_match)
        release_tag = image_match.group(1)
        self.assertEqual(package_version_for_release_tag(release_tag), package_version)

        for relative_path in ("deploy/README.md", "docs/OPERATIONS.md"):
            document = (ROOT / relative_path).read_text()
            documented_tags = set(re.findall(r"^RELEASE_TAG=(v\S+)$", document, re.MULTILINE))
            self.assertEqual(documented_tags, {release_tag}, relative_path)


if __name__ == "__main__":
    unittest.main()
