"""Unit tests for the cost cutoff function. Standard library only, no network.

    python -m unittest discover -s infra/cost-cutoff

functions_framework and google.auth are not needed: the decorator is stubbed below, and the
Cloud Run API is a fake HTTP session.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

# Stub the framework before importing main: its decorator only registers the function.
sys.modules.setdefault("functions_framework", types.SimpleNamespace(cloud_event=lambda f: f))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402

SERVICE = "projects/p/locations/us-west1/services/site"
URL = main.RUN_API + SERVICE
EXPLORABLES = "projects/p/locations/us-west1/services/explorables"
EXPLORABLES_URL = main.RUN_API + EXPLORABLES


class Response:
    def __init__(self, body=None, status=200):
        self.body, self.status = body or {}, status

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeRun:
    """Records calls; answers GET with the service's current ingress.

    ingress is one value for every service, or a dict of URL to value. fail_patch names URLs
    whose PATCH fails.
    """

    def __init__(self, ingress="INGRESS_TRAFFIC_ALL", get_status=200, patch_status=200, fail_patch=()):
        self.ingress, self.get_status, self.patch_status = ingress, get_status, patch_status
        self.fail_patch = set(fail_patch)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        ingress = self.ingress.get(url, "INGRESS_TRAFFIC_ALL") if isinstance(self.ingress, dict) else self.ingress
        return Response({"ingress": ingress}, self.get_status)

    def patch(self, url, **kw):
        self.calls.append(("PATCH", url, kw))
        return Response({"name": "operations/1"}, 500 if url in self.fail_patch else self.patch_status)

    def patched(self):
        return [url for verb, url, _ in self.calls if verb == "PATCH"]


def event(notification) -> types.SimpleNamespace:
    data = base64.b64encode(json.dumps(notification).encode()).decode()
    return types.SimpleNamespace(data={"message": {"data": data}})


def run(notification, run_api=None, cutoff=10.0, services=(SERVICE,)):
    run_api = run_api or FakeRun()
    with mock.patch("builtins.print"):
        result = main.handle_notification(notification, list(services), cutoff, lambda: run_api)
    return result, run_api


class TriggerLogic(unittest.TestCase):
    def test_under_cutoff_does_nothing(self):
        result, api = run({"costAmount": 9.99, "budgetAmount": 10})
        self.assertEqual(result, "under cutoff")
        self.assertEqual(api.calls, [])

    def test_exactly_at_cutoff_turns_off(self):
        result, api = run({"costAmount": 10.0, "budgetAmount": 10})
        self.assertEqual(result, "turned off")

    def test_over_cutoff_patches_only_ingress(self):
        result, api = run({"costAmount": 12.5, "budgetAmount": 10})
        self.assertEqual(result, "turned off")
        self.assertEqual([c[0] for c in api.calls], ["GET", "PATCH"])
        _, url, kw = api.calls[1]
        self.assertEqual(url, URL)
        self.assertEqual(kw["params"], {"updateMask": "ingress"})
        self.assertEqual(kw["json"], {"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY"})

    def test_forecast_alone_never_trips(self):
        # Forecast far over, alert threshold present, actual cost under the cutoff.
        result, api = run({"costAmount": 2.0, "budgetAmount": 10, "forecastThresholdExceeded": 5.0,
                           "alertThresholdExceeded": 1.0})
        self.assertEqual(result, "under cutoff")
        self.assertEqual(api.calls, [])

    def test_cutoff_comes_from_config_not_the_message(self):
        # A message whose budgetAmount is lower than the configured cutoff does not trip it.
        result, _ = run({"costAmount": 5.0, "budgetAmount": 1}, cutoff=10.0)
        self.assertEqual(result, "under cutoff")

    def test_missing_or_bad_cost_does_nothing(self):
        for bad in ({}, {"costAmount": None}, {"costAmount": "12"}, {"costAmount": True},
                    {"costAmount": float("nan")}, {"costAmount": float("inf")}):
            with self.subTest(bad=bad):
                # A cutoff of 1, so a bool (True == 1) would trip it if it counted as a number.
                result, api = run(bad, cutoff=1.0)
                self.assertEqual(result, "under cutoff")
                self.assertEqual(api.calls, [])

    def test_not_a_budget_message_is_ignored(self):
        result, api = run(None)
        self.assertEqual(result, "ignored")
        self.assertEqual(api.calls, [])


class Idempotency(unittest.TestCase):
    def test_already_off_is_left_alone(self):
        result, api = run({"costAmount": 50}, FakeRun(ingress="INGRESS_TRAFFIC_INTERNAL_ONLY"))
        self.assertEqual(result, "already off")
        self.assertEqual([c[0] for c in api.calls], ["GET"])

    def test_repeated_messages_patch_once(self):
        api = FakeRun()
        run({"costAmount": 50}, api)
        api.ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"  # what the PATCH did
        run({"costAmount": 51}, api)
        self.assertEqual([c[0] for c in api.calls], ["GET", "PATCH", "GET"])


class SeveralServices(unittest.TestCase):
    BOTH = (SERVICE, EXPLORABLES)

    def test_over_cutoff_turns_every_service_off(self):
        result, api = run({"costAmount": 12}, services=self.BOTH)
        self.assertEqual(result, "turned off")
        self.assertEqual(api.patched(), [URL, EXPLORABLES_URL])

    def test_under_cutoff_touches_none(self):
        result, api = run({"costAmount": 1}, services=self.BOTH)
        self.assertEqual(result, "under cutoff")
        self.assertEqual(api.calls, [])

    def test_one_already_off_still_turns_the_other_off(self):
        api = FakeRun(ingress={URL: "INGRESS_TRAFFIC_INTERNAL_ONLY"})
        result, _ = run({"costAmount": 12}, api, services=self.BOTH)
        self.assertEqual(result, "turned off")
        self.assertEqual(api.patched(), [EXPLORABLES_URL])

    def test_last_already_off_still_reports_the_trip(self):
        api = FakeRun(ingress={EXPLORABLES_URL: "INGRESS_TRAFFIC_INTERNAL_ONLY"})
        result, _ = run({"costAmount": 12}, api, services=self.BOTH)
        self.assertEqual(result, "turned off")
        self.assertEqual(api.patched(), [URL])

    def test_all_already_off(self):
        result, api = run({"costAmount": 12}, FakeRun(ingress="INGRESS_TRAFFIC_INTERNAL_ONLY"), services=self.BOTH)
        self.assertEqual(result, "already off")
        self.assertEqual(api.patched(), [])

    def test_a_failure_raises_after_the_earlier_ones_are_off(self):
        api = FakeRun(fail_patch={EXPLORABLES_URL})
        with self.assertRaises(RuntimeError):
            run({"costAmount": 12}, api, services=self.BOTH)
        self.assertEqual(api.patched(), [URL, EXPLORABLES_URL])

    def test_services_are_read_from_the_environment(self):
        with mock.patch.dict("os.environ", {"SERVICES": f"{SERVICE}, {EXPLORABLES}"}):
            self.assertEqual(main.services_from_env(), [SERVICE, EXPLORABLES])


class Errors(unittest.TestCase):
    def test_read_error_raises_so_pubsub_retries(self):
        with self.assertRaises(RuntimeError):
            run({"costAmount": 50}, FakeRun(get_status=403))

    def test_patch_error_raises_so_pubsub_retries(self):
        with self.assertRaises(RuntimeError):
            run({"costAmount": 50}, FakeRun(patch_status=500))

    def test_bad_cutoff_config_raises(self):
        for raw in ("", "abc", "0", "-1", "nan", "inf"):
            with self.subTest(raw=raw), mock.patch.dict("os.environ", {"CUTOFF_USD": raw}):
                with self.assertRaises(RuntimeError):
                    main.cutoff_from_env()

    def test_good_cutoff_config(self):
        with mock.patch.dict("os.environ", {"CUTOFF_USD": "10"}):
            self.assertEqual(main.cutoff_from_env(), 10.0)

    def test_bad_service_config_raises(self):
        for raw in ("", "site", f"{SERVICE},explorables", " , "):
            with self.subTest(raw=raw), mock.patch.dict("os.environ", {"SERVICES": raw, "CUTOFF_USD": "10"}):
                with self.assertRaises(RuntimeError):
                    main.handle(event({"costAmount": 1}))


class Messages(unittest.TestCase):
    def test_reads_a_pubsub_cloud_event(self):
        self.assertEqual(main.read_notification(event({"costAmount": 3.5})), {"costAmount": 3.5})

    def test_garbage_is_not_a_notification(self):
        for data in ({}, {"message": {}}, {"message": {"data": "!!!"}},
                     {"message": {"data": base64.b64encode(b"[1]").decode()}}):
            with self.subTest(data=data):
                self.assertIsNone(main.read_notification(types.SimpleNamespace(data=data)))

    def test_end_to_end_through_the_entry_point(self):
        api = FakeRun()
        env = {"SERVICES": SERVICE, "CUTOFF_USD": "10"}
        with mock.patch.dict("os.environ", env), mock.patch.object(main, "session", lambda: api), \
                mock.patch("builtins.print") as printed:
            main.handle(event({"costAmount": 11}))
        self.assertEqual([c[0] for c in api.calls], ["GET", "PATCH"])
        logged = json.loads(printed.call_args.args[0])
        self.assertEqual(logged["severity"], "ALERT")
        self.assertIn("COST CUTOFF TRIPPED", logged["message"])


class Wiring(unittest.TestCase):
    """The parts of the design that live outside this file."""

    INFRA = Path(__file__).resolve().parent.parent

    def test_tofu_apply_does_not_reopen_a_tripped_site(self):
        # Each service must leave ingress to this function, or the next apply undoes a trip.
        for name in ("cloud-run.tf", "explorables.tf"):
            with self.subTest(file=name):
                text = (self.INFRA / name).read_text(encoding="utf-8")
                ignored = re.search(r"ignore_changes\s*=\s*\[(.*?)\n\s*\]", text, re.S).group(1)
                self.assertRegex(ignored, r"(?m)^\s*ingress,")

    def test_function_env_matches_what_main_reads(self):
        text = (self.INFRA / "cost-cutoff.tf").read_text(encoding="utf-8")
        for name in ("SERVICES", "CUTOFF_USD"):
            self.assertRegex(text, rf"(?m)^\s*{name}\s*=")
        self.assertRegex(text, r'(?m)^\s*entry_point\s*=\s*"handle"\s*$')


if __name__ == "__main__":
    unittest.main()
