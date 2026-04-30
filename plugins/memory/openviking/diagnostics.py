"""OpenViking standard diagnostics channel.

Emits structured JSON-lines events on stderr and keeps the most recent
events in an in-memory ring buffer for the ``hermes openviking diag`` CLI.

Wire-format envelope (one line per event)::

    {"openviking": {"event": "...", "session_id": "...", "ts": "...", "payload": {...}}}

Conforms to ``tests/fixtures/openviking_diag_schema.json``.

Caller contract — early guard pattern:
    if provider._diag_enabled:
        diagnostics.emit(provider, "prefetch", {"query": q, "elapsed_ms": ms})

Callers MUST check the flag themselves so that this module's body is not
entered (and no payload dict is constructed) when diagnostics are off. The
``is_enabled(provider)`` helper exists for symmetry but the literal early
guard inline at the call site stays the recommended pattern.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

# Allowed event types — kept in sync with the JSON Schema fixture.
ALLOWED_EVENTS = frozenset({
    "prefetch",
    "commit",
    "forget",
    "bypass",
    "phase2_complete",
    "phase2_error",
})

_RING_MAX = 100
_RING: Deque[Dict[str, Any]] = deque(maxlen=_RING_MAX)


def is_enabled(provider: Any) -> bool:
    """Return whether diagnostics are enabled on ``provider``.

    Reads the ``_diag_enabled`` attribute populated from
    ``V3Config.emit_standard_diagnostics``. Returns False when missing so
    that providers that never opted in stay silent.
    """
    return bool(getattr(provider, "_diag_enabled", False))


def emit(provider: Any, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Emit a diagnostic event.

    Writes one JSON line to stderr AND appends to the ring buffer. The
    caller is responsible for the early-guard check; this function does
    not re-check ``provider._diag_enabled`` so that the cost of the missed
    branch is paid at the call site, not here.

    Unknown ``event_type`` values are still emitted (not dropped) but a
    sentinel field is added so consumers can detect schema drift early.
    """
    session_id = getattr(provider, "_session_id", "") or "_default_"
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    inner: Dict[str, Any] = {
        "event": event_type,
        "session_id": session_id,
        "ts": ts,
        "payload": payload or {},
    }
    if event_type not in ALLOWED_EVENTS:
        inner["_unknown_event"] = True

    envelope = {"openviking": inner}
    _RING.append(envelope)

    try:
        sys.stderr.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        sys.stderr.flush()
    except Exception:
        # Diagnostics must never crash the provider. Swallow stderr errors
        # (closed file, broken pipe at shutdown) — the ring buffer still
        # captured the event for the CLI consumer.
        pass


def drain_ring() -> List[Dict[str, Any]]:
    """Return + clear the in-memory ring buffer.

    Used by the ``hermes openviking diag`` CLI to display recent events.
    """
    items = list(_RING)
    _RING.clear()
    return items


def peek_ring() -> List[Dict[str, Any]]:
    """Return a snapshot of the ring buffer without clearing it (test/CLI)."""
    return list(_RING)


def _reset_ring_for_tests() -> None:
    """Test-only hook to clear ring state between cases."""
    _RING.clear()


__all__ = [
    "ALLOWED_EVENTS",
    "drain_ring",
    "emit",
    "is_enabled",
    "peek_ring",
]
