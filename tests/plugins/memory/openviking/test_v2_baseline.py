"""T0b baseline tests — structural invariants after mechanical file split.

Moved from tests/plugins/memory/test_openviking_provider.py plus three new
invariant tests that enforce the split constraints.
"""

from __future__ import annotations

import ast
import atexit
import json
from unittest.mock import MagicMock

from plugins.memory.openviking import OpenVikingMemoryProvider


# ---------------------------------------------------------------------------
# Tests moved from test_openviking_provider.py
# ---------------------------------------------------------------------------

def test_tool_search_sorts_by_raw_score_across_buckets():
    provider = OpenVikingMemoryProvider()
    provider._client = MagicMock()
    provider._client.post.return_value = {
        "result": {
            "memories": [
                {"uri": "viking://memories/1", "score": 0.9003, "abstract": "memory result"},
            ],
            "resources": [
                {"uri": "viking://resources/1", "score": 0.9004, "abstract": "resource result"},
            ],
            "skills": [
                {"uri": "viking://skills/1", "score": 0.8999, "abstract": "skill result"},
            ],
            "total": 3,
        }
    }

    result = json.loads(provider._tool_search({"query": "ranking"}))

    assert [entry["uri"] for entry in result["results"]] == [
        "viking://resources/1",
        "viking://memories/1",
        "viking://skills/1",
    ]
    assert [entry["score"] for entry in result["results"]] == [0.9, 0.9, 0.9]
    assert result["total"] == 3


def test_tool_search_sorts_missing_raw_score_after_negative_scores():
    provider = OpenVikingMemoryProvider()
    provider._client = MagicMock()
    provider._client.post.return_value = {
        "result": {
            "memories": [
                {"uri": "viking://memories/missing", "abstract": "missing score"},
            ],
            "resources": [
                {"uri": "viking://resources/negative", "score": -0.25, "abstract": "negative score"},
            ],
            "skills": [
                {"uri": "viking://skills/positive", "score": 0.1, "abstract": "positive score"},
            ],
            "total": 3,
        }
    }

    result = json.loads(provider._tool_search({"query": "ranking"}))

    assert [entry["uri"] for entry in result["results"]] == [
        "viking://skills/positive",
        "viking://memories/missing",
        "viking://resources/negative",
    ]
    assert [entry["score"] for entry in result["results"]] == [0.1, 0.0, -0.25]
    assert result["total"] == 3


# ---------------------------------------------------------------------------
# Invariant: atexit handler registered exactly once
# ---------------------------------------------------------------------------

def test_atexit_handler_registered_once():
    """Importing the plugin must register _atexit_commit_sessions exactly once.

    Uses a subprocess to get a clean Python interpreter with no prior imports,
    then counts registrations via atexit._ncallbacks() before and after import.
    Python 3.13 uses a C-level atexit that does not expose _exithandlers, so
    we measure the delta instead of inspecting the handler list directly.
    """
    import subprocess
    import sys

    import pathlib
    project_root = str(pathlib.Path(__file__).parent.parent.parent.parent.parent)

    # Build the child script by injecting project_root as a literal string,
    # then appending the rest as a plain string to avoid f-string brace conflicts.
    child_script = (
        f"import sys, os\nproject_root = {project_root!r}\n"
        "if project_root not in sys.path:\n"
        "    sys.path.insert(0, project_root)\n"
        "\n"
        "import types\n"
        "fake_httpx = types.ModuleType('httpx')\n"
        "class _FakeResp:\n"
        "    status_code = 200\n"
        "    def json(self): return {}\n"
        "    def raise_for_status(self): pass\n"
        "fake_httpx.get = lambda *a, **kw: _FakeResp()\n"
        "fake_httpx.post = lambda *a, **kw: _FakeResp()\n"
        "sys.modules['httpx'] = fake_httpx\n"
        "\n"
        "import atexit\n"
        "_original_register = atexit.register\n"
        "_commit_count = 0\n"
        "\n"
        "def _counting_register(func, *args, **kwargs):\n"
        "    global _commit_count\n"
        "    if callable(func) and getattr(func, '__name__', '') == '_atexit_commit_sessions':\n"
        "        _commit_count += 1\n"
        "    return _original_register(func, *args, **kwargs)\n"
        "\n"
        "atexit.register = _counting_register\n"
        "import plugins.memory.openviking\n"
        "atexit.register = _original_register\n"
        "print(_commit_count)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", child_script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    count = int(result.stdout.strip())
    assert count == 1, (
        f"Expected exactly 1 atexit registration of _atexit_commit_sessions, got {count}. "
        "Check that atexit.register(_atexit_commit_sessions) is called only once, "
        "in plugins/memory/openviking/__init__.py."
    )


# ---------------------------------------------------------------------------
# Invariant: class identity preserved
# ---------------------------------------------------------------------------

def test_class_identity_preserved():
    """OpenVikingMemoryProvider must live in plugins.memory.openviking, not a re-export stub."""
    assert OpenVikingMemoryProvider.__module__ == "plugins.memory.openviking", (
        f"Expected __module__ == 'plugins.memory.openviking', "
        f"got {OpenVikingMemoryProvider.__module__!r}"
    )


# ---------------------------------------------------------------------------
# Invariant: tools.py has no runtime import of OpenVikingMemoryProvider
# ---------------------------------------------------------------------------

def test_tools_module_has_no_runtime_provider_import():
    """tools.py must not import OpenVikingMemoryProvider at the top level.

    The only allowed reference is inside an `if TYPE_CHECKING:` block.
    Parse the AST and assert no top-level `from . import OpenVikingMemoryProvider`
    (or equivalent) exists outside a TYPE_CHECKING guard.
    """
    import pathlib

    tools_path = pathlib.Path(__file__).parent.parent.parent.parent.parent / (
        "plugins/memory/openviking/tools.py"
    )
    source = tools_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(tools_path))

    # Collect names of top-level `if` blocks that guard TYPE_CHECKING
    type_checking_bodies: list[ast.stmt] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If):
            # Match `if TYPE_CHECKING:` — the test value is a plain Name node
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                type_checking_bodies.extend(node.body)
            # Also match `if typing.TYPE_CHECKING:`
            elif (
                isinstance(node.test, ast.Attribute)
                and node.test.attr == "TYPE_CHECKING"
            ):
                type_checking_bodies.extend(node.body)

    type_checking_body_ids = {id(n) for n in type_checking_bodies}

    # Now check that no top-level import of OpenVikingMemoryProvider exists
    # outside those bodies
    for node in ast.iter_child_nodes(tree):
        if id(node) in type_checking_body_ids:
            continue  # inside TYPE_CHECKING — allowed
        if isinstance(node, ast.ImportFrom):
            # from . import OpenVikingMemoryProvider
            for alias in node.names:
                assert alias.name != "OpenVikingMemoryProvider", (
                    "tools.py has a top-level runtime import of OpenVikingMemoryProvider. "
                    "Move it under `if TYPE_CHECKING:` to avoid circular imports."
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "OpenVikingMemoryProvider" not in alias.name, (
                    "tools.py has a top-level runtime import of OpenVikingMemoryProvider."
                )
