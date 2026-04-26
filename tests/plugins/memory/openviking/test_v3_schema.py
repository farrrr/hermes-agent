"""T1 schema invariants — plugin.yaml shape + v3 runtime config defaults.

Verifies:
- plugin.yaml is bumped to 2.1.0
- plugin.yaml has NO ``default_config`` block (loader doesn't read it)
- ``get_config_schema()`` still returns ONLY the v2 tenant/env entries
  (v3 behavior flags do NOT belong in the setup schema — they live in
  ``plugins.memory.openviking.config.V3Config``)
- ``V3Config`` defaults are safe / off for every flag
- ``phase2_poll_timeout_ms`` defaults to ``None`` so callers must pick the
  platform-aware value (TM-8/T6 wires CLI=30000, gateway=300000)
"""

from __future__ import annotations

import pathlib

import yaml

from plugins.memory.openviking import OpenVikingMemoryProvider
from plugins.memory.openviking.config import V3Config


_PLUGIN_YAML = (
    pathlib.Path(__file__).parent.parent.parent.parent.parent
    / "plugins/memory/openviking/plugin.yaml"
)


def _load_plugin_yaml() -> dict:
    return yaml.safe_load(_PLUGIN_YAML.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# plugin.yaml shape
# ---------------------------------------------------------------------------

def test_plugin_yaml_version_bumped_to_2_1_0():
    data = _load_plugin_yaml()
    assert data.get("version") == "2.1.0", (
        f"Expected plugin.yaml version 2.1.0, got {data.get('version')!r}"
    )


def test_yaml_schema_no_default_config():
    """plugin.yaml must NOT carry a ``default_config`` block.

    The plugin loader does not read it, so any entries there are silently
    dead config. ``get_config_schema()`` is the single source of truth for
    setup-CLI defaults; ``V3Config`` is the source of truth for runtime
    behavior defaults.
    """
    data = _load_plugin_yaml()
    assert "default_config" not in data, (
        "plugin.yaml has a default_config key, but the loader doesn't read it. "
        "Move tenant defaults into get_config_schema() and runtime flags into "
        "plugins.memory.openviking.config.V3Config."
    )


# ---------------------------------------------------------------------------
# get_config_schema() must stay tenant-only (no v3 runtime flags)
# ---------------------------------------------------------------------------

_V3_FLAG_KEYS = {
    "prefetch_multi_session",
    "threshold_commit",
    "commit_char_threshold",
    "commit_min_interval_seconds",
    "phase2_poll_timeout_ms",
    "phase2_poll_interval_ms",
    "isolate_user_scope_by_agent",
    "isolate_agent_scope_by_user",
    "bypass_session_patterns",
    "emit_standard_diagnostics",
    "structured_capture",
}


def test_setup_schema_excludes_v3_runtime_flags():
    """Per memory_provider base contract, schema entries without ``env_var``
    are dropped by setup CLI persistence (no save_config override here).

    v3 behavior flags must not pollute the schema — they live in V3Config.
    """
    schema = OpenVikingMemoryProvider().get_config_schema()
    keys = {entry["key"] for entry in schema}
    leaked = keys & _V3_FLAG_KEYS
    assert not leaked, (
        f"v3 runtime flags leaked into get_config_schema(): {leaked}. "
        "These belong in plugins.memory.openviking.config.V3Config."
    )

    # Every remaining entry must still have env_var (base contract).
    missing_env_var = [
        e["key"] for e in schema if not e.get("env_var")
    ]
    assert not missing_env_var, (
        f"Schema entries missing env_var (base contract violation): {missing_env_var}"
    )


# ---------------------------------------------------------------------------
# V3Config defaults
# ---------------------------------------------------------------------------

_V3_DEFAULTS = {
    "prefetch_multi_session": False,
    "threshold_commit": False,
    "commit_char_threshold": 80_000,
    "commit_min_interval_seconds": 300,
    "phase2_poll_timeout_ms": None,  # platform-aware sentinel
    "phase2_poll_interval_ms": 500,
    "isolate_user_scope_by_agent": False,
    "isolate_agent_scope_by_user": False,
    "bypass_session_patterns": [],
    "emit_standard_diagnostics": False,
    "structured_capture": False,
}


def test_v3_config_defaults_are_safe():
    cfg = V3Config()
    for key, expected in _V3_DEFAULTS.items():
        actual = getattr(cfg, key)
        assert actual == expected, (
            f"V3Config.{key}: expected {expected!r}, got {actual!r}"
        )


def test_phase2_poll_timeout_default_is_none_sentinel():
    """The platform-aware timeout default MUST be None so callers cannot
    accidentally use the schema default — TM-8/T6 selects CLI=30000 vs
    gateway=300000 explicitly.
    """
    assert V3Config().phase2_poll_timeout_ms is None
