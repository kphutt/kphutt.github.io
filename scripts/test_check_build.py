"""Tests for the JSON-LD rule in scripts/check_build.py's inline-code check.

JSON-LD is the one kind of <script> element the site may carry: it is data, never run, so
script-src 'none' has nothing to allow for it. These tests pin that the exemption covers
exactly a well-formed data block, and that every script a browser would run still fails.

    python -m unittest discover -s scripts -p "test_*.py"
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_build as cb  # noqa: E402

GOOD = '{"@context":"https://schema.org","@type":"Person","name":"A"}'


class RelMe(unittest.TestCase):
    URLS = ["https://infosec.exchange/@kphutt", "https://github.com/kphutt"]

    def test_a_link_for_every_profile_passes(self):
        html = "".join(f"<link rel=me href={u}>" for u in self.URLS)
        self.assertEqual(cb.rel_me_problems(html, self.URLS), [])

    def test_quoted_attributes_pass(self):
        html = "".join(f'<link href="{u}" rel="me">' for u in self.URLS)
        self.assertEqual(cb.rel_me_problems(html, self.URLS), [])

    def test_a_missing_profile_fails(self):
        html = f"<link rel=me href={self.URLS[1]}>"
        self.assertEqual(len(cb.rel_me_problems(html, self.URLS)), 1)

    def test_another_rel_does_not_count(self):
        html = "".join(f"<link rel=alternate href={u}>" for u in self.URLS)
        self.assertEqual(len(cb.rel_me_problems(html, self.URLS)), 2)

    def test_a_plain_anchor_does_not_count(self):
        html = "".join(f'<a rel="me" href="{u}">x</a>' for u in self.URLS)
        self.assertEqual(len(cb.rel_me_problems(html, self.URLS)), 2)


class RelMeConfig(unittest.TestCase):
    """hugo.toml: rel=me covers sameAs and relMeOnly, and only sameAs is claimed identity."""

    def setUp(self):
        import tomllib
        self.config = tomllib.loads(cb.CONFIG.read_text(encoding="utf-8"))
        self.schema = self.config["params"]["schema"]
        self.profiles = cb.rel_me_profiles(self.config)

    def test_gate_list_has_every_sameas_and_mastodon(self):
        for u in self.schema["sameAs"] + ["https://infosec.exchange/@kphutt"]:
            self.assertIn(u, self.profiles)

    def test_mastodon_verifies_but_is_not_claimed(self):
        self.assertIn("https://infosec.exchange/@kphutt", self.schema["relMeOnly"])
        self.assertNotIn("https://infosec.exchange/@kphutt", self.schema["sameAs"])
        hosts = [urllib.parse.urlsplit(u).hostname for u in self.schema["sameAs"]]
        self.assertFalse([h for h in hosts if h in ("x.com", "twitter.com")])

    def test_a_missing_relmeonly_link_fails(self):
        html = "".join(f"<link rel=me href={u}>" for u in self.schema["sameAs"])
        self.assertEqual(len(cb.rel_me_problems(html, self.profiles)), len(self.schema["relMeOnly"]))

    def test_a_missing_sameas_link_fails(self):
        html = "".join(f"<link rel=me href={u}>" for u in self.schema["relMeOnly"])
        self.assertEqual(len(cb.rel_me_problems(html, self.profiles)), len(self.schema["sameAs"]))


class PersonImage(unittest.TestCase):
    BASE = "https://kphutt.com"
    IMG = "images/kphutt.jpg"
    URL = "https://kphutt.com/images/kphutt.jpg"

    def page(self, image=URL, body=""):
        import json
        node = {"@type": "Person", "name": "A"}
        if image is not None:
            node["image"] = image
        ld = json.dumps({"@context": "https://schema.org", "@graph": [node]})
        return f'<script type="application/ld+json">{ld}</script>{body}'

    def problems(self, html, exists=True):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            if exists:
                (root / "images").mkdir()
                (root / self.IMG).write_bytes(b"x")
            return cb.person_image_problems(html, root, self.IMG, self.BASE)

    def test_config_sets_the_image(self):
        import tomllib
        cfg = tomllib.loads(cb.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["params"]["schema"]["image"], self.IMG)

    def test_correct_page_passes(self):
        self.assertEqual(self.problems(self.page()), [])

    def test_missing_image_fails(self):
        self.assertTrue(self.problems(self.page(image=None)))

    def test_relative_image_fails(self):
        self.assertTrue(self.problems(self.page(image="/images/kphutt.jpg")))

    def test_missing_file_fails(self):
        self.assertTrue(self.problems(self.page(), exists=False))

    def test_visible_img_fails(self):
        for body in (f'<img src="{self.URL}">', "<img src=/images/kphutt.jpg>",
                     '<img src="../images/kphutt.jpg">',
                     '<img src="/x.png" srcset="/a.png 1x, /images/kphutt.jpg 2x">'):
            self.assertTrue(cb.headshot_img_problems(body, self.IMG), body)

    def test_gt_in_alt_cannot_hide_img(self):
        body = '<img alt="a > b" src=/images/kphutt.jpg>'
        self.assertTrue(cb.headshot_img_problems(body, self.IMG), body)
        pic = '<picture><source srcset="/images/kphutt.jpg"><img src="/x.png"></picture>'
        self.assertTrue(cb.headshot_img_problems(pic, self.IMG), pic)

    def test_other_img_passes(self):
        self.assertEqual(cb.headshot_img_problems('<img src="/x.png">', self.IMG), [])
        self.assertEqual(cb.headshot_img_problems('<img src="/old-images/kphutt.jpg">', self.IMG), [])

    def test_img_on_a_non_home_page_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "posts" / "x").mkdir(parents=True)
            (root / "index.html").write_text("<p>home</p>", encoding="utf-8")
            (root / "posts" / "x" / "index.html").write_text(
                '<img src="/images/kphutt.jpg">', encoding="utf-8")
            found = cb.headshot_img_failures(root, self.IMG)
        self.assertEqual(len(found), 1, found)
        self.assertIn("posts/x/index.html", found[0])


class PreviewMeta(unittest.TestCase):
    BASE = "https://kphutt.com"
    IMG = "images/kphutt.jpg"          # the square headshot: twitter:image
    URL = "https://kphutt.com/images/kphutt.jpg"
    WIDE = "images/kphutt-wide.jpg"    # the wide card: og:image
    WURL = "https://kphutt.com/images/kphutt-wide.jpg"

    def page(self, og=WURL, tw=URL, card="summary", alt="Karsten Huttelmaier", quoted=True):
        q = '"' if quoted else ""
        parts = [f"<meta property={q}og:url{q} content={q}https://kphutt.com/{q}>"]
        if og is not None:
            parts.append(f"<meta property={q}og:image{q} content={q}{og}{q}>")
        if alt is not None:
            # A value with a space is always quoted, even in minified output.
            parts.append(f'<meta property={q}og:image:alt{q} content="{alt}">')
        if card is not None:
            parts.append(f"<meta name={q}twitter:card{q} content={q}{card}{q}>")
        if tw is not None:
            parts.append(f"<meta name={q}twitter:image{q} content={q}{tw}{q}>")
        return "".join(parts)

    def problems(self, html, alt="Karsten Huttelmaier"):
        return cb.preview_meta_problems(html, self.WIDE, self.BASE, alt, twitter_image=self.IMG)

    def test_config_sets_the_image(self):
        import tomllib
        cfg = tomllib.loads(cb.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["params"]["images"], [self.WIDE])
        self.assertEqual(cfg["params"]["twitterImage"], self.IMG)
        meta = cfg["params"]["imageMeta"]
        self.assertEqual((meta["width"], meta["height"]), (1200, 630))
        self.assertEqual(cfg["params"]["imageMeta"]["alt"], "Karsten Huttelmaier")

    def test_correct_page_passes(self):
        self.assertEqual(self.problems(self.page()), [])
        self.assertEqual(self.problems(self.page(quoted=False)), [])

    def test_missing_or_wrong_tags_fail(self):
        for kw in ({"og": None}, {"tw": None}, {"card": None}, {"alt": None},
                   {"og": "https://kphutt.com/og-image.png"},
                   {"og": self.URL},       # og:image must be the wide card, not the square
                   {"tw": self.WURL},      # twitter:image must be the square, not the wide card
                   {"tw": "/images/kphutt.jpg"},
                   {"og": "http://kphutt.com/images/kphutt-wide.jpg"},
                   {"card": "summary_large_image"},
                   {"alt": "someone else"}):
            self.assertTrue(self.problems(self.page(**kw)), kw)

    def test_duplicate_image_fails(self):
        html = self.page() + f'<meta property="og:image" content="{self.WURL}">'
        self.assertTrue(self.problems(html))

    def test_alt_is_not_required_when_not_configured(self):
        self.assertEqual(self.problems(self.page(alt=None), alt=""), [])

    def test_every_page_with_og_url_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "posts" / "x").mkdir(parents=True)
            (root / "images").mkdir()
            (root / "images" / "kphutt-wide.jpg").write_bytes(self.jpeg(1200, 630))
            (root / "index.html").write_text(self.size_page(1200, 630), encoding="utf-8")
            (root / "404.html").write_text("<p>no og url here</p>", encoding="utf-8")
            (root / "posts" / "x" / "index.html").write_text(
                self.size_page(1200, 630, card="summary_large_image", og_type="article"),
                encoding="utf-8")
            found = self.failures(root)
        self.assertEqual(len(found), 1, found)
        self.assertIn("posts/x/index.html", found[0])

    def test_no_checkable_page_fails(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "images").mkdir()
            (Path(d) / "images" / "kphutt-wide.jpg").write_bytes(self.jpeg(1200, 630))
            (Path(d) / "index.html").write_text("<p>x</p>", encoding="utf-8")
            self.assertTrue(cb.preview_meta_failures(Path(d), self.WIDE, self.BASE, "", self.IMG))

    @staticmethod
    def jpeg(width, height):
        sof = b"\xff\xc0\x00\x0b\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
        app = b"\xff\xe0\x00\x04\x00\x00"
        return b"\xff\xd8" + app + sof + b"\xff\xd9"

    @staticmethod
    def png(width, height):
        ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR" + ihdr + b"\x00\x00\x00\x00"

    def failures(self, root):
        return cb.preview_meta_failures(root, self.WIDE, self.BASE, "Karsten Huttelmaier", self.IMG)

    def size_page(self, width, height, mime="image/jpeg", og_type="website", **kw):
        return (self.page(**kw) + f'<meta property="og:type" content="{og_type}">' + f'<meta property="og:image:type" content="{mime}">'
                f'<meta property="og:image:width" content="{width}">'
                f'<meta property="og:image:height" content="{height}">')

    def test_jpeg_size_reads_the_frame_header(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.jpg"
            f.write_bytes(self.jpeg(640, 480))
            self.assertEqual(cb.jpeg_size(f), (640, 480))
            f.write_bytes(b"not a jpeg")
            with self.assertRaises(ValueError):
                cb.jpeg_size(f)

    def test_preview_size_must_match_the_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "images").mkdir()
            (root / "images" / "kphutt-wide.jpg").write_bytes(self.jpeg(1200, 630))
            for w, h, mime, bad in ((1200, 630, "image/jpeg", False), (640, 630, "image/jpeg", True),
                                    (1200, 1200, "image/jpeg", True), (1200, 630, "image/png", True)):
                (root / "index.html").write_text(self.size_page(w, h, mime), encoding="utf-8")
                found = self.failures(root)
                self.assertEqual(bool(found), bad, (w, h, mime, found))

    def test_missing_image_file_fails_the_size_check(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text(self.size_page(1200, 630), encoding="utf-8")
            self.assertTrue(cb.preview_meta_failures(Path(d), self.WIDE, self.BASE, "", self.IMG))

    def test_og_type_is_website_on_home_and_article_on_a_post(self):
        self.assertEqual(cb.expected_og_type("index.html"), "website")
        self.assertEqual(cb.expected_og_type("posts/index.html"), "website")
        self.assertEqual(cb.expected_og_type("404.html"), "website")
        self.assertEqual(cb.expected_og_type("posts/brochure/index.html"), "article")

    def test_og_type_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "posts" / "x").mkdir(parents=True)
            (root / "images").mkdir()
            (root / "images" / "kphutt-wide.jpg").write_bytes(self.jpeg(1200, 630))
            (root / "index.html").write_text(self.size_page(1200, 630), encoding="utf-8")
            (root / "posts" / "x" / "index.html").write_text(
                self.size_page(1200, 630, og_type="article"), encoding="utf-8")
            self.assertEqual(self.failures(root), [])
            # The reported bug: the home page says "article".
            (root / "index.html").write_text(
                self.size_page(1200, 630, og_type="article"), encoding="utf-8")
            found = self.failures(root)
            self.assertEqual(len(found), 1, found)
            self.assertIn("index.html: og:type", found[0])
            # A post that says "website" fails too.
            (root / "index.html").write_text(self.size_page(1200, 630), encoding="utf-8")
            (root / "posts" / "x" / "index.html").write_text(
                self.size_page(1200, 630), encoding="utf-8")
            self.assertTrue(self.failures(root))

    def test_no_img_may_show_the_wide_card_either(self):
        self.assertTrue(cb.headshot_img_problems('<img src="/images/kphutt-wide.jpg">', self.WIDE))

    def check_image(self, w, h, mime, name="og-image.png", data=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / name).write_bytes(self.png(1200, 630) if data is None else data)
            (root / "index.html").write_text(
                self.size_page(w, h, mime).replace("images/kphutt-wide.jpg", name)
                .replace("images/kphutt.jpg", name), encoding="utf-8")
            return cb.preview_meta_failures(root, name, self.BASE, "Karsten Huttelmaier")

    def test_png_size_reads_the_header(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.png"
            f.write_bytes(self.png(1200, 630))
            self.assertEqual(cb.image_size(f), ("image/png", 1200, 630))

    def test_png_with_matching_meta_passes(self):
        self.assertEqual(self.check_image(1200, 630, "image/png"), [])

    def test_png_with_stale_width_fails(self):
        self.assertTrue(self.check_image(1200, 630, "image/png", data=self.png(1000, 630)))

    def test_png_declared_as_jpeg_fails(self):
        self.assertTrue(self.check_image(1200, 630, "image/jpeg"))

    def test_png_with_the_old_jpeg_meta_fails(self):
        # The reviewer's case: og-image.png (1200x630) with stale 1200x1200 image/jpeg meta.
        self.assertTrue(self.check_image(1200, 1200, "image/jpeg"))

    def test_unsupported_extension_fails_instead_of_skipping(self):
        found = self.check_image(1200, 630, "image/webp", name="og-image.webp", data=b"RIFF")
        self.assertTrue(found)
        self.assertIn("use JPEG or PNG", found[0])


class SiteDescription(unittest.TestCase):
    """The home page's description is the text under every link preview and search result."""
    MIN = 100

    def test_home_page_and_site_description_are_long_enough_and_agree(self):
        import tomllib
        cfg = tomllib.loads(cb.CONFIG.read_text(encoding="utf-8"))
        front = (cb.CONFIG.parent / "content" / "_index.md").read_text(encoding="utf-8")
        m = re.search(r'^description:\s*"(.*)"\s*$', front, re.M)
        self.assertTrue(m, "content/_index.md has no description")
        site = cfg["params"]["description"]
        self.assertGreaterEqual(len(site), self.MIN)
        self.assertEqual(m.group(1), site)


class BannedWords(unittest.TestCase):
    """No company names on the site (cb.BANNED_WORDS), whole words only, in .html and llms.txt."""

    def found(self, **files):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, text in files.items():
                (root / name.replace("_", ".")).write_text(text, encoding="utf-8")
            return cb.banned_word_failures(root)

    def test_every_banned_name_fails_in_html_and_llms(self):
        for word in cb.BANNED_WORDS:
            self.assertTrue(self.found(index_html=f"<p>at {word.upper()} for years</p>"), word)
            self.assertTrue(self.found(llms_txt=f"worked at {word.lower()}"), word)

    def test_whole_words_only_and_other_files_are_not_scanned(self):
        self.assertEqual(self.found(index_html="<p>laws, Amazonbot, snowflakes</p>",
                                    robots_txt="User-agent: Amazonbot"), [])

    def test_the_real_home_page_source_is_clean(self):
        text = (cb.CONFIG.parent / "content" / "_index.md").read_text(encoding="utf-8")
        self.assertEqual(self.found(index_html=text), [])


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
