"""Tests for scripts/apply_infra.py: the plan policy, the secret read, and the apply flow.

No OpenTofu, 1Password or network: commands are replaced with fakes that record what would
have run. The plans in testdata/apply_infra/ have the shape of `tofu show -json` output, with
the addresses this repository's real plans had.

    python -m unittest discover -s scripts -p "test_*.py"
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_infra as ai  # noqa: E402

DATA = Path(__file__).resolve().parent / "testdata" / "apply_infra"
REPO = Path(__file__).resolve().parent.parent


def plan(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def kinds(name: str) -> list[tuple[str, str]]:
    return [(c.address, c.kind) for c in ai.changes(plan(name))]


class KindOf(unittest.TestCase):
    def test_names(self):
        self.assertIsNone(ai.kind_of(["no-op"]))
        self.assertIsNone(ai.kind_of(["read"]))
        for a in ("create", "update", "delete", "forget"):
            self.assertEqual(ai.kind_of([a]), a)
        self.assertEqual(ai.kind_of(["delete", "create"]), "replace")
        self.assertEqual(ai.kind_of(["create", "delete"]), "replace")
        self.assertEqual(ai.kind_of(["delete", "forget"]), "delete+forget")
        self.assertEqual(ai.kind_of([]), "empty")


class Changes(unittest.TestCase):
    def test_skips_no_op_and_read(self):
        self.assertEqual(kinds("update_service.json"), [("google_cloud_run_v2_service.site", "update")])

    def test_import_with_no_change_is_not_a_change(self):
        self.assertEqual(kinds("no_changes.json"), [])

    def test_keeps_plan_order(self):
        self.assertEqual([k for _, k in kinds("replace_source.json")],
                         ["create", "create", "create", "replace", "update"])


class Covers(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(ai.covers("a.b[0]", "a.b[0]"))
        self.assertFalse(ai.covers("a.b[0]", "a.b[1]"))

    def test_resource_names_every_instance(self):
        self.assertTrue(ai.covers("a.b", 'a.b["x"]'))
        self.assertTrue(ai.covers("a.b", "a.b[0]"))
        self.assertTrue(ai.covers("a.b", "a.b"))

    def test_no_prefix_matching(self):
        self.assertFalse(ai.covers("a.b", "a.bc"))
        self.assertFalse(ai.covers("a.b", "a.bc[0]"))
        self.assertFalse(ai.covers("a", "a.b"))
        self.assertFalse(ai.covers("a.b[0]", "a.b[0][1]"))


class Check(unittest.TestCase):
    def test_create_and_update_pass(self):
        self.assertEqual(ai.check(ai.changes(plan("update_service.json")), {}), [])

    def test_empty_plan_passes(self):
        self.assertEqual(ai.check([], {}), [])

    def test_replace_refused_without_allowance(self):
        problems = ai.check(ai.changes(plan("replace_source.json")), {})
        self.assertEqual(len(problems), 1)
        self.assertIn("google_storage_bucket_object.cost_cutoff_source: replace not allowed", problems[0])
        self.assertIn("--allow-replace google_storage_bucket_object.cost_cutoff_source", problems[0])

    def test_replace_allowed_by_name(self):
        found = ai.changes(plan("replace_source.json"))
        self.assertEqual(ai.check(found, {"replace": ["google_storage_bucket_object.cost_cutoff_source"]}), [])

    def test_delete_allowance_does_not_allow_replace(self):
        found = ai.changes(plan("replace_source.json"))
        problems = ai.check(found, {"delete": ["google_storage_bucket_object.cost_cutoff_source"]})
        self.assertEqual(len(problems), 2)  # the replace is refused, and the allowance is unused
        self.assertTrue(any("replace not allowed" in p for p in problems))
        self.assertTrue(any(p.startswith("--allow-delete google_storage_bucket_object") for p in problems))

    def test_test_domain_off_needs_every_delete_named(self):
        found = ai.changes(plan("test_domain_off.json"))
        self.assertEqual(len(ai.check(found, {})), 5)
        allow = {"delete": ["cloudflare_dns_record.verification", "google_site_verification_web_resource.test",
                            "google_cloud_run_domain_mapping.test", "cloudflare_dns_record.test"]}
        self.assertEqual(ai.check(found, allow), [])
        allow["delete"].remove("cloudflare_dns_record.test")
        problems = ai.check(found, allow)
        self.assertEqual(sorted(problems), sorted([
            'cloudflare_dns_record.test["a0"]: delete not allowed (pass --allow-delete cloudflare_dns_record.test["a0"])',
            'cloudflare_dns_record.test["aaaa0"]: delete not allowed (pass --allow-delete cloudflare_dns_record.test["aaaa0"])',
        ]))

    def test_unused_allowance_refuses(self):
        problems = ai.check(ai.changes(plan("update_service.json")), {"delete": ["cloudflare_dns_record.web"]})
        self.assertEqual(problems, ["--allow-delete cloudflare_dns_record.web: matches no delete in this plan"])

    def test_unknown_action_refused_even_if_named(self):
        found = ai.changes(plan("odd_actions.json"))
        allow = {"replace": ["google_cloud_run_v2_service.site"],
                 "forget": ["google_site_verification_web_resource.test"],
                 "delete": []}
        problems = ai.check(found, allow)
        self.assertEqual(problems, ["cloudflare_dns_record.web[1]: unknown action 'delete+forget', "
                                    "never applied by this script"])

    def test_forget_needs_its_own_allowance(self):
        found = [ai.Change("x.y[0]", "forget")]
        self.assertEqual(len(ai.check(found, {"delete": ["x.y"]})), 2)
        self.assertEqual(ai.check(found, {"forget": ["x.y"]}), [])


class Summary(unittest.TestCase):
    def test_lines(self):
        text = ai.summary(ai.changes(plan("replace_source.json")))
        self.assertIn("   -/+ google_storage_bucket_object.cost_cutoff_source", text)
        self.assertIn("     + google_cloud_run_v2_service.explorables", text)
        self.assertIn("     ~ google_cloudfunctions2_function.cost_cutoff", text)
        self.assertEqual(ai.summary([]), "  no changes")
        self.assertIn("     - a.b", ai.summary([ai.Change("a.b", "delete")]))


class Config(unittest.TestCase):
    def write(self, text: str) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / "infra-apply.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_requires_backend(self):
        with self.assertRaises(SystemExit):
            ai.load_config(self.write('infra_dir = "infra"\n'))
        with self.assertRaises(SystemExit):
            ai.load_config(self.write('infra_dir = "infra"\n[backend]\nbucket = "b"\n'))
        with self.assertRaises(SystemExit):
            ai.load_config(self.write('[backend]\nbucket = "b"\nprefix = "p"\n'))

    def test_secrets_must_be_references(self):
        base = 'infra_dir = "i"\n[backend]\nbucket = "b"\nprefix = "p"\n'
        one = '[[secret]]\nenv = "{}"\nref = "{}"\n'
        for bad in (one.format("TOK", "plain-value"), one.format("BAD NAME", "op://v/i/f"),
                    one.format("", "op://v/i/f"), one.format("TOK", "op://a/b/c") * 2):
            with self.assertRaises(SystemExit):
                ai.load_config(self.write(base + bad))
        cfg = ai.load_config(self.write(base + one.format("TOK", "op://v/i/f") + one.format("B", "op://v/j/f")))
        self.assertEqual(cfg["secrets"], {"TOK": "op://v/i/f", "B": "op://v/j/f"})
        self.assertNotIn("secret", cfg)
        self.assertEqual(ai.load_config(self.write(base))["secrets"], {})

    def test_missing_file(self):
        with self.assertRaises(SystemExit):
            ai.load_config(Path(tempfile.mkdtemp()) / "nope.toml")

    def test_tfvars_strings(self):
        text = 'project_id = "p-1"\n  region="us-west1" # c\nflag = false\n# x = "y"\nlist = ["a"]\n'
        self.assertEqual(ai.tfvars_strings(text), {"project_id": "p-1", "region": "us-west1"})


class CommittedSettings(unittest.TestCase):
    """The committed config is what a fresh clone plans with, so pin what it must hold."""

    def setUp(self):
        self.cfg = ai.load_config(REPO / "infra-apply.toml")

    def test_var_files_exist_and_hold_nothing_personal(self):
        for f in self.cfg["var_files"]:
            text = (REPO / self.cfg["infra_dir"] / f).read_text(encoding="utf-8")
            self.assertNotIn("@", text, f"{f} must not hold an email address")
            for key in ("alert_email", "billing_account"):
                self.assertNotIn(key, ai.tfvars_strings(text), f"{key} belongs in a [[secret]], not {f}")

    def test_personal_values_come_from_1password(self):
        for name in ("CLOUDFLARE_API_TOKEN", "TF_VAR_alert_email", "TF_VAR_billing_account"):
            self.assertIn(name, self.cfg["secrets"])

    def test_test_domain_stays_off(self):
        text = (REPO / "infra" / "site.tfvars").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^test_domain_enabled\s*=\s*false\s*$")

    def test_steps_and_smoke_scripts_exist(self):
        self.assertTrue((REPO / self.cfg["steps"]["seed-vars"][1]).is_file())
        for script in self.cfg["live"]["smoke"].values():
            self.assertTrue((REPO / script).is_file(), script)
        self.assertTrue((REPO / ".github" / "workflows" / self.cfg["deploy_workflow"]).is_file())


class Secrets(unittest.TestCase):
    REFS = {"A_TOKEN": "op://v/a/credential", "TF_VAR_mail": "op://v/b/mail"}

    def test_missing_only(self):
        self.assertEqual(ai.missing_secrets(self.REFS, {"A_TOKEN": "set"}), {"TF_VAR_mail": "op://v/b/mail"})
        self.assertEqual(ai.missing_secrets(self.REFS, {"A_TOKEN": ""}), self.REFS)
        self.assertEqual(ai.missing_secrets(self.REFS, {"A_TOKEN": "x", "TF_VAR_mail": "y"}), {})

    def test_nothing_to_read_calls_nothing(self):
        with mock.patch.object(ai.subprocess, "run") as run:
            self.assertEqual(ai.read_secrets({}), {})
        run.assert_not_called()

    def test_one_op_call_for_every_reference(self):
        out = json.dumps({"A_TOKEN": "s3cret", "TF_VAR_mail": "m"})
        done = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        buf = io.StringIO()
        with mock.patch.object(ai.shutil, "which", return_value="op"), \
                mock.patch.object(ai.subprocess, "run", return_value=done) as run, redirect_stdout(buf):
            self.assertEqual(ai.read_secrets(self.REFS), {"A_TOKEN": "s3cret", "TF_VAR_mail": "m"})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["op", "inject"])
        self.assertEqual(json.loads(run.call_args.kwargs["input"]),
                         {"A_TOKEN": "{{ op://v/a/credential }}", "TF_VAR_mail": "{{ op://v/b/mail }}"})
        self.assertNotIn("s3cret", buf.getvalue())

    def test_bad_output_is_not_echoed(self):
        done = subprocess.CompletedProcess([], 0, stdout="s3cret not json", stderr="")
        with mock.patch.object(ai.shutil, "which", return_value="op"), \
                mock.patch.object(ai.subprocess, "run", return_value=done):
            with self.assertRaises(SystemExit) as e:
                ai.read_secrets(self.REFS)
        self.assertNotIn("s3cret", str(e.exception.code))

    def test_empty_value_refused(self):
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps({"A_TOKEN": "x", "TF_VAR_mail": ""}), stderr="")
        with mock.patch.object(ai.shutil, "which", return_value="op"), \
                mock.patch.object(ai.subprocess, "run", return_value=done):
            with self.assertRaises(SystemExit) as e:
                ai.read_secrets(self.REFS)
        self.assertIn("TF_VAR_mail", str(e.exception.code))

    def test_op_failure_and_absence(self):
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="not signed in")
        with mock.patch.object(ai.shutil, "which", return_value="op"), \
                mock.patch.object(ai.subprocess, "run", return_value=done):
            with self.assertRaises(SystemExit) as e:
                ai.read_secrets(self.REFS)
        self.assertIn("not signed in", str(e.exception.code))
        with mock.patch.object(ai.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit):
                ai.read_secrets(self.REFS)


class OutputsChanged(unittest.TestCase):
    def test_reads_output_changes(self):
        self.assertTrue(ai.outputs_changed(plan("output_only.json")))
        self.assertFalse(ai.outputs_changed(plan("no_changes.json")))
        self.assertFalse(ai.outputs_changed({"output_changes": {"a": {"actions": ["no-op"]}}}))
        self.assertTrue(ai.outputs_changed({"output_changes": {"a": {"actions": ["no-op"]},
                                                               "b": {"actions": ["create"]}}}))
        self.assertFalse(ai.outputs_changed({}))


class DomainReady(unittest.TestCase):
    def test_ready(self):
        m = {"status": {"conditions": [{"type": "Ready", "status": "True"},
                                       {"type": "CertificateProvisioned", "status": "True"}]}}
        self.assertEqual(ai.domain_ready(m), (True, "Ready"))

    def test_waiting_says_why(self):
        m = {"status": {"conditions": [{"type": "Ready", "status": "Unknown"},
                                       {"type": "CertificateProvisioned", "status": "Unknown",
                                        "message": "Waiting for certificate"},
                                       {"type": "DomainRoutable", "status": "True"}]}}
        ok, why = ai.domain_ready(m)
        self.assertFalse(ok)
        self.assertIn("CertificateProvisioned: Waiting for certificate", why)
        self.assertNotIn("DomainRoutable", why)

    def test_no_status(self):
        self.assertEqual(ai.domain_ready({}), (False, "no status yet"))


class Flow(unittest.TestCase):
    """main() end to end, with every command faked. What matters: what reaches `tofu apply`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "infra").mkdir()
        (self.root / "infra" / "site.tfvars").write_text('project_id = "p"\n', encoding="utf-8")
        (self.root / "infra-apply.toml").write_text(
            'infra_dir = "infra"\nvar_files = ["site.tfvars"]\ndeploy_workflow = "d.yml"\n'
            '[backend]\nbucket = "bkt"\nprefix = "pre"\n'
            '[[secret]]\nenv = "TOK"\nref = "op://v/i/f"\n[steps]\nafter = ["python", "x.py"]\n', encoding="utf-8")
        self.calls: list[tuple[list[str], dict]] = []

    def go(self, fixture: str, *args: str, replan: int = 0, env: dict | None = None) -> tuple[int, str]:
        shown = (DATA / fixture).read_text(encoding="utf-8")

        def fake_run(cmd, cwd, env, capture=False, ok=(0,)):
            self.calls.append((cmd, env))
            if "show" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=shown, stderr="")
            if "-detailed-exitcode" in cmd:
                return subprocess.CompletedProcess(cmd, replan, stdout="Plan: 0 to add", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        buf = io.StringIO()
        with mock.patch.object(ai, "run", side_effect=fake_run), \
                mock.patch.object(ai, "mise", return_value="mise"), \
                mock.patch.object(ai.shutil, "which", return_value="gh"), \
                mock.patch.object(ai, "read_secrets", return_value={"TOK": "s3cret"}) as rs, \
                mock.patch.dict(os.environ, env or {}, clear=False), redirect_stdout(buf):
            if not env:
                os.environ.pop("TOK", None)
            code = ai.main(["--config", str(self.root / "infra-apply.toml"), *args])
        self.read_secrets = rs
        return code, buf.getvalue()

    def tofu(self, verb: str) -> list[tuple[list[str], dict]]:
        return [(c, e) for c, e in self.calls if c[:4] == ["mise", "exec", "--", "tofu"] and c[4] == verb]

    def test_allowed_plan_is_applied_from_the_saved_file(self):
        code, out = self.go("update_service.json")
        self.assertEqual(code, 0)
        init = self.tofu("init")[0][0]
        self.assertIn("-backend-config=bucket=bkt", init)
        self.assertIn("-backend-config=prefix=pre", init)
        plan_cmd = self.tofu("plan")[0][0]
        self.assertIn("-var-file=site.tfvars", plan_cmd)
        out_arg = [a for a in plan_cmd if a.startswith("-out=")][0].removeprefix("-out=")
        applies = self.tofu("apply")
        self.assertEqual(len(applies), 1)
        self.assertEqual(applies[0][0][-1], out_arg)
        self.assertIn("-var-file=site.tfvars", self.tofu("plan")[1][0])
        self.assertIn("Re-plan: clean", out)
        self.assertFalse(Path(out_arg).exists(), "the plan file must not outlive the run")

    def test_secret_reaches_tofu_and_nothing_else(self):
        self.go("update_service.json", "--run", "after", "--deploy")
        for cmd, env in self.calls:
            is_tofu = cmd[:4] == ["mise", "exec", "--", "tofu"]
            self.assertEqual(env.get("TOK") == "s3cret", is_tofu, cmd)
        self.assertIn(["mise", "exec", "--", "python", "x.py"], [c for c, _ in self.calls])
        self.assertIn(["gh", "workflow", "run", "d.yml", "--ref", "main"], [c for c, _ in self.calls])

    def test_refused_plan_applies_nothing(self):
        code, out = self.go("replace_source.json", "--run", "after", "--deploy")
        self.assertEqual(code, 3)
        self.assertEqual(self.tofu("apply"), [])
        self.assertEqual(len(self.tofu("plan")), 1)  # no re-plan, no steps, no deploy
        self.assertEqual(len(self.calls), 3)
        self.assertIn("REFUSED", out)

    def test_allowance_from_the_command_line(self):
        code, _ = self.go("replace_source.json", "--allow-replace", "google_storage_bucket_object.cost_cutoff_source")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tofu("apply")), 1)

    def test_plan_only_applies_nothing(self):
        code, out = self.go("update_service.json", "--plan-only")
        self.assertEqual(code, 0)
        self.assertEqual(self.tofu("apply"), [])
        self.assertIn("Plan only", out)

    def test_empty_plan_skips_apply_but_runs_steps(self):
        code, out = self.go("no_changes.json", "--run", "after")
        self.assertEqual(code, 0)
        self.assertEqual(self.tofu("apply"), [])
        self.assertIn("Nothing to apply", out)
        self.assertIn(["mise", "exec", "--", "python", "x.py"], [c for c, _ in self.calls])

    def test_output_only_plan_is_applied(self):
        code, out = self.go("output_only.json", "--run", "after")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tofu("apply")), 1)
        self.assertNotIn("Nothing to apply", out)
        self.assertIn("output values change", out)
        names = [c for c, _ in self.calls]
        self.assertLess(names.index(self.tofu("apply")[0][0]),
                        names.index(["mise", "exec", "--", "python", "x.py"]),
                        "seed-vars must run after the outputs are saved")

    def test_dirty_replan_exits_4(self):
        code, out = self.go("update_service.json", replan=2)
        self.assertEqual(code, 4)
        self.assertIn("NOT clean", out)

    def test_secret_already_in_env_is_not_read(self):
        self.go("update_service.json", env={"TOK": "from-env"})
        self.read_secrets.assert_called_once_with({})

    def test_stray_tfvars_warns(self):
        (self.root / "infra" / "terraform.tfvars").write_text("x = 1\n", encoding="utf-8")
        _, out = self.go("update_service.json")
        self.assertIn("WARNING: terraform.tfvars in infra/", out)
        self.assertIn("overrides the TF_VAR_* values", out)

    def test_auto_loaded_matches_what_opentofu_loads(self):
        infra = self.root / "infra"
        for name in ("terraform.tfvars", "terraform.tfvars.json", "x.auto.tfvars",
                     "y.auto.tfvars.json", "old.tfvars", "site.tfvars", "notes.txt"):
            (infra / name).write_text("", encoding="utf-8")
        (infra / "dir.auto.tfvars").mkdir()
        self.assertEqual(ai.auto_loaded(infra, ["site.tfvars"]),
                         ["terraform.tfvars", "terraform.tfvars.json", "x.auto.tfvars", "y.auto.tfvars.json"])
        self.assertEqual(ai.auto_loaded(infra, ["site.tfvars", "terraform.tfvars"]),
                         ["terraform.tfvars.json", "x.auto.tfvars", "y.auto.tfvars.json"])

    def test_missing_var_file_stops(self):
        (self.root / "infra" / "site.tfvars").unlink()
        with self.assertRaises(SystemExit):
            self.go("update_service.json")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
