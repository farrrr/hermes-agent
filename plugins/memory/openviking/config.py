"""OpenViking v3 runtime config — internal behavior flags.

These are NOT tenant settings (account / user / agent / endpoint live in
``get_config_schema()`` and are env-bound for setup CLI persistence). The
fields here are v3 behavior toggles + thresholds consumed by callers
(provider methods, gateway lifecycle hooks, run_agent scaffolding).

Defaults are deliberately conservative — every flag is off, every threshold
high enough to be a no-op for v2 traffic. Callers that need v3 behavior
construct a ``V3Config`` instance with explicit overrides.

``phase2_poll_timeout_ms`` is a platform-aware value (CLI=30000,
gateway=300000) and intentionally defaults to ``None``: callers MUST pick
the right value for their platform. TM-8 (T6) wires the platform-aware
selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class V3Config:
    """Runtime behavior flags + thresholds for OpenViking v3.

    All defaults are safe / off. Callers opt in by constructing with kwargs.
    """

    # ---- Phase 1 prefetch -------------------------------------------------
    prefetch_multi_session: bool = False

    # ---- Threshold-triggered in-flight commits ----------------------------
    threshold_commit: bool = False
    commit_char_threshold: int = 80_000
    commit_min_interval_seconds: int = 300

    # ---- Phase 2 extraction polling ---------------------------------------
    # Platform-aware: CLI=30000, gateway=300000. None forces caller to pick.
    phase2_poll_timeout_ms: Optional[int] = None
    phase2_poll_interval_ms: int = 500

    # ---- Tenant scope isolation -------------------------------------------
    isolate_user_scope_by_agent: bool = False
    isolate_agent_scope_by_user: bool = False

    # ---- Bypass / capture toggles -----------------------------------------
    bypass_session_patterns: List[str] = field(default_factory=list)
    emit_standard_diagnostics: bool = False
    structured_capture: bool = False


__all__ = ["V3Config"]
