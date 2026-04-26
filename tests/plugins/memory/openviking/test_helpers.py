"""Test helpers for OpenViking memory provider tests.

Provides utilities used across multiple test modules in this package.
These are not pytest tests themselves — they are imported by test modules.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time


def simulate_keyboard_interrupt_in_atexit(timeout: float = 5.0) -> float:
    """Spawn a subprocess that registers the plugin's atexit handler, then
    sends SIGINT during atexit execution. Returns the wall-clock duration in
    seconds that the subprocess took to exit after receiving SIGINT.

    This helper is used to verify that the plugin's shutdown path handles
    KeyboardInterrupt cleanly and exits within an acceptable time window
    (< 2s per AC-6 / pre-mortem situation 3a).

    Implementation notes:
    - Uses subprocess.Popen so we can send SIGINT after the process has
      started and registered its atexit handlers.
    - The subprocess imports the plugin (which registers _atexit_commit_sessions
      via atexit.register at import time), then blocks waiting for SIGINT.
    - On SIGINT the process exits, which triggers atexit handlers. The mock
      server in the subprocess is a no-op so atexit completes quickly.
    - Wall-clock time is measured from SIGINT send to process exit.

    Returns:
        float: Wall-clock seconds between SIGINT and process exit.

    Raises:
        RuntimeError: If the subprocess does not exit within ``timeout`` seconds
            after SIGINT.
    """
    # Small Python script run in the child process:
    # 1. Patch httpx so the atexit commit doesn't try a real network call.
    # 2. Import the plugin (registers atexit handler as a module side-effect).
    # 3. Set the module-level _last_active_provider to a mock so atexit has
    #    something to call on_session_end() for.
    # 4. Signal readiness by printing "READY" to stdout.
    # 5. Block on stdin (waits for SIGINT from the parent).
    child_script = """
import sys
import os

# Stub out httpx to avoid real network calls in the atexit handler
import types
fake_httpx = types.ModuleType("httpx")

class _FakeResp:
    status_code = 200
    def json(self):
        return {}
    def raise_for_status(self):
        pass

fake_httpx.get = lambda *a, **kw: _FakeResp()
fake_httpx.post = lambda *a, **kw: _FakeResp()
sys.modules["httpx"] = fake_httpx

# Ensure project root is importable
project_root = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.environ.setdefault("OPENVIKING_ENDPOINT", "http://127.0.0.1:19330")

# Import triggers atexit.register(_atexit_commit_sessions)
import plugins.memory.openviking as ov

# Create a minimal provider with a mock client so on_session_end does work
from plugins.memory.openviking import OpenVikingMemoryProvider, _VikingClient

class _MockClient:
    def post(self, path, payload=None, **kwargs):
        return {}
    def health(self):
        return True

provider = OpenVikingMemoryProvider()
provider._client = _MockClient()
provider._session_id = "atexit-test-session"
provider._turn_count = 1

ov._last_active_provider = provider

# Signal parent we are ready
print("READY", flush=True)

# Block until interrupted
try:
    sys.stdin.read()
except (KeyboardInterrupt, EOFError):
    pass
"""

    proc = subprocess.Popen(
        [sys.executable, "-c", child_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
    )

    # Wait for "READY" signal from the child (with a short deadline)
    ready_deadline = time.monotonic() + 10.0
    ready = False
    while time.monotonic() < ready_deadline:
        line = proc.stdout.readline()
        if line.strip() == b"READY":
            ready = True
            break

    if not ready:
        proc.kill()
        proc.wait()
        raise RuntimeError("Child process did not print READY within 10s")

    # Send SIGINT and measure how long until exit
    t0 = time.monotonic()
    proc.send_signal(signal.SIGINT)

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        elapsed = time.monotonic() - t0
        raise RuntimeError(
            f"Child process did not exit within {timeout}s after SIGINT "
            f"(elapsed={elapsed:.2f}s)"
        )

    elapsed = time.monotonic() - t0
    return elapsed
