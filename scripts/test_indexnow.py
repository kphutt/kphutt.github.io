"""Tests for scripts/indexnow.py: which URLs a deploy submits, and the request it builds."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import indexnow  # noqa: E402

ORIGIN = "https://kphutt.com"


def sitemap(*urls) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc>{f'<lastmod>{mod}</lastmod>' if mod else ''}</url>" for loc, mod in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'.encode()


class Changed(unittest.TestCase):
    def test_unchanged_pages_are_not_submitted(self):
        s = sitemap((f"{ORIGIN}/", "2026-01-01"), (f"{ORIGIN}/a/", "2026-01-02"))
        self.assertEqual(indexnow.changed(s, s, ORIGIN), [])

    def test_a_changed_lastmod_is_submitted(self):
        before = sitemap((f"{ORIGIN}/", "2026-01-01"), (f"{ORIGIN}/a/", "2026-01-02"))
        after = sitemap((f"{ORIGIN}/", "2026-01-05"), (f"{ORIGIN}/a/", "2026-01-02"))
        self.assertEqual(indexnow.changed(before, after, ORIGIN), [f"{ORIGIN}/"])

    def test_a_new_page_is_submitted(self):
        before = sitemap((f"{ORIGIN}/", "2026-01-01"))
        after = sitemap((f"{ORIGIN}/", "2026-01-01"), (f"{ORIGIN}/new/", ""))
        self.assertEqual(indexnow.changed(before, after, ORIGIN), [f"{ORIGIN}/new/"])

    def test_a_removed_page_is_not_submitted(self):
        before = sitemap((f"{ORIGIN}/", "2026-01-01"), (f"{ORIGIN}/gone/", "2026-01-01"))
        after = sitemap((f"{ORIGIN}/", "2026-01-01"))
        self.assertEqual(indexnow.changed(before, after, ORIGIN), [])

    def test_no_readable_old_sitemap_submits_everything(self):
        after = sitemap((f"{ORIGIN}/a/", "2026-01-01"), (f"{ORIGIN}/", "2026-01-01"))
        self.assertEqual(indexnow.changed(b"", after, ORIGIN), [f"{ORIGIN}/", f"{ORIGIN}/a/"])

    def test_urls_on_another_host_are_never_submitted(self):
        after = sitemap(("https://example.com/", "2026-01-01"), (f"{ORIGIN}.evil.example/", "2026-01-01"))
        self.assertEqual(indexnow.changed(b"", after, ORIGIN), [])

    def test_a_url_with_no_loc_is_ignored(self):
        self.assertEqual(indexnow.entries(sitemap(("", "2026-01-01"))), {})


class Payload(unittest.TestCase):
    def test_payload(self):
        self.assertEqual(indexnow.payload([f"{ORIGIN}/"], ORIGIN, "k"), {
            "host": "kphutt.com",
            "key": "k",
            "keyLocation": f"{ORIGIN}/indexnow.txt",
            "urlList": [f"{ORIGIN}/"],
        })

    def test_the_key_file_holds_a_valid_key(self):
        # The protocol allows 8 to 128 characters of a-z, A-Z, 0-9 and dashes.
        key = indexnow.KEY_FILE.read_text(encoding="utf-8").strip()
        self.assertRegex(key, r"^[A-Za-z0-9-]{8,128}$")

    def test_the_origin_is_the_sites_https_base_url(self):
        self.assertEqual(indexnow.base_url(), ORIGIN)


if __name__ == "__main__":
    unittest.main()
