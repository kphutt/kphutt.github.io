"""Turn the site off when this month's actual spend reaches the cutoff.

A Cloud Billing budget (infra/cost-cutoff.tf) publishes a message to a Pub/Sub topic several
times a day. Each message triggers this function. If the month's actual cost (costAmount) is
at or above CUTOFF_USD, it sets the Cloud Run ingress of every service in SERVICES to
internal only: the site's, and the explorables service's. The internet
then gets an error page from Google's front end and never reaches the service, so a flood of
requests stops costing anything. Nothing is deleted: the service, its revisions and its
images stay as they were, and one command reopens it (infra/cost-cutoff.md).

Only actual cost counts. The forecast fields in the message are ignored, so a forecast that
spikes early in the month never takes the site down.

Safe to run any number of times: Pub/Sub delivers at least once and the budget keeps sending
messages, so a service that is already off is left alone and logged as such. If turning one
service off fails, the error is raised and Pub/Sub retries: the ones already off stay off,
and the rest are tried again.

Environment (set by infra/cost-cutoff.tf):
    SERVICES    comma-separated projects/<project>/locations/<region>/services/<name>
    CUTOFF_USD  the spend, in US dollars, at which they are turned off
"""

from __future__ import annotations

import base64
import json
import math
import os

import functions_framework

OFF = "INGRESS_TRAFFIC_INTERNAL_ONLY"
RUN_API = "https://run.googleapis.com/v2/"


def log(severity: str, message: str, **fields) -> None:
    """One structured line; Cloud Logging reads severity and message from it."""
    print(json.dumps({"severity": severity, "message": message, **fields}), flush=True)


def read_notification(cloud_event) -> dict | None:
    """The budget notification in a Pub/Sub CloudEvent, or None if it is not one."""
    try:
        data = cloud_event.data["message"]["data"]
        notification = json.loads(base64.b64decode(data))
    except (KeyError, TypeError, ValueError):
        return None
    return notification if isinstance(notification, dict) else None


def over_cutoff(notification: dict, cutoff: float) -> bool:
    """True only when the month's actual cost is at or above the cutoff.

    Uses costAmount alone. forecastThresholdExceeded and alertThresholdExceeded are not
    read: the first is a forecast, and the second depends on the budget's threshold list,
    not on the cutoff this function enforces.
    """
    cost = notification.get("costAmount")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost):
        return False
    return cost >= cutoff


def cutoff_from_env() -> float:
    raw = os.environ.get("CUTOFF_USD", "")
    try:
        cutoff = float(raw)
    except ValueError:
        cutoff = math.nan
    if not math.isfinite(cutoff) or cutoff <= 0:
        # A misconfiguration, not a message problem: fail loudly so it shows in the logs
        # and Pub/Sub retries once it is fixed.
        raise RuntimeError(f"CUTOFF_USD must be a positive number, got {raw!r}")
    return cutoff


def session():
    """An HTTP session authorised as the function's own service account."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials)


def turn_off(http, service: str) -> str:
    """Set the service's ingress to internal only. Returns what happened.

    Changes only the ingress field (updateMask), so no new revision is made and nothing
    else about the service moves. An API error raises, and Pub/Sub retries the message.
    """
    url = RUN_API + service
    current = http.get(url, timeout=30)
    current.raise_for_status()
    if current.json().get("ingress") == OFF:
        return "already off"
    patched = http.patch(url, params={"updateMask": "ingress"}, json={"ingress": OFF}, timeout=30)
    patched.raise_for_status()
    return "turned off"


def handle_notification(notification: dict | None, services: list[str], cutoff: float, http_factory) -> str:
    """Turn every service off once spend is over the cutoff.

    Returns "turned off" if this call turned any service off, "already off" if every one
    already was, or why nothing was done.
    """
    if notification is None:
        log("WARNING", "Ignored a message that is not a budget notification.")
        return "ignored"
    cost = notification.get("costAmount")
    if not over_cutoff(notification, cutoff):
        log("INFO", "Spend is under the cutoff; nothing to do.", cost=cost, cutoff=cutoff)
        return "under cutoff"
    http = http_factory()
    results = []
    for service in services:
        result = turn_off(http, service)
        results.append(result)
        if result == "already off":
            log("INFO", "Spend is over the cutoff and the service is already off.", cost=cost, cutoff=cutoff, service=service)
        else:
            log("ALERT", "COST CUTOFF TRIPPED: spend reached the cutoff, so the service's ingress is now internal only. "
                "See infra/cost-cutoff.md to reopen it.", cost=cost, cutoff=cutoff, service=service)
    return "turned off" if "turned off" in results else "already off"


def services_from_env() -> list[str]:
    raw = os.environ.get("SERVICES", "")
    services = [s.strip() for s in raw.split(",") if s.strip()]
    if not services or any(not s.startswith("projects/") or "/services/" not in s for s in services):
        raise RuntimeError(f"SERVICES must be comma-separated projects/.../locations/.../services/..., got {raw!r}")
    return services


@functions_framework.cloud_event
def handle(cloud_event) -> None:
    handle_notification(read_notification(cloud_event), services_from_env(), cutoff_from_env(), session)
