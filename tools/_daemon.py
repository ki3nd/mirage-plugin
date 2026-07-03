"""DaemonManager: spawn/reach the mirage daemon, create/reuse workspaces,
and execute commands against them.

All calls are synchronous httpx over HTTP against a locally-spawned
``uvicorn mirage.server.daemon:app`` process (see
``mirage.cli.client.DaemonClient``). This module is safe to import and use
from a gevent-patched process (the plugin runtime): it never runs an
asyncio event loop itself -- all async work happens inside the daemon
subprocess.

Task 5 scope adds: client-side idle-TTL eviction + LRU capping (the
daemon does not evict individual workspaces itself -- only self-exits
once its whole registry has been empty for a grace period), a
background reaper thread, retry-on-cold for ``execute``/
``ensure_workspace`` (daemon down or workspace evicted/gone -> respawn
+ recreate + retry once), and the module-level ``MANAGER`` singleton.
"""

import atexit
import os
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
        spawn duplicate daemon processes. Fast-path the common
        already-running case with a lock-free health check so concurrent
        invocations don't all serialize through ``_daemon_lock`` just to
        confirm the daemon is up; take the lock only when a spawn looks
        necessary (re-checking inside the lock so only one greenlet spawns).
        """
        if self._client.is_reachable():
            return
        with self._daemon_lock:
            if self._client.is_reachable():
                return
            self._client.ensure_running(startup_timeout=40.0, allow_spawn=True)

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return self._client.request(method, path, **kw)

    def _create_workspace(self, wid: str, config: dict) -> None:
        r = self._req("POST", "/v1/workspaces",
                      json={"config": config, "id": wid})
        if r.status_code not in (201, 409):
            raise RuntimeError(
                f"create workspace failed: {r.status_code} {r.text[:200]}")

    def ensure_workspace(self, wid: str, config: dict) -> None:
        """Create the workspace if it doesn't exist yet (409 = already
        exists = also fine -- idempotent reuse), and record it as used.

        Runs eviction first (idle-TTL), so a stale workspace never
        counts against the cap before it's swept, then enforces the
        LRU cap after tracking the new/reused workspace.
        """
        self._evict_idle()
        try:
            self._create_workspace(wid, config)
        except httpx.RequestError:
            # Daemon down (e.g. reaped itself while idle, or never spawned
            # in this process) -- respawn once and retry.
            self.ensure_daemon()
            self._create_workspace(wid, config)
        with self._lock:
            self._last_used[wid] = self._clock()
        self._enforce_cap()

    def execute(self, wid: str, command: str, config: dict | None = None,
               timeout: float = 110.0) -> tuple[str, str, int]:
        """Run ``command`` inside workspace ``wid``.

        On a cold failure (daemon unreachable, or a 404 meaning the
        workspace was evicted / the daemon lost its registry) this
        respawns the daemon, recreates the workspace when ``config`` is
        given, and retries exactly once before giving up.

        Returns:
            tuple[str, str, int]: ``(stdout, stderr, exit_code)``. The
            daemon materializes stdout/stderr fully before responding
            (``{"kind": "io", "exit_code", "stdout", "stderr"}``), so
            there is no streaming to handle here.
        """
        try:
            r = self._req("POST", f"/v1/workspaces/{wid}/execute",
                          json={"command": command}, timeout=timeout)
            cold = r.status_code == 404
        except httpx.RequestError:
            r = None
            cold = True

        if cold:
            self.ensure_daemon()
            if config is not None:
                self.ensure_workspace(wid, config)
            try:
                r = self._req("POST", f"/v1/workspaces/{wid}/execute",
                              json={"command": command}, timeout=timeout)
            except httpx.RequestError as exc:
                raise RuntimeError(
                    f"execute failed: daemon unreachable after retry ({exc})"
                ) from exc

        if r.status_code != 200:
            raise RuntimeError(
                f"execute failed: {r.status_code} {r.text[:200]}")
        with self._lock:
            self._last_used[wid] = self._clock()
        d = r.json()
        return d.get("stdout", ""), d.get("stderr", ""), int(d.get("exit_code", 1))

    def snapshot(self, wid: str, compress: str | None = None,
                timeout: float = 110.0) -> bytes:
        """Snapshot workspace ``wid`` to a tar file on the daemon host and
        return its bytes.

        The daemon endpoint writes the tar to its own ``snapshot_root``
        (path is relative in the request, resolved+returned absolute in
        the response) and does NOT stream bytes or compress -- since the
        daemon is a subprocess on the same host as the plugin, we just
        open the returned absolute path and read it. If ``compress ==
        "gz"``, gzip client-side (the daemon never compresses).
        """
        r = self._req("POST", f"/v1/workspaces/{wid}/snapshot",
                      json={"path": f"{wid}.tar"}, timeout=timeout)
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"snapshot failed: {r.status_code} {r.text[:200]}")
        path = r.json()["path"]
        try:
            with open(path, "rb") as f:
                data = f.read()
        finally:
            # The daemon writes one tar per (deterministic) workspace id;
            # we already hold the bytes, so delete the on-disk file to
            # avoid unbounded accumulation under the daemon's snapshot_root.
            try:
                os.unlink(path)
            except OSError:
                pass
        if compress == "gz":
            import gzip
            data = gzip.compress(data)
        with self._lock:
            self._last_used[wid] = self._clock()
        return data

    def delete(self, wid: str) -> None:
        try:
            self._req("DELETE", f"/v1/workspaces/{wid}")
        except httpx.RequestError:
            pass
        with self._lock:
            self._last_used.pop(wid, None)

    def _evict_idle(self) -> None:
        """DELETE every workspace idle longer than ``idle_ttl``.

        Gathers the stale ids while holding ``self._lock``, then issues
        the (HTTP) deletes outside the lock -- ``delete()`` itself
        acquires the lock, so holding it across the HTTP call would
        deadlock/serialize unnecessarily.
        """
        now = self._clock()
        with self._lock:
            stale = [w for w, t in self._last_used.items()
                    if now - t > self._idle_ttl]
        for wid in stale:
            self.delete(wid)

    def _enforce_cap(self) -> None:
        """If tracked workspaces exceed ``max_workspaces``, DELETE the
        oldest-by-last-used ones down to the cap. Same lock discipline
        as ``_evict_idle``: gather inside the lock, delete outside it.
        """
        with self._lock:
            over = len(self._last_used) - self._max
            if over <= 0:
                return
            ordered = sorted(self._last_used.items(), key=lambda kv: kv[1])
            drop = [w for w, _ in ordered[:over]]
        for wid in drop:
            self.delete(wid)

    def start_reaper(self, interval: float = 60.0) -> None:
        """Start a daemon background thread that periodically sweeps
        idle workspaces. Safe to skip in tests (leave unstarted) --
        ``shutdown()`` tolerates never having a reaper.
        """
        self._reaper_stop = threading.Event()

        def _loop() -> None:
            while not self._reaper_stop.wait(interval):
                try:
                    self._evict_idle()
                except Exception:
                    pass

        self._reaper = threading.Thread(
            target=_loop, name="mirage-v2-reaper", daemon=True)
        self._reaper.start()

    def shutdown(self) -> None:
        """Stop the reaper (if started) then DELETE every tracked
        workspace. The daemon process itself self-exits once its
        registry goes empty (idle grace period), so there is nothing
        else to do here.
        """
        stop = getattr(self, "_reaper_stop", None)
        if stop is not None:
            stop.set()
        with self._lock:
            wids = list(self._last_used)
        for wid in wids:
            self.delete(wid)


# Module-level singleton: one DaemonManager per plugin process, shared by
# every tool invocation. The reaper thread (interval per spec sec 6.4:
# 60s) starts immediately so idle workspaces get swept even between tool
# calls; atexit makes sure tracked workspaces are DELETEd (and the reaper
# stopped) on interpreter shutdown.
MANAGER = DaemonManager()
MANAGER.start_reaper()
atexit.register(MANAGER.shutdown)
