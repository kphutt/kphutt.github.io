"""Unit tests for the explorables gate. Standard library only, no network.

    python -m unittest discover -s scripts -p "test_*.py"

Each test starts from a small explorables tree that passes, breaks one rule, and checks the
gate names it. The tree is built with scripts/build_explorables.py, so the tests also prove
the build leaves every README out.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_explorables  # noqa: E402
import check_build as cb  # noqa: E402
import check_explorables as ce  # noqa: E402

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Demo</title>
<meta name="description" content="A demo explorable.">
<link rel="canonical" href="https://explorables.kphutt.com/demo/">
<link rel="stylesheet" href="demo.css">
</head>
<body>
<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"></circle></svg>
<button type="button" id="toggle">Toggle</button>
<script type="module" src="demo.js"></script>
</body>
</html>
"""

SCRIPT = """import { draw } from "./draw.js";
document.getElementById("toggle").addEventListener("click", () => draw());
"""

STYLE = """@font-face { font-family: Demo; src: url("demo.woff2") format("woff2"); }
body { font-family: Demo, sans-serif; }
"""


class Tree:
    """A passing explorables source tree, its built output and copies of the server configs."""

    def __init__(self, tmp: Path):
        self.source = tmp / "explorables"
        shutil.copytree(ce.SOURCE, self.source, ignore=lambda d, names: [n for n in names if n != "README.md" and Path(d, n).is_dir()])
        demo = self.source / "demo"
        demo.mkdir()
        (demo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (demo / "index.html").write_text(PAGE, encoding="utf-8")
        (demo / "demo.js").write_text(SCRIPT, encoding="utf-8")
        (demo / "draw.js").write_text("export function draw() {}\n", encoding="utf-8")
        (demo / "demo.css").write_text(STYLE, encoding="utf-8")
        (demo / "demo.woff2").write_bytes(b"wOF2")
        self.root = tmp / "public-explorables"
        self.server = tmp / "server"
        self.server.mkdir()
        self.config = self.server / "explorables.sws.toml"
        shutil.copy(ce.CONFIG, self.config)
        self.site_config = self.server / "sws.toml"
        shutil.copy(cb.SERVER_CONFIG, self.site_config)
        self.dockerfiles = (tmp / "Dockerfile", tmp / "explorables.Dockerfile")
        for real, copy in zip(ce.DOCKERFILES, self.dockerfiles):
            shutil.copy(real, copy)

    def build(self) -> None:
        build_explorables.build(self.source, self.root)

    def failures(self) -> list:
        return ce.check(self.root, self.source, self.config, self.site_config, self.dockerfiles)

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{old!r} not in {path}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


class GateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = Tree(Path(self._tmp.name))
        self.tree.build()

    def tearDown(self):
        self._tmp.cleanup()

    def page(self) -> Path:
        return self.tree.root / "demo" / "index.html"

    def assertFails(self, needle: str) -> None:
        failures = self.tree.failures()
        self.assertTrue(any(needle in f for f in failures), f"no failure mentioning {needle!r}: {failures}")


class Passing(GateTest):
    def test_the_fixture_passes(self):
        self.assertEqual(self.tree.failures(), [])

    def test_the_real_tree_passes(self):
        with tempfile.TemporaryDirectory() as out:
            build_explorables.build(ce.SOURCE, Path(out) / "built")
            self.assertEqual(ce.check(Path(out) / "built"), [])

    def test_the_build_leaves_readmes_out(self):
        self.assertTrue((self.tree.source / "demo" / "README.md").is_file())
        self.assertFalse((self.tree.root / "demo" / "README.md").exists())
        self.assertTrue((self.tree.root / "demo" / "index.html").is_file())


class InlineCode(GateTest):
    def test_inline_script(self):
        self.tree.edit(self.page(), "</body>", "<script>alert(1)</script></body>")
        self.assertFails("inline script")

    def test_script_with_src_and_a_body(self):
        self.tree.edit(self.page(), 'src="demo.js"></script>', 'src="demo.js">alert(1)</script>')
        self.assertFails("demo/index.html: external script")

    def test_unclosed_script(self):
        self.tree.edit(self.page(), "</body>", "<script src=demo.js></body>")
        self.assertFails("without its </script>")

    def test_style_element(self):
        self.tree.edit(self.page(), "</head>", "<style>body{}</style></head>")
        self.assertFails("<style> element")

    def test_style_attribute(self):
        self.tree.edit(self.page(), '<button type="button"', '<button style="color:red" type="button"')
        self.assertFails("style= attribute")

    def test_event_handler(self):
        self.tree.edit(self.page(), '<button type="button"', '<button onclick="go()" type="button"')
        self.assertFails("onclick=")

    def test_event_handler_in_svg(self):
        self.tree.edit(self.page(), "<circle ", "<circle onload=go() ")
        self.assertFails("onload=")

    def test_javascript_url(self):
        self.tree.edit(self.page(), "</body>", '<a href="javascript:go()">x</a></body>')
        self.assertFails("javascript: URL")


class OtherOrigins(GateTest):
    def test_script_from_another_origin(self):
        self.tree.edit(self.page(), 'src="demo.js"', 'src="https://cdn.example.com/x.js"')
        self.assertFails("from another origin")

    def test_protocol_relative_script(self):
        self.tree.edit(self.page(), 'src="demo.js"', 'src="//cdn.example.com/x.js"')
        self.assertFails("from another origin")

    def test_script_by_absolute_url_on_this_origin(self):
        self.tree.edit(self.page(), 'src="demo.js"', f'src="{ce.ORIGIN}/demo/demo.js"')
        self.assertFails("absolute URL")

    def test_module_import_from_another_origin(self):
        self.tree.edit(self.tree.root / "demo" / "demo.js", '"./draw.js"', '"https://cdn.example.com/draw.js"')
        self.assertFails("from another origin")

    def test_dynamic_import_from_another_origin(self):
        self.tree.edit(self.tree.root / "demo" / "demo.js", "draw());", 'import("https://cdn.example.com/x.js"));')
        self.assertFails("from another origin")

    def test_stylesheet_from_another_origin(self):
        self.tree.edit(self.page(), 'href="demo.css"', 'href="https://fonts.example.com/x.css"')
        self.assertFails("from another origin")

    def test_font_from_another_origin(self):
        self.tree.edit(self.tree.root / "demo" / "demo.css", 'url("demo.woff2")', 'url("https://fonts.example.com/x.woff2")')
        self.assertFails("from another origin")

    def test_image_from_another_origin(self):
        self.tree.edit(self.page(), "</body>", '<img src="https://img.example.com/x.png" alt=""></body>')
        self.assertFails("from another origin")

    def test_css_import(self):
        self.tree.edit(self.tree.root / "demo" / "demo.css", "body {", '@import "other.css";\nbody {')
        self.assertFails("@import")

    def test_a_bundled_font_is_allowed(self):
        # @font-face is refused on the main site; here it is allowed, with a same-origin url().
        self.assertEqual(self.tree.failures(), [])


class Findable(GateTest):
    def test_a_page_with_noindex(self):
        self.tree.edit(self.page(), "<title>", '<meta name="robots" content="noindex"><title>')
        self.assertFails("keeps the page out of search")

    def test_a_page_with_robots_none(self):
        self.tree.edit(self.page(), "<title>", '<meta name="robots" content="none"><title>')
        self.assertFails("keeps the page out of search")

    def test_a_page_with_noindex_for_one_crawler(self):
        self.tree.edit(self.page(), "<title>", "<meta name=googlebot content='follow, NoIndex'><title>")
        self.assertFails("keeps the page out of search")

    def test_the_landing_page_with_noindex(self):
        self.tree.edit(self.tree.root / "index.html", "<title>", '<meta name="robots" content="noindex"><title>')
        self.assertFails("index.html: a robots meta tag says noindex")

    def test_nofollow_alone_is_allowed(self):
        self.tree.edit(self.page(), "<title>", '<meta name="robots" content="nofollow"><title>')
        self.assertEqual(self.tree.failures(), [])

    def test_the_404_page_must_say_noindex(self):
        self.tree.edit(self.tree.root / "404.html", '<meta name="robots" content="noindex">', "")
        self.assertFails("404.html: no <meta")

    def test_missing_title(self):
        self.tree.edit(self.page(), "<title>Demo</title>", "")
        self.assertFails("no <title>")

    def test_empty_title(self):
        self.tree.edit(self.page(), "<title>Demo</title>", "<title> </title>")
        self.assertFails("no <title>")

    def test_missing_description(self):
        self.tree.edit(self.page(), '<meta name="description" content="A demo explorable.">', "")
        self.assertFails("description")

    def test_empty_description(self):
        self.tree.edit(self.page(), 'content="A demo explorable."', 'content=" "')
        self.assertFails("description")

    def test_two_descriptions(self):
        self.tree.edit(self.page(), "<title>", '<meta name="description" content="Another."><title>')
        self.assertFails("description")

    def test_missing_canonical(self):
        self.tree.edit(self.page(), '<link rel="canonical" href="https://explorables.kphutt.com/demo/">', "")
        self.assertFails("canonical link is []")

    def test_canonical_to_another_page(self):
        self.tree.edit(self.page(), 'href="https://explorables.kphutt.com/demo/"', 'href="https://explorables.kphutt.com/"')
        self.assertFails("canonical link")

    def test_canonical_on_another_host(self):
        self.tree.edit(self.page(), 'href="https://explorables.kphutt.com/demo/"', 'href="https://kphutt.com/demo/"')
        self.assertFails("canonical link")

    def test_relative_canonical(self):
        self.tree.edit(self.page(), 'href="https://explorables.kphutt.com/demo/"', 'href="/demo/"')
        self.assertFails("canonical link")


class Discovery(GateTest):
    def sitemap(self) -> Path:
        return self.tree.root / "sitemap.xml"

    def test_the_build_lists_every_explorable(self):
        text = self.sitemap().read_text(encoding="utf-8")
        self.assertIn("<loc>https://explorables.kphutt.com/</loc>", text)
        self.assertIn("<loc>https://explorables.kphutt.com/demo/</loc>", text)
        self.assertIn("Sitemap: https://explorables.kphutt.com/sitemap.xml",
                      (self.tree.root / "robots.txt").read_text(encoding="utf-8"))

    def test_the_build_refuses_a_hand_written_sitemap(self):
        (self.tree.source / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.tree.build()

    def test_an_explorable_missing_from_the_sitemap(self):
        self.tree.edit(self.sitemap(), "  <url><loc>https://explorables.kphutt.com/demo/</loc></url>\n", "")
        self.assertFails("does not list https://explorables.kphutt.com/demo/")

    def test_the_landing_page_missing_from_the_sitemap(self):
        self.tree.edit(self.sitemap(), "  <url><loc>https://explorables.kphutt.com/</loc></url>\n", "")
        self.assertFails("does not list https://explorables.kphutt.com/")

    def test_a_path_that_is_not_built(self):
        self.tree.edit(self.sitemap(), "</urlset>", "  <url><loc>https://explorables.kphutt.com/gone/</loc></url>\n</urlset>")
        self.assertFails("not a page in the build")

    def test_a_file_that_is_not_a_page(self):
        self.tree.edit(self.sitemap(), "</urlset>", "  <url><loc>https://explorables.kphutt.com/demo/demo.js</loc></url>\n</urlset>")
        self.assertFails("not a page in the build")

    def test_a_page_without_its_trailing_slash(self):
        # /demo only redirects to /demo/, so it is not the page's URL.
        self.tree.edit(self.sitemap(), "https://explorables.kphutt.com/demo/</loc>", "https://explorables.kphutt.com/demo</loc>")
        self.assertFails("not a page in the build")

    def test_a_url_on_another_host(self):
        self.tree.edit(self.sitemap(), "</urlset>", "  <url><loc>https://kphutt.com/</loc></url>\n</urlset>")
        self.assertFails("not an absolute URL on")

    def test_a_relative_url(self):
        self.tree.edit(self.sitemap(), "</urlset>", "  <url><loc>/demo/</loc></url>\n</urlset>")
        self.assertFails("not an absolute URL on")

    def test_a_url_listed_twice(self):
        self.tree.edit(self.sitemap(), "</urlset>", "  <url><loc>https://explorables.kphutt.com/demo/</loc></url>\n</urlset>")
        self.assertFails("twice")

    def test_invalid_xml(self):
        self.sitemap().write_text("<urlset", encoding="utf-8")
        self.assertFails("not valid XML")

    def test_missing_sitemap(self):
        self.sitemap().unlink()
        self.assertFails("sitemap.xml missing")

    def test_missing_robots_txt(self):
        (self.tree.root / "robots.txt").unlink()
        self.assertFails("robots.txt missing")

    def test_robots_txt_without_the_sitemap(self):
        self.tree.edit(self.tree.root / "robots.txt", "Sitemap: https://explorables.kphutt.com/sitemap.xml\n", "")
        self.assertFails("no 'Sitemap:")

    def test_robots_txt_blocking_everything(self):
        self.tree.edit(self.tree.root / "robots.txt", "Allow: /", "Disallow: /")
        self.assertFails("keeps pages out of search")

    def test_robots_txt_blocking_one_explorable(self):
        self.tree.edit(self.tree.root / "robots.txt", "Allow: /", "Allow: /\nDisallow: /demo/")
        self.assertFails("keeps pages out of search")


class Layout(GateTest):
    def test_explorable_without_a_readme(self):
        (self.tree.source / "demo" / "README.md").unlink()
        self.assertFails("no README.md")

    def test_explorable_without_an_index(self):
        (self.tree.source / "demo" / "index.html").unlink()
        self.assertFails("no index.html")

    def test_a_folder_that_is_not_a_slug(self):
        shutil.copytree(self.tree.source / "demo", self.tree.source / "Demo_2")
        self.assertFails("not a slug")

    def test_a_readme_in_the_output(self):
        (self.tree.root / "demo" / "README.md").write_text("# Demo\n", encoding="utf-8")
        self.assertFails("would serve it")

    def test_a_readme_with_another_name_in_the_output(self):
        (self.tree.root / "demo" / "readme.txt").write_text("x\n", encoding="utf-8")
        self.assertFails("would serve it")

    def test_dot_file(self):
        (self.tree.root / "demo" / ".env").write_text("x\n", encoding="utf-8")
        self.assertFails("hidden files")

    def test_dot_folder(self):
        (self.tree.root / ".git").mkdir()
        (self.tree.root / ".git" / "config").write_text("x\n", encoding="utf-8")
        self.assertFails("hidden files")

    def test_missing_404_page(self):
        (self.tree.root / "404.html").unlink()
        self.assertFails("404.html missing")

    def test_broken_internal_reference(self):
        self.tree.edit(self.page(), 'href="demo.css"', 'href="gone.css"')
        self.assertFails("broken internal reference")

    def test_different_base_images(self):
        self.tree.edit(self.tree.dockerfiles[1], "2.44.1@", "2.44.0@")
        self.assertFails("same pinned image")


CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
       "require-trusted-types-for 'script'")


class Headers(GateTest):
    def csp(self, old: str, new: str) -> None:
        self.assertIn(CSP, self.tree.config.read_text(encoding="utf-8"))
        self.tree.edit(self.tree.config, CSP, CSP.replace(old, new, 1))

    def test_the_brief_policy_is_what_ships(self):
        self.assertIn(f'Content-Security-Policy = "{CSP}"', ce.CONFIG.read_text(encoding="utf-8"))

    def test_unsafe_inline(self):
        self.csp("script-src 'self'", "script-src 'self' 'unsafe-inline'")
        self.assertFails("script-src allows 'unsafe-inline'")

    def test_unsafe_eval(self):
        self.csp("script-src 'self'", "script-src 'self' 'unsafe-eval'")
        self.assertFails("script-src allows 'unsafe-eval'")

    def test_hash(self):
        self.csp("script-src 'self'", "script-src 'self' 'sha256-abc='")
        self.assertFails("script-src allows 'sha256-abc='")

    def test_nonce(self):
        self.csp("script-src 'self'", "script-src 'self' 'nonce-abc'")
        self.assertFails("script-src allows 'nonce-abc'")

    def test_another_origin(self):
        self.csp("script-src 'self'", "script-src 'self' https://cdn.example.com")
        self.assertFails("script-src allows https://cdn.example.com")

    def test_wildcard(self):
        self.csp("img-src 'self'", "img-src *")
        self.assertFails("img-src allows *")

    def test_default_src_self(self):
        self.csp("default-src 'none'", "default-src 'self'")
        self.assertFails("default-src allows 'self'")

    def test_connect_src(self):
        self.csp("form-action 'none'", "form-action 'none'; connect-src 'self'")
        self.assertFails("connect-src")

    def test_even_connect_src_none(self):
        self.csp("form-action 'none'", "form-action 'none'; connect-src 'none'")
        self.assertFails("has connect-src")

    def test_an_unlisted_fetch_directive(self):
        self.csp("form-action 'none'", "form-action 'none'; script-src-elem 'self'")
        self.assertFails("script-src-elem allows 'self'")

    def test_missing_directive(self):
        self.csp("frame-ancestors 'none'; ", "")
        self.assertFails("frame-ancestors is missing")

    def test_missing_trusted_types(self):
        self.csp("; require-trusted-types-for 'script'", "")
        self.assertFails("require-trusted-types-for")

    def test_trusted_types_with_another_value(self):
        # Browsers ignore a value other than 'script', which turns Trusted Types off.
        self.csp("require-trusted-types-for 'script'", "require-trusted-types-for 'none'")
        self.assertFails("require-trusted-types-for 'script'")

    def test_missing_policy(self):
        self.tree.edit(self.tree.config, "Content-Security-Policy = ", "X-Not-A-Policy = ")
        self.assertFails("no Content-Security-Policy")

    def test_noindex_header(self):
        self.tree.edit(self.tree.config, 'Cache-Control = "public, max-age=300"', 'Cache-Control = "public, max-age=300"\nX-Robots-Tag = "noindex"')
        self.assertFails("would keep every page out of search")

    def test_robots_none_header(self):
        self.tree.edit(self.tree.config, 'Cache-Control = "public, max-age=300"', 'Cache-Control = "public, max-age=300"\nX-Robots-Tag = "none"')
        self.assertFails("would keep every page out of search")

    def test_noindex_header_on_a_narrower_rule(self):
        self.tree.edit(self.tree.config, 'headers.Cache-Control = "public, max-age=86400"', 'headers.X-Robots-Tag = "noindex"')
        self.assertFails("override the catch-all")

    def test_weaker_referrer_policy(self):
        self.tree.edit(self.tree.config, '"no-referrer"', '"strict-origin-when-cross-origin"')
        self.assertFails("Referrer-Policy")

    def test_missing_nosniff(self):
        self.tree.edit(self.tree.config, 'X-Content-Type-Options = "nosniff"\n', "")
        self.assertFails("X-Content-Type-Options")

    def test_missing_coop(self):
        self.tree.edit(self.tree.config, 'Cross-Origin-Opener-Policy = "same-origin"\n', "")
        self.assertFails("Cross-Origin-Opener-Policy")

    def test_missing_corp(self):
        self.tree.edit(self.tree.config, 'Cross-Origin-Resource-Policy = "same-origin"\n', "")
        self.assertFails("Cross-Origin-Resource-Policy")

    def test_hsts_include_subdomains(self):
        self.tree.edit(self.tree.config, '"max-age=63072000"', '"max-age=63072000; includeSubDomains"')
        self.assertFails("includesubdomains")

    def test_hsts_too_short(self):
        self.tree.edit(self.tree.config, '"max-age=63072000"', '"max-age=600"')
        self.assertFails("max-age must be at least")

    def test_permissions_policy_differs_from_the_site(self):
        self.tree.edit(self.tree.config, "camera=(), ", "")
        self.assertFails("Permissions-Policy must equal")

    def test_missing_cache_control(self):
        self.tree.edit(self.tree.config, 'Cache-Control = "public, max-age=300"\n', "")
        self.assertFails("Cache-Control")

    def test_a_narrower_rule_overriding_a_security_header(self):
        self.tree.edit(self.tree.config, 'headers.Cache-Control = "public, max-age=86400"',
                       'headers.Content-Security-Policy = "default-src *"')
        self.assertFails("override the catch-all")

    def test_builtin_security_headers_on(self):
        self.tree.edit(self.tree.config, "security-headers = false", "security-headers = true")
        self.assertFails("security-headers must be false")

    def test_no_404_page_configured(self):
        self.tree.edit(self.tree.config, 'page404 = "404.html"\n', "")
        self.assertFails("page404")

    def test_hidden_files_served(self):
        self.tree.edit(self.tree.config, "ignore-hidden-files = true", "ignore-hidden-files = false")
        self.assertFails("ignore-hidden-files")

    def test_directory_listing(self):
        self.tree.edit(self.tree.config, "directory-listing = false", "directory-listing = true")
        self.assertFails("directory-listing")


class MainGateUnchanged(unittest.TestCase):
    """The helpers check_build.py now shares keep the site's own rules by default."""

    def test_site_floor_still_refuses_script_src_self(self):
        self.assertTrue(any("script-src allows 'self'" in p for p in cb.policy_problems(
            "default-src 'none'; script-src 'self'; base-uri 'none'; form-action 'none'")))

    def test_site_headers_still_pass(self):
        failures = []
        self.assertIsNotNone(cb.check_server_headers(failures))
        self.assertEqual(failures, [])

    def test_site_still_refuses_external_scripts_and_fonts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text('<script src="a.js"></script>', encoding="utf-8")
            (root / "a.css").write_text('@font-face { src: url("f.woff2"); }', encoding="utf-8")
            failures = []
            cb.check_inline_code(root, failures)
            cb.check_no_outside_loads(root, "https://kphutt.com", failures)
            self.assertTrue(any("external script" in f for f in failures), failures)
            self.assertTrue(any("@font-face" in f for f in failures), failures)


if __name__ == "__main__":
    unittest.main()
