"""
LibreLinkUp MCP Server
=======================

An MCP (Model Context Protocol) server that exposes LibreLinkUp glucose data
(via the `pylibrelinkup` client) as MCP tools, so an MCP-compatible client
(Claude Desktop, Claude Code, etc.) can query a follower's current glucose
reading, recent history, and two-week logbook.

Authentication
--------------
Credentials are read from environment variables (never hard-code them):

  LIBRELINKUP_EMAIL     - your LibreLinkUp account email
  LIBRELINKUP_PASSWORD  - your LibreLinkUp account password
  LIBRELINKUP_REGION    - optional, one of the pylibrelinkup APIUrl names:
                           US (default), EU, EU2, DE, FR, JP, AP, AU, AE, CA,
                           LA, RU

The underlying pylibrelinkup client authenticates once and is cached for the
lifetime of the server process; if a call fails with an auth-looking error,
the server re-authenticates once and retries.

Tools exposed
-------------
  list_patients()                       -> patients you follow
  get_current_glucose(patient)          -> latest reading + trend
  get_glucose_graph(patient)            -> ~last 12 hours of readings
  get_glucose_logbook(patient)          -> ~last 2 weeks of logged events

`patient` may be a patient_id (UUID string) or a "First Last" name, resolved
against list_patients().

Run
---
  pip install mcp pylibrelinkup
  export LIBRELINKUP_EMAIL=you@example.com
  export LIBRELINKUP_PASSWORD=your_password
  python server.py
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer
from pylibrelinkup import APIUrl, PyLibreLinkUp
from pylibrelinkup.exceptions import (
    AuthenticationError,
    PatientNotFoundError,
    PyLibreLinkUpError,
)
from pylibrelinkup.models.data import GlucoseMeasurement, Patient

mcp = MCPServer("librelinkup")

_client_lock = threading.Lock()
_client: PyLibreLinkUp | None = None
_patient_cache: list[Patient] = []


# --------------------------------------------------------------------------
# Client bootstrap
# --------------------------------------------------------------------------

def _region() -> APIUrl:
    name = os.environ.get("LIBRELINKUP_REGION", "US").strip().upper()
    try:
        return APIUrl[name]
    except KeyError:
        valid = ", ".join(sorted(APIUrl.__members__))
        raise RuntimeError(
            f"Invalid LIBRELINKUP_REGION '{name}'. Valid options: {valid}"
        )


def _get_client(force_reauth: bool = False) -> PyLibreLinkUp:
    global _client
    with _client_lock:
        if _client is not None and not force_reauth:
            return _client

        email = os.environ.get("LIBRELINKUP_EMAIL")
        password = os.environ.get("LIBRELINKUP_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                "LIBRELINKUP_EMAIL and LIBRELINKUP_PASSWORD environment "
                "variables must be set before calling any LibreLinkUp tool."
            )

        client = PyLibreLinkUp(email=email, password=password, api_url=_region())
        client.authenticate()
        _client = client
        return _client


def _call_with_retry(fn_name: str, *args, **kwargs) -> Any:
    """Call a method on the cached client, re-authenticating once on auth errors."""
    client = _get_client()
    fn = getattr(client, fn_name)
    try:
        return fn(*args, **kwargs)
    except AuthenticationError:
        client = _get_client(force_reauth=True)
        fn = getattr(client, fn_name)
        return fn(*args, **kwargs)


# --------------------------------------------------------------------------
# Patient resolution helpers
# --------------------------------------------------------------------------

def _refresh_patients() -> list[Patient]:
    global _patient_cache
    _patient_cache = _call_with_retry("get_patients")
    return _patient_cache


def _resolve_patient(identifier: str) -> Patient:
    """Resolve a patient_id (UUID string) or 'First Last' name to a Patient."""
    patients = _patient_cache or _refresh_patients()

    def _find(pool: list[Patient]) -> Patient | None:
        needle = identifier.strip().lower()
        for p in pool:
            if str(p.patient_id).lower() == needle or str(p.id).lower() == needle:
                return p
            if f"{p.first_name} {p.last_name}".strip().lower() == needle:
                return p
        return None

    match = _find(patients)
    if match is None:
        # Cache may be stale (e.g. a new follower was added) — refresh once.
        match = _find(_refresh_patients())
    if match is None:
        names = ", ".join(f"{p.first_name} {p.last_name}" for p in patients) or "(none)"
        raise ValueError(
            f"No followed patient matches '{identifier}'. "
            f"Known patients: {names}. Use list_patients() to see all options."
        )
    return match


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _patient_to_dict(p: Patient) -> dict:
    return {
        "patient_id": str(p.patient_id),
        "name": f"{p.first_name} {p.last_name}",
        "first_name": p.first_name,
        "last_name": p.last_name,
    }


def _measurement_to_dict(m: GlucoseMeasurement) -> dict:
    d = {
        "value": m.value,
        "value_mg_per_dl": m.value_in_mg_per_dl,
        "is_high": m.is_high,
        "is_low": m.is_low,
        "timestamp": _iso(m.timestamp),
        "factory_timestamp_utc": _iso(m.factory_timestamp),
    }
    trend = getattr(m, "trend", None)
    if trend is not None:
        d["trend"] = trend.name
        d["trend_arrow"] = trend.indicator
    return d


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, PatientNotFoundError):
        return f"Patient not found on LibreLinkUp: {exc}"
    if isinstance(exc, AuthenticationError):
        return (
            "LibreLinkUp authentication failed. Check LIBRELINKUP_EMAIL, "
            "LIBRELINKUP_PASSWORD, and LIBRELINKUP_REGION."
        )
    if isinstance(exc, PyLibreLinkUpError):
        return f"LibreLinkUp API error: {exc}"
    return f"Unexpected error: {exc}"


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

@mcp.tool()
def list_patients() -> list[dict]:
    """List every patient (follower connection) available on this LibreLinkUp account.

    Returns each patient's id, first/last name, and combined name. Use the
    returned patient_id or the full name as the `patient` argument to the
    other tools.
    """
    try:
        patients = _refresh_patients()
        return [_patient_to_dict(p) for p in patients]
    except Exception as exc:  # noqa: BLE001
        return [{"error": _friendly_error(exc)}]


@mcp.tool()
def get_current_glucose(patient: str) -> dict:
    """Get the most recent glucose measurement for a patient.

    Args:
        patient: a patient_id (UUID string) or "First Last" name, as returned
            by list_patients().

    Returns the latest glucose value (mmol/L or mg/dL depending on account
    units), high/low flags, trend direction/arrow, and the reading timestamp.
    """
    try:
        p = _resolve_patient(patient)
        measurement = _call_with_retry("latest", patient_identifier=p)
        result = _measurement_to_dict(measurement)
        result["patient"] = _patient_to_dict(p)
        return result
    except Exception as exc:  # noqa: BLE001
        return {"error": _friendly_error(exc)}


@mcp.tool()
def get_glucose_graph(patient: str) -> dict:
    """Get roughly the last 12 hours of glucose measurements for a patient
    (the same data used to draw the recent-history graph in the LibreLinkUp app).

    Args:
        patient: a patient_id (UUID string) or "First Last" name, as returned
            by list_patients().
    """
    try:
        p = _resolve_patient(patient)
        measurements = _call_with_retry("graph", patient_identifier=p)
        return {
            "patient": _patient_to_dict(p),
            "count": len(measurements),
            "measurements": [_measurement_to_dict(m) for m in measurements],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": _friendly_error(exc)}


@mcp.tool()
def get_glucose_logbook(patient: str) -> dict:
    """Get roughly the last two weeks of logged glucose events for a patient.

    Args:
        patient: a patient_id (UUID string) or "First Last" name, as returned
            by list_patients().
    """
    try:
        p = _resolve_patient(patient)
        measurements = _call_with_retry("logbook", patient_identifier=p)
        return {
            "patient": _patient_to_dict(p),
            "count": len(measurements),
            "measurements": [_measurement_to_dict(m) for m in measurements],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": _friendly_error(exc)}


if __name__ == "__main__":
    # Fail fast with a clear message if credentials are missing, rather than
    # waiting for the first tool call.
    if not os.environ.get("LIBRELINKUP_EMAIL") or not os.environ.get("LIBRELINKUP_PASSWORD"):
        print(
            "Warning: LIBRELINKUP_EMAIL / LIBRELINKUP_PASSWORD are not set. "
            "The server will start, but tool calls will fail until they are set "
            "(e.g. via your MCP client's 'env' config).",
            file=sys.stderr,
        )
    mcp.run()
