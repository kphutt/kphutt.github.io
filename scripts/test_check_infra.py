"""Tests for the production DNS guard in check_infra.py (no tofu needed)."""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_infra as c  # noqa: E402

DNS = (c.INFRA / "dns.tf").read_text(encoding="utf-8")


def guard(dns_text: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as d:
        for f in c.INFRA.glob("*.tf"):
            shutil.copy(f, d)
        (Path(d) / "dns.tf").write_text(dns_text, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = c.check_cloudflare(Path(d))
        return ok, out.getvalue()


def with_name(old_line_start: str, new_name: str) -> str:
    """Rename the record on the list entry that starts with old_line_start."""
    lines = DNS.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(old_line_start):
            head, rest = line.split('name = "', 1)
            lines[i] = head + 'name = "' + new_name + rest[rest.index('"'):]
            return "\n".join(lines) + "\n"
    raise AssertionError(old_line_start)


class IdentityRecordGuard(unittest.TestCase):
    def test_committed_config_passes(self):
        self.assertTrue(guard(DNS)[0])

    def test_handle_name_variants_are_refused_in_the_mail_list(self):
        for name in ("_atproto.kphutt.com", "_ATPROTO.kphutt.com", "_atproto.kphutt.com.",
                     "_AtProto.Kphutt.COM."):
            with self.subTest(name=name):
                ok, out = guard(with_name('"txt-dmarc"', name))
                self.assertFalse(ok)
                self.assertIn("txt-dmarc", out)

    def test_handle_name_variants_are_refused_in_the_services_list(self):
        for name in ("_ATPROTO.kphutt.com", "_atproto.kphutt.com."):
            with self.subTest(name=name):
                self.assertFalse(guard(with_name('"lodestar"', name))[0])

    def test_identity_name_must_be_the_exact_spelling(self):
        for name in ("_ATPROTO.kphutt.com", "_atproto.kphutt.com.", "_atproto.KPHUTT.com"):
            with self.subTest(name=name):
                ok, out = guard(with_name('"atproto"', name))
                self.assertFalse(ok)
                self.assertIn("identity_records", out)

    def test_wildcard_is_refused_in_mail(self):
        self.assertFalse(guard(with_name('"txt-spf"', "*.kphutt.com"))[0])

    def test_non_canonical_spellings_stay_refused_in_mail_and_services(self):
        # Refused by the zone check or the services pattern on the raw name, as before.
        for line, name in (('"txt-spf"', "kphutt.com."), ('"txt-spf"', "next.kphutt.com."),
                           ('"txt-spf"', "_dmarc.kphutt.com."), ('"lodestar"', "Lodestar.kphutt.com"),
                           ('"lodestar"', "lodestar.kphutt.com."), ('"lodestar"', "lodestar.KPHUTT.com")):
            with self.subTest(line=line, name=name):
                self.assertFalse(guard(with_name(line, name))[0])

    def test_handle_and_wildcard_have_their_own_message(self):
        for line in ('"txt-spf"', '"lodestar"'):
            ok, out = guard(with_name(line, "_ATPROTO.kphutt.com"))
            self.assertFalse(ok)
            self.assertIn("lives only in the identity list", out)
            ok, out = guard(with_name(line, "*.kphutt.com"))
            self.assertIn("no wildcard names", out)

    def test_changed_did_is_refused(self):
        self.assertFalse(guard(DNS.replace("25m67qcipzldd2mitkbaqth4", "other"))[0])


if __name__ == "__main__":
    unittest.main()
