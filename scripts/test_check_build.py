"""Tests for the JSON-LD rule in scripts/check_build.py's inline-code check.

JSON-LD is the one kind of <script> element the site may carry: it is data, never run, so
script-src 'none' has nothing to allow for it. These tests pin that the exemption covers
exactly a well-formed data block, and that every script a browser would run still fails.

    python -m unittest discover -s scripts -p "test_*.py"
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_build as cb  # noqa: E402

GOOD = '{"@context":"https://schema.org","@type":"Person","name":"A"}'


class NoScriptNoFonts(unittest.TestCase):
    """The site refuses every script file and every web font: script-src 'none', no font-src."""

    def test_an_external_script_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text('<script src="a.js"></script>', encoding="utf-8")
            failures: list = []
            cb.check_inline_code(root, failures)
        self.assertTrue(any("external script" in f for f in failures), failures)

    def test_a_font_face_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.css").write_text('@font-face { src: url("f.woff2"); }', encoding="utf-8")
            failures: list = []
            cb.check_no_outside_loads(root, "https://kphutt.com", failures)
        self.assertTrue(any("@font-face" in f for f in failures), failures)


def failures_for(html: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "index.html").write_text(f"<!doctype html><html><head>{html}</head></html>", encoding="utf-8")
        failures: list = []
        cb.check_inline_code(root, failures)
        return failures


class JsonLd(unittest.TestCase):
    def assertFails(self, html: str, needle: str) -> None:
        found = failures_for(html)
        self.assertTrue(any(needle in f for f in found), f"no failure mentioning {needle!r}: {found}")

    def test_a_json_ld_object_passes(self):
        self.assertEqual(failures_for(f'<script type="application/ld+json">{GOOD}</script>'), [])

    def test_unquoted_type_as_hugo_minifies_it_passes(self):
        self.assertEqual(failures_for(f"<script type=application/ld+json>{GOOD}</script>"), [])

    def test_a_json_ld_array_passes(self):
        self.assertEqual(failures_for(f'<script type="application/ld+json">[{GOOD}]</script>'), [])

    def test_javascript_still_fails(self):
        self.assertFails("<script>alert(1)</script>", "inline script")

    def test_javascript_typed_script_still_fails(self):
        self.assertFails('<script type="text/javascript">alert(1)</script>', "inline script")

    def test_module_script_still_fails(self):
        self.assertFails('<script type="module">alert(1)</script>', "inline script")

    def test_a_type_that_only_contains_ld_json_fails(self):
        self.assertFails('<script type="application/ld+json+js">alert(1)</script>', "inline script")

    def test_invalid_json_fails(self):
        self.assertFails('<script type="application/ld+json">alert(1)</script>', "is not valid JSON")

    def test_a_json_scalar_fails(self):
        self.assertFails('<script type="application/ld+json">"text"</script>', "not a JSON object or array")

    def test_a_second_type_attribute_fails(self):
        self.assertFails(f'<script type="text/javascript" type="application/ld+json">{GOOD}</script>',
                         "more than one type attribute")

    def test_a_second_type_attribute_after_a_quote_fails(self):
        self.assertFails(f'<script id="x"type="text/javascript" type="application/ld+json">{GOOD}</script>',
                         "more than one type attribute")

    def test_a_src_attribute_fails(self):
        self.assertFails(f'<script type="application/ld+json" src="a.json">{GOOD}</script>', "has a src attribute")

    def test_a_comment_opener_fails(self):
        self.assertFails('<script type="application/ld+json">{"a":"<!--"}</script>', "changes where the element ends")

    def test_a_script_tag_inside_fails(self):
        self.assertFails('<script type="application/ld+json">{"a":"<SCRIPT"}</script>', "changes where the element ends")

    def test_a_closing_tag_inside_leaves_the_rest_as_inline_script(self):
        found = failures_for('<script type="application/ld+json">{"a":"</script><script>alert(1)</script>')
        self.assertTrue(any("inline script" in f for f in found), found)

    def test_data_type_attribute_is_not_a_second_type(self):
        self.assertEqual(failures_for(f'<script data-type="x" type="application/ld+json">{GOOD}</script>'), [])


SITE_POLICY = ("default-src 'none'; script-src 'none'; style-src 'self'; img-src 'self'; "
               "connect-src {}; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class ConnectSrc(unittest.TestCase):
    """connect-src may name the site's own robots.txt and nothing broader."""

    def problems(self, sources: str) -> list:
        return cb.policy_problems(SITE_POLICY.format(sources))

    def test_the_robots_url_passes(self):
        self.assertEqual(cb.ROBOTS_URL, "https://kphutt.com/robots.txt")
        self.assertEqual(self.problems("https://kphutt.com/robots.txt"), [])

    def test_none_passes(self):
        self.assertEqual(self.problems("'none'"), [])

    def test_the_server_config_carries_exactly_the_robots_url(self):
        failures = []
        headers = cb.check_server_headers(failures)
        self.assertEqual(failures, [])
        csp = cb.parse_policy(headers["content-security-policy"])
        self.assertEqual(csp["connect-src"], ["https://kphutt.com/robots.txt"])

    def test_anything_broader_fails(self):
        for broader in (
            "'self'",
            "*",
            "https:",
            "https://kphutt.com",
            "https://kphutt.com/",
            "https://kphutt.com/robots.txt/",
            "https://kphutt.com/*",
            "https://*.kphutt.com/robots.txt",
            "http://kphutt.com/robots.txt",
            "kphutt.com/robots.txt",
            "https://kphutt.com/sitemap.xml",
            "https://example.com/robots.txt",
            "https://kphutt.com/robots.txt https://example.com/",
        ):
            with self.subTest(broader=broader):
                self.assertTrue(any(p.startswith("connect-src allows") for p in self.problems(broader)),
                                self.problems(broader))


if __name__ == "__main__":
    unittest.main()
