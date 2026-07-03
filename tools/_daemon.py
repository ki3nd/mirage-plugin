"""DaemonManager: spawn/reach the mirage daemon, create/reuse workspaces,
and execute commands against them.

All calls are synchronous httpx over HTTP against a locally-spawned
``uvicorn mirage.server.daemon:app`` process (see
``mirage.cli.client.DaemonClient``). This module is safe to import and use
from a gevent-patched process (the plugin runtime): it never runs an
asyncio event loop itself -- all async work happens inside the daemon
subprocess.

Task 4 scope: ``ensure_daemon``/``ensure_workspace``/``execute`` plus a
minimal ``delete``/``shutdown`` (enough for test teardown). Idle-TTL
eviction, the background reaper, retry-on-cold, and the module-level
singleton wiring land in Task 5.
"""

import threading
import time

import httpx

from mirage.cli.client import make_client


class DaemonManager:
    """Owns one ``DaemonClient`` and tracks last-used times per workspace
    id so later tasks can add idle-TTL eviction / LRU capping on top.
    """

    def __init__(self, *, idle_ttl: float = 600.0, max_workspaces: int = 6,
                 clock=time.monotonic, client=None) -> None:
        self._client = client if client is not None else make_client()
        self._lock = threading.Lock()
        self._daemon_lock = threading.Lock()
        self._last_used: dict[str, float] = {}
        self._idle_ttl = idle_ttl
        self._max = max_workspaces
        self._clock = clock

    def ensure_daemon(self) -> None:
        """Idempotently make sure the daemon is reachable, spawning it if
        needed. Guarded by a lock so concurrent callers don't race to
        spawn duplicate daemon processes.
        """
        with self._daemon_lock:
            self._client.ensure_running(startup_timeout=40.0, allow_spawn=True)

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return self._client.request(method, path, **kw)

    def ensure_workspace(self, wid: str, config: dict) -> None:
        """Create the workspace if it doesn't exist yet (409 = already
        exists = also fine -- idempotent reuse), and record it as used.
        """
        r = self._req("POST", "/v1/workspaces",
                      json={"config": config, "id": wid})
        if r.status_code not in (201, 409):
            raise RuntimeError(
                f"create workspace failed: {r.status_code} {r.text[:200]}")
        with self._lock:
            self._last_used[wid] = self._clock()

    def execute(self, wid: str, command: str,
               timeout: float = 110.0) -> tuple[str, str, int]:
        """Run ``command`` inside workspace ``wid``.

        Returns:
            tuple[str, str, int]: ``(stdout, stderr, exit_code)``. The
            daemon materializes stdout/stderr fully before responding
            (``{"kind": "io", "exit_code", "stdout", "stderr"}``), so
            there is no streaming to handle here.
        """
        with self._lock:
            self._last_used[wid] = self._clock()
        r = self._req("POST", f"/v1/workspaces/{wid}/execute",
                      json={"command": command}, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(
                f"execute failed: {r.status_code} {r.text[:200]}")
        d = r.json()
        return d.get("stdout", ""), d.get("stderr", ""), int(d.get("exit_code", 1))

    def delete(self, wid: str) -> None:
        try:
            self._req("DELETE", f"/v1/workspaces/{wid}")
        except httpx.RequestError:
            pass
        with self._lock:
            self._last_used.pop(wid, None)

    def shutdown(self) -> None:
        """Minimal teardown: DELETE every tracked workspace. The daemon
        process itself self-exits once its registry goes empty (idle
        grace period), so there is nothing else to do here yet. Reaper
        thread stop + LRU/idle-TTL eviction land in Task 5.
        """
        with self._lock:
            wids = list(self._last_used)
        for wid in wids:
            self.delete(wid)
