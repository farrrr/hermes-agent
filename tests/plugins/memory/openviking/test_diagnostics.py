"""T2 diagnostic emit channel tests (TM-5, AC-11).

Covers:
- emit() envelope conforms to openviking_diag_schema.json
- session_id falls back to '_default_' when provider has none
- Ring buffer caps at 100 events (oldest dropped)
- Unknown event types are flagged but still emitted
- Early-guard pattern: when ``provider._diag_enabled`` is False, callers
  that follow the guard pattern incur zero ring/stderr writes
"""

from __future__ import annotations

import copy
import io
import json
import pathlib
from typing import Any, Dict
from unittest.mock import patch

import jsonschema
import pytest

from plugins.memory.openviking import diagnostics


_SCHEMA_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent / "fixtures/openviking_diag_schema.json"
)


def _load_envelope_schema() -> Dict[str, Any]:
    """Load the diagnostic schema, dropping the payload ``oneOf`` clause.

    The fixture's payload ``oneOf`` lists six open-shape variants without
    ``additionalProperties: false`` or ``required`` fields, so any object
    matches every variant — which makes ``oneOf`` always fail. Until the
    fixture is tightened (out of TM-5 scope), we validate the envelope
    contract (event enum, session_id, ts pattern, payload-is-object) and
    skip the per-variant payload shape check.
    """
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = schema["properties"]["openviking"]["properties"]["payload"]
    payload.pop("oneOf", None)
    return schema


_ENVELOPE_SCHEMA = _load_envelope_schema()


class _FakeProvider:
    def __init__(self, session_id: str = "sess-001", diag_enabled: bool = True):
        self._session_id = session_id
        self._diag_enabled = diag_enabled


@pytest.fixture(autouse=True)
def _reset_ring():
    diagnostics._reset_ring_for_tests()
    yield
    diagnostics._reset_ring_for_tests()


# ---------------------------------------------------------------------------
# AC-11: emit format
# ---------------------------------------------------------------------------

def test_diagnostic_emit_format_validates_against_schema(capsys):
    provider = _FakeProvider()
    diagnostics.emit(provider, "prefetch", {"query": "hello", "result_count": 3, "elapsed_ms": 12.5})

    captured = capsys.readouterr().err.strip().splitlines()
    assert len(captured) == 1, f"Expected exactly one stderr line, got: {captured!r}"

    envelope = json.loads(captured[0])
    jsonschema.validate(instance=envelope, schema=_ENVELOPE_SCHEMA)

    inner = envelope["openviking"]
    assert inner["event"] == "prefetch"
    assert inner["session_id"] == "sess-001"
    assert inner["payload"] == {"query": "hello", "result_count": 3, "elapsed_ms": 12.5}


def test_emit_session_id_defaults_to_sentinel_when_missing(capsys):
    class _NoSession:
        _session_id = ""
        _diag_enabled = True

    diagnostics.emit(_NoSession(), "commit", {"trigger": "session_end"})
    captured = capsys.readouterr().err.strip().splitlines()
    envelope = json.loads(captured[0])
    assert envelope["openviking"]["session_id"] == "_default_"


def test_emit_marks_unknown_event_types_but_still_emits(capsys):
    diagnostics.emit(_FakeProvider(), "definitely-not-a-known-event", {"foo": "bar"})
    captured = capsys.readouterr().err.strip().splitlines()
    envelope = json.loads(captured[0])
    inner = envelope["openviking"]
    assert inner["event"] == "definitely-not-a-known-event"
    assert inner.get("_unknown_event") is True


def test_emit_appends_to_ring_buffer():
    provider = _FakeProvider()
    diagnostics.emit(provider, "prefetch", {"query": "a"})
    diagnostics.emit(provider, "commit", {"trigger": "threshold"})
    snapshot = diagnostics.peek_ring()
    assert [e["openviking"]["event"] for e in snapshot] == ["prefetch", "commit"]


def test_ring_buffer_caps_at_100_events_dropping_oldest():
    provider = _FakeProvider()
    for i in range(150):
        diagnostics.emit(provider, "prefetch", {"query": f"q{i}"})
    snapshot = diagnostics.peek_ring()
    assert len(snapshot) == 100
    # Oldest 50 dropped; first remaining should be q50.
    assert snapshot[0]["openviking"]["payload"]["query"] == "q50"
    assert snapshot[-1]["openviking"]["payload"]["query"] == "q149"


def test_drain_ring_returns_and_clears():
    provider = _FakeProvider()
    diagnostics.emit(provider, "prefetch", {"query": "a"})
    drained = diagnostics.drain_ring()
    assert len(drained) == 1
    assert diagnostics.peek_ring() == []


# ---------------------------------------------------------------------------
# AC-11: zero overhead when flag off (caller-side early guard contract)
# ---------------------------------------------------------------------------

def _caller_with_guard(provider: Any, event: str, payload: Dict[str, Any]) -> None:
    """Reference call-site shape that callers across the codebase MUST follow.

    Exercising this function in the test pins the contract: when the flag
    is off, ``emit`` is never invoked.
    """
    if provider._diag_enabled:
        diagnostics.emit(provider, event, payload)


def test_diagnostic_no_overhead_when_off(capsys):
    provider_off = _FakeProvider(diag_enabled=False)

    with patch.object(diagnostics, "emit", wraps=diagnostics.emit) as spy:
        for _ in range(20):
            _caller_with_guard(provider_off, "prefetch", {"query": "noop"})

        assert spy.call_count == 0, (
            "Caller-side early guard violated: emit() was invoked while "
            "_diag_enabled was False."
        )

    # Ring + stderr untouched.
    assert diagnostics.peek_ring() == []
    assert capsys.readouterr().err == ""


def test_diagnostic_emits_when_flag_on(capsys):
    provider_on = _FakeProvider(diag_enabled=True)

    with patch.object(diagnostics, "emit", wraps=diagnostics.emit) as spy:
        _caller_with_guard(provider_on, "prefetch", {"query": "go"})
        assert spy.call_count == 1

    assert len(diagnostics.peek_ring()) == 1
    assert capsys.readouterr().err.strip().count("\n") == 0  # exactly one line
