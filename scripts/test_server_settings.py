"""Unit tests for the rule that every server feature the sites do not use stays off.

    python -m unittest discover -s scripts -p "test_*.py"

Each test starts from a copy of the site's server config, turns one feature back on, leaves
it out or misspells it, and checks the gate names it. explorables.kphutt.com's config is
checked by the same rule in kphutt/explorables.
"""

from __future__ import annotations

import ast
import copy
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_build as cb  # noqa: E402

# A value that turns each feature back on.
ON = {
    "health": True,
    "metrics": True,
    "maintenance-mode": True,
    "accept-markdown": True,
    "cors-allow-origins": "https://example.com",
    "basic-auth": "user:$2y$05$hash",
    "page-fallback": "index.html",
    "directory-listing": True,
    "directory-listing-download": ["targz"],
    "compression-static": True,
    "follow-symlinks": True,
    "tls": True,
    "http2": True,
    "https-redirect": True,
    "use-relative-root": True,
    "etag": True,
    "log-remote-address": True,
    "log-x-real-ip": True,
    "log-forwarded-for": True,
}

SITE = ("server/sws.toml", cb.SERVER_CONFIG, frozenset({"headers", "redirects"}))


class Settings(unittest.TestCase):
    config = SITE

    def setUp(self):
        self.name, path, self.advanced = self.config
        self.parsed = tomllib.loads(path.read_text(encoding="utf-8"))

    def problems(self, parsed=None) -> list:
        return cb.server_settings_problems(self.name, parsed or self.parsed, self.advanced)

    def changed(self, key, value=None, remove=False) -> list:
        parsed = copy.deepcopy(self.parsed)
        if remove:
            del parsed["general"][key]
        else:
            parsed["general"][key] = value
        return self.problems(parsed)

    def assertNames(self, problems, needle):
        self.assertTrue(any(needle in p for p in problems), f"no problem mentioning {needle!r}: {problems}")

    def test_the_real_config_passes(self):
        self.assertEqual(self.problems(), [])

    def test_every_feature_on_fails(self):
        self.assertEqual(set(ON), set(cb.SERVER_FEATURES_OFF), "ON must cover every feature the gate turns off")
        for key, value in ON.items():
            with self.subTest(key=key):
                self.assertNames(self.changed(key, value), f"{key} is {value!r}")

    def test_every_feature_left_out_fails(self):
        for key in cb.SERVER_FEATURES_OFF:
            with self.subTest(key=key):
                self.assertNames(self.changed(key, remove=True), f"{key} must be set")

    def test_off_written_with_the_wrong_type_fails(self):
        # 0 == False in Python, and SWS would reject "false" for a bool, but neither is the
        # value the gate means, so neither passes.
        self.assertNames(self.changed("health", 0), "health is 0")
        self.assertNames(self.changed("cors-allow-origins", []), "cors-allow-origins is []")

    def test_a_key_is_either_off_or_allowed(self):
        # A key in both lists reads as free to set, though the gate holds it off.
        self.assertEqual(set(cb.SERVER_FEATURES_OFF) & cb.SERVER_KEYS_ALLOWED, set())

    def test_unknown_key_fails(self):
        self.assertNames(self.changed("rewrites", True), "rewrites is not a key this gate knows")

    def test_misspelled_key_fails(self):
        # SWS would warn about helth and keep health at its default.
        parsed = copy.deepcopy(self.parsed)
        parsed["general"]["helth"] = parsed["general"].pop("health")
        problems = self.problems(parsed)
        self.assertNames(problems, "helth is not a key this gate knows")
        self.assertNames(problems, "health must be set")

    def test_v2_key_fails(self):
        self.assertNames(self.changed("ignore-hidden-files", True), "ignore-hidden-files is not a key")

    def test_unknown_section_fails(self):
        parsed = copy.deepcopy(self.parsed)
        parsed["extra"] = {}
        self.assertNames(self.problems(parsed), "[extra] is not a section")

    def test_advanced_features_fail(self):
        for table in ("rewrites", "virtual-hosts", "memory-cache"):
            with self.subTest(table=table):
                parsed = copy.deepcopy(self.parsed)
                parsed["advanced"][table] = [{"source": "/a", "destination": "/b"}]
                self.assertNames(self.problems(parsed), f"[advanced.{table}]")

    def test_idle_timeout_zero_fails(self):
        self.assertNames(self.changed("idle-timeout", 0), "idle-timeout must be set")

    def test_idle_timeout_negative_fails(self):
        self.assertNames(self.changed("idle-timeout", -1), "idle-timeout must be set")

    def test_idle_timeout_left_out_fails(self):
        self.assertNames(self.changed("idle-timeout", remove=True), "idle-timeout must be set")

    def test_idle_timeout_not_a_number_fails(self):
        self.assertNames(self.changed("idle-timeout", True), "idle-timeout must be set")
        self.assertNames(self.changed("idle-timeout", "30"), "idle-timeout must be set")

    def test_idle_timeout_of_one_passes(self):
        self.assertEqual(self.changed("idle-timeout", 1), [])


class Wired(unittest.TestCase):
    """The gate runs the rule on the file it is given, not on parsed data."""

    def edited(self, real: Path, old: str, new: str, run) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "server"
            server.mkdir()
            config = server / real.name
            text = real.read_text(encoding="utf-8")
            self.assertIn(old, text)
            config.write_text(text.replace(old, new, 1), encoding="utf-8")
            failures = []
            run(failures, config)
            return failures

    def test_check_build_reads_the_site_config(self):
        failures = self.edited(cb.SERVER_CONFIG, "health = false", "health = true", cb.check_server_settings)
        self.assertTrue(any("health is True" in f for f in failures), failures)

    def test_check_build_passes_the_real_site_config(self):
        failures = []
        cb.check_server_settings(failures)
        self.assertEqual(failures, [])

    def test_check_build_refuses_a_file_it_cannot_read(self):
        failures = []
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "server").mkdir()
            cb.check_server_settings(failures, Path(tmp) / "server" / "missing.toml")
        self.assertTrue(any("could not be read" in f for f in failures), failures)

    def test_the_site_gate_runs_the_rule(self):
        # check_build.main is what CI runs, and it needs a built site, so read its code: it
        # must call the rule on the failures it reports.
        tree = ast.parse(Path(cb.__file__).read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "check_server_settings"]
        self.assertTrue(any([getattr(a, "id", None) for a in c.args] == ["failures"] for c in calls), "main does not run it")


if __name__ == "__main__":
    unittest.main()
