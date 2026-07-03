from tools._daemon import DaemonManager


class FakeResp:
    def __init__(self, code=201, body=None):
        self.status_code = code
        self.text = ""
        self._body = body or {"exit_code": 0, "stdout": "", "stderr": ""}

    def json(self):
        return self._body


class FakeClient:
    def __init__(self):
        self.deleted = []
        self.created = []
        self.ensure_calls = 0
        self._execute_codes: list[int] = []

    def ensure_running(self, **k):
        self.ensure_calls += 1

    def request(self, method, path, **k):
        if method == "DELETE":
            self.deleted.append(path)
            return FakeResp(200)
        if method == "POST" and path == "/v1/workspaces":
            self.created.append(path)
            return FakeResp(201)
        if method == "POST" and path.endswith("/execute"):
            if self._execute_codes:
                return FakeResp(self._execute_codes.pop(0))
            return FakeResp(200)
        return FakeResp(200)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def adv(self, d):
        self.t += d


def test_idle_evict_deletes():
    clk = Clock()
    fc = FakeClient()
    m = DaemonManager(idle_ttl=100.0, client=fc, clock=clk)
    m.ensure_workspace("ws-a", {})
    clk.adv(101)
    m._evict_idle()
    assert any("ws-a" in p for p in fc.deleted)


def test_lru_cap_deletes_oldest():
    clk = Clock()
    fc = FakeClient()
    m = DaemonManager(max_workspaces=2, client=fc, clock=clk)
    m.ensure_workspace("ws-a", {})
    clk.adv(1)
    m.ensure_workspace("ws-b", {})
    clk.adv(1)
    m.ensure_workspace("ws-c", {})  # over cap -> evict ws-a (oldest last_used)
    assert any("ws-a" in p for p in fc.deleted)


def test_execute_retries_on_cold_404():
    clk = Clock()
    fc = FakeClient()
    m = DaemonManager(client=fc, clock=clk)
    m.ensure_workspace("ws-a", {})
    # Simulate daemon having lost the workspace (e.g. restarted): first
    # execute 404s, retry after ensure_daemon()+ensure_workspace() succeeds.
    fc._execute_codes = [404, 200]
    out, err, code = m.execute("ws-a", "echo hi", config={})
    assert code == 0
    assert fc.ensure_calls >= 1
    assert len([p for p in fc.created if p == "/v1/workspaces"]) == 2


def test_execute_without_config_raises_clear_error_on_persistent_404():
    clk = Clock()
    fc = FakeClient()
    m = DaemonManager(client=fc, clock=clk)
    m.ensure_workspace("ws-a", {})
    fc._execute_codes = [404, 404]  # still 404 after retry (no config to recreate with)
    try:
        m.execute("ws-a", "echo hi")
    except RuntimeError as e:
        assert "404" in str(e) or "execute failed" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_shutdown_without_reaper_started():
    fc = FakeClient()
    m = DaemonManager(client=fc)
    m.ensure_workspace("ws-a", {})
    m.shutdown()  # must not raise even though start_reaper() was never called
    assert any("ws-a" in p for p in fc.deleted)
