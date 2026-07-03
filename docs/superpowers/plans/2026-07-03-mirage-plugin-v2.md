# Mirage Plugin v2 (daemon) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dify tool plugin chạy lệnh bash xuyên nhiều resource của mirage, bằng cách chạy mirage trong **một daemon subprocess (không gevent)** và gọi tới nó bằng httpx sync từ plugin (dưới gevent). Tool `execute` + `snapshot`; connection/cache tái dùng trong daemon; keying mỗi (conversation×config) một workspace.

**Architecture:** Plugin process (dưới gevent) là thin client. `DaemonManager` singleton tầng module: spawn daemon (`uvicorn mirage.server.daemon:app`) khi cần, tạo/tái dùng workspace theo `workspace_id = hash(yaml+env+cache+conversation_id)`, execute/snapshot qua REST. Async của mirage sống trong daemon → né hẳn gevent. Client-side eviction (TTL/LRU → DELETE workspace).

**Tech Stack:** Python 3.12, `dify_plugin>=0.7.4`, `mirage-ai[backends]`, `httpx`, `uvicorn`, pytest.

## Global Constraints

- `requirements.txt`: `dify_plugin>=0.7.4`; `mirage-ai[s3,r2,gcs,oci,redis,postgres,mongodb,ssh,hf,chroma,lancedb,qdrant,nextcloud,databricks,email,parquet,pdf,hdf5]`; `httpx>=0.27`; `uvicorn>=0.30`. KHÔNG thêm `camel`/`fuse`/adapter agent-framework.
- `manifest.yaml`: `resource.memory: 1073741824`; `permission.tool.enabled: true`; không khai `storage`. Python 3.12.
- Plugin process KHÔNG được chạy asyncio event loop (mọi async ở daemon). Plugin chỉ dùng httpx sync + `mirage.config.load_config` (import an toàn, không chạy loop).
- **Mặc định READ:** `build_config_dict` parse YAML thô và `setdefault("mode","READ")` trước `load_config`. User muốn ghi phải khai `mode: WRITE`.
- Secret chỉ ở credentials (`.env`), resolve `${...}` client-side, gửi daemon qua **127.0.0.1** (không rời host). Không log secret.
- Keying: `workspace_id = "ws-" + sha256(workspace_yaml + sorted(env items) + cache_backend + redis_url + conversation_id).hexdigest()[:32]`. `conversation_id = self.session.conversation_id or self.session.message_id or "global"`.
- Hằng số: idle-TTL 600s, LRU cap 6, cache_limit "128MB", reaper 60s, command timeout (httpx) 110s, daemon startup_timeout 40s.
- Pool/daemon là best-effort: mọi thao tác phải chạy được từ trạng thái nguội (daemon chưa spawn / workspace chưa tạo → tự tạo). Bắt lỗi kết nối/404 → respawn + recreate rồi thử lại.
- Test: chỉ workspace RAM (không cần secret/mạng). Daemon integration tests spawn daemon THẬT trên **cổng test riêng** và shutdown ở teardown.

---

### Task 1: Setup — deps, git, manifest, conftest (gevent-faithful), smoke

**Files:**
- Modify: `requirements.txt`, `manifest.yaml` (đã scaffold sẵn — xác nhận đúng)
- Create: `conftest.py` (root), `tests/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: venv cài được deps; `import dify_plugin`, `from mirage.cli.client import make_client, DaemonClient`, `from mirage.config import load_config` chạy được. Root `conftest.py` patch gevent (faithful) + đặt cổng daemon test.

- [ ] **Step 1: Git baseline (repo đã init sẵn ở scaffold)**

```bash
cd /home/pc1175/Code/Project/dify-plugins/mirage-plugin-v2
git add -A && git commit -q -m "chore: scaffold + specs/plans" 2>/dev/null || true
git checkout -q -b feat/v2 2>/dev/null || git checkout -q feat/v2
```

- [ ] **Step 2: venv + cài deps**

```bash
python3.12 -m venv .venv && .venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -c "import uvicorn, httpx; from mirage.cli.client import make_client; print('ok')"
```
Expected: `ok`. Nếu `uvicorn`/`httpx` thiếu (mirage không kéo) thì đã có trong requirements — cài lại.

- [ ] **Step 3: conftest.py (root) — chạy test faithful dưới gevent + cổng daemon riêng**

```python
import os
# Simulate the real plugin process: dify_plugin patches gevent at import.
# Do this BEFORE anything spawns the daemon so the client side is tested under gevent.
import dify_plugin  # noqa: F401,E402

# Point the daemon client at a dedicated TEST port so tests never touch a real daemon.
# (verify env var name in mirage/cli/settings or load_daemon_settings; default port 8765)
os.environ.setdefault("MIRAGE_DAEMON_URL", "http://127.0.0.1:8799")
```
Ghi chú: implementer xác minh tên env var mà `load_daemon_settings()` đọc (mở `mirage/cli/` — có thể là `MIRAGE_DAEMON_URL` hoặc field trong DaemonSettings). Nếu khác, sửa cho đúng.

- [ ] **Step 4: smoke test**

`tests/test_smoke.py`:
```python
def test_imports():
    import dify_plugin  # noqa
    from mirage.cli.client import make_client, DaemonClient  # noqa
    from mirage.config import load_config  # noqa
    import httpx, uvicorn  # noqa

def test_build_ram_config_dict():
    from mirage.config import load_config
    cfg = load_config({"mode": "WRITE", "mounts": {"/data": {"resource": "ram"}}}, env={})
    d = cfg.to_workspace_kwargs()
    assert "cache" in d or "mode" in d  # sanity: config validates & dumps
    assert cfg.model_dump(mode="json")["mounts"]["/data"]["resource"] == "ram"
```

- [ ] **Step 5: chạy smoke**

Run: `.venv/bin/python -m pytest tests/test_smoke.py -v`
Expected: 2 passed. Nếu fail vì gevent-import gây treo → dừng, báo BLOCKED (v2 giả định client-side an toàn dưới gevent; nếu không thì cả hướng B sai).

- [ ] **Step 6: commit**

```bash
git add -A && git commit -m "chore: deps, gevent-faithful conftest, smoke tests"
```

---

### Task 2: `tools/_env.py` — parse `.env`

**Files:** Create `tools/_env.py`; Test `tests/tools/test_env.py`

**Interfaces:** Produces `parse_env_block(text: str) -> dict[str,str]` (bỏ blank/`#`, split `=` đầu, strip key+value, bỏ 1 cặp nháy bao value, bỏ key rỗng).

- [ ] **Step 1: test thất bại**

`tests/tools/test_env.py`:
```python
from tools._env import parse_env_block

def test_basic(): assert parse_env_block("A=1\nB=two") == {"A": "1", "B": "two"}
def test_comments_blank(): assert parse_env_block("# c\n\nA=1\n") == {"A": "1"}
def test_equals_and_quotes():
    assert parse_env_block('U=redis://h:6379/0\nT="x=y"') == {"U": "redis://h:6379/0", "T": "x=y"}
def test_strip(): assert parse_env_block("  A = 1 ") == {"A": "1"}
def test_empty(): assert parse_env_block("") == {}
```

- [ ] **Step 2: chạy fail** → `.venv/bin/python -m pytest tests/tools/test_env.py -v` → ModuleNotFoundError.

- [ ] **Step 3: cài đặt** `tools/_env.py`:
```python
def parse_env_block(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip(); v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            result[k] = v
    return result
```

- [ ] **Step 4: chạy pass** (5 passed).
- [ ] **Step 5: commit** `git add tools/_env.py tests/tools/test_env.py && git commit -m "feat: .env parser"`

---

### Task 3: `tools/_config.py` — build config dict + workspace id

**Files:** Create `tools/_config.py`; Test `tests/tools/test_config.py`

**Interfaces:**
- `build_config_dict(workspace_yaml: str, env: dict[str,str], cache_backend: str="ram", redis_url: str|None=None, cache_limit: str="128MB") -> dict` — parse YAML thô, `setdefault("mode","READ")`, `load_config(raw, env)`, ghi đè cache/index theo backend, trả `cfg.model_dump(mode="json")`.
- `make_workspace_id(workspace_yaml: str, env: dict[str,str], cache_backend: str, redis_url: str|None, conversation_id: str) -> str`.

- [ ] **Step 1: test thất bại**

`tests/tools/test_config.py`:
```python
from tools._config import build_config_dict, make_workspace_id

RAM = "mounts:\n  /data: {resource: ram}\n"
RAM_W = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"

def test_default_mode_read():
    d = build_config_dict(RAM, env={})
    assert d["mode"] == "READ"          # ép read-only khi YAML không khai

def test_explicit_write_kept():
    d = build_config_dict(RAM_W, env={})
    assert d["mode"] == "WRITE"

def test_secret_interpolation():
    d = build_config_dict(
        "mounts:\n  /s3: {resource: s3, config: {bucket: b, aws_access_key_id: ${AK}, aws_secret_access_key: ${SK}}}\n",
        env={"AK": "akid", "SK": "secret"})
    cfg_s3 = d["mounts"]["/s3"]["config"]
    assert cfg_s3["aws_access_key_id"] == "akid"    # ${AK} resolved

def test_id_stable_and_conversation_scoped():
    a = make_workspace_id(RAM, {}, "ram", None, "conv1")
    b = make_workspace_id(RAM, {}, "ram", None, "conv1")
    c = make_workspace_id(RAM, {}, "ram", None, "conv2")
    assert a == b and a != c and a.startswith("ws-")
```
Ghi chú: nếu `model_dump` che secret (SecretStr → "**********"), đổi assert của `test_secret_interpolation` sang kiểm tra id đổi theo env thay vì đọc giá trị secret; nhưng interpolation phải xảy ra trước validate nên giá trị vào config là plaintext — kiểm tra thực tế và điều chỉnh.

- [ ] **Step 2: chạy fail.**

- [ ] **Step 3: cài đặt** `tools/_config.py`:
```python
import hashlib
import yaml
from mirage.config import load_config
from mirage.cache.file.config import CacheConfig, RedisCacheConfig
from mirage.cache.index.config import IndexConfig, RedisIndexConfig


def build_config_dict(workspace_yaml, env, cache_backend="ram",
                      redis_url=None, cache_limit="128MB"):
    raw = yaml.safe_load(workspace_yaml) or {}
    if isinstance(raw, dict):
        raw.setdefault("mode", "READ")
    cfg = load_config(raw, env=env)
    d = cfg.model_dump(mode="json")
    if cache_backend == "redis":
        if not redis_url:
            raise ValueError("cache_backend=redis requires redis_url")
        d["cache"] = {"type": "redis", "url": redis_url, "limit": cache_limit}
        d["index"] = {"type": "redis", "url": redis_url}
    else:
        d["cache"] = {"type": "ram", "limit": cache_limit}
        d["index"] = {"type": "ram"}
    return d


def make_workspace_id(workspace_yaml, env, cache_backend, redis_url, conversation_id):
    h = hashlib.sha256()
    h.update(workspace_yaml.encode()); h.update(b"\0")
    for k in sorted(env):
        h.update(f"{k}={env[k]}".encode()); h.update(b"\0")
    h.update((cache_backend or "ram").encode()); h.update(b"\0")
    h.update((redis_url or "").encode()); h.update(b"\0")
    h.update((conversation_id or "global").encode())
    return "ws-" + h.hexdigest()[:32]
```
Ghi chú: implementer xác minh khoá cache/index dict khớp `WorkspaceConfig` schema (`CacheBlock`/`IndexBlock` — mở `mirage/config.py`). Nếu schema khác (vd field tên khác), chỉnh `d["cache"]/d["index"]` cho khớp; hoặc dựng qua `RamCacheBlock(...).model_dump()`. Cache override phải để daemon nhận đúng.

- [ ] **Step 4: chạy pass** (4 passed) — có thể phải chỉnh theo schema thực.
- [ ] **Step 5: commit.**

---

### Task 4: `tools/_daemon.py` — DaemonManager: ensure_daemon + ensure_workspace + execute

**Files:** Create `tools/_daemon.py`; Test `tests/tools/test_daemon_execute.py`

**Interfaces:**
- `class DaemonManager.__init__(self, *, idle_ttl=600.0, max_workspaces=6, clock=time.monotonic, client=None)` (client=None → `make_client()`).
- `ensure_daemon(self) -> None` (idempotent, lock, `client.ensure_running(startup_timeout=40, allow_spawn=True)`).
- `ensure_workspace(self, wid: str, config: dict) -> None` (POST create; 201 hoặc 409 = OK; cập nhật last_used).
- `execute(self, wid: str, command: str, timeout=110.0) -> tuple[str,str,int]`.
- Nội bộ: `_request_json(method, path, **kw)`, retry-on-cold (bắt lỗi kết nối/404 → ensure_daemon + tạo lại).

- [ ] **Step 1: test thất bại (integration — daemon thật, RAM)**

`tests/tools/test_daemon_execute.py`:
```python
import pytest
from tools._daemon import DaemonManager
from tools._config import build_config_dict, make_workspace_id

RAM_W = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"

@pytest.fixture
def mgr():
    m = DaemonManager()
    m.ensure_daemon()
    yield m
    m.shutdown()  # DELETE tracked workspaces + stop reaper (Task 5); daemon self-exits when empty

def test_execute_reuse(mgr):
    cfg = build_config_dict(RAM_W, env={})
    wid = make_workspace_id(RAM_W, {}, "ram", None, "conv1")
    mgr.ensure_workspace(wid, cfg)
    _, _, c1 = mgr.execute(wid, "echo hi > /data/x")
    out, _, c2 = mgr.execute(wid, "cat /data/x")   # reuse: read what write left
    assert c1 == 0 and c2 == 0 and out.strip() == "hi"

def test_default_read_blocks_write(mgr):
    ro = "mounts:\n  /data: {resource: ram}\n"
    cfg = build_config_dict(ro, env={})              # no mode -> READ
    wid = make_workspace_id(ro, {}, "ram", None, "convRO")
    mgr.ensure_workspace(wid, cfg)
    _, _, code = mgr.execute(wid, "echo hi > /data/x")
    assert code != 0

def test_conversation_isolation(mgr):
    cfg = build_config_dict(RAM_W, env={})
    w1 = make_workspace_id(RAM_W, {}, "ram", None, "A"); mgr.ensure_workspace(w1, cfg)
    w2 = make_workspace_id(RAM_W, {}, "ram", None, "B"); mgr.ensure_workspace(w2, cfg)
    mgr.execute(w1, "echo one > /data/f")
    out, _, code = mgr.execute(w2, "cat /data/f")    # separate workspace -> file absent
    assert code != 0                                  # isolated (no such file)
```
Ghi chú: cần `shutdown()` (Task 5) tồn tại tối thiểu từ Task 4 để fixture teardown; thêm bản tối thiểu ở Step 3 (DELETE các wid đã track). Chạy test **foreground**; daemon spawn lần đầu có thể mất ~10-20s (import mirage) — đặt startup_timeout 40s. KHÔNG chạy test ở background rồi poll.

- [ ] **Step 2: chạy fail** → ModuleNotFoundError.

- [ ] **Step 3: cài đặt** `tools/_daemon.py` (phần Task 4):
```python
import threading
import time
import httpx
from mirage.cli.client import make_client, DaemonUnreachable


class DaemonManager:
    def __init__(self, *, idle_ttl=600.0, max_workspaces=6,
                 clock=time.monotonic, client=None):
        self._client = client if client is not None else make_client()
        self._lock = threading.Lock()
        self._daemon_lock = threading.Lock()
        self._last_used: dict[str, float] = {}
        self._idle_ttl = idle_ttl
        self._max = max_workspaces
        self._clock = clock

    def ensure_daemon(self) -> None:
        with self._daemon_lock:
            self._client.ensure_running(startup_timeout=40.0, allow_spawn=True)

    def _req(self, method, path, **kw):
        r = self._client.request(method, path, **kw)
        return r

    def ensure_workspace(self, wid: str, config: dict) -> None:
        r = self._req("POST", "/v1/workspaces",
                      json={"config": config, "id": wid})
        if r.status_code not in (201, 409):
            raise RuntimeError(f"create workspace failed: {r.status_code} {r.text[:200]}")
        with self._lock:
            self._last_used[wid] = self._clock()

    def execute(self, wid: str, command: str, timeout: float = 110.0):
        with self._lock:
            self._last_used[wid] = self._clock()
        r = self._req("POST", f"/v1/workspaces/{wid}/execute",
                      json={"command": command}, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"execute failed: {r.status_code} {r.text[:200]}")
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
        with self._lock:
            wids = list(self._last_used)
        for wid in wids:
            self.delete(wid)
```
Ghi chú: xác minh response execute có key `stdout/stderr/exit_code` (spike thấy `{"kind":"io","exit_code","stdout","stderr"}`). Nếu daemon trả stream/khác, chỉnh parse.

- [ ] **Step 4: chạy pass** (3 passed) → `.venv/bin/python -m pytest tests/tools/test_daemon_execute.py -v` (foreground).
- [ ] **Step 5: commit.**

---

### Task 5: `tools/_daemon.py` — client-side eviction + reaper + retry-on-cold + singleton

**Files:** Modify `tools/_daemon.py`; Test `tests/tools/test_daemon_evict.py`

**Interfaces:**
- `_evict_idle(self)` — DELETE workspace `now-last_used>idle_ttl`.
- `_enforce_cap(self)` — vượt cap → DELETE cái `last_used` nhỏ nhất.
- `ensure_workspace`/`execute` gọi `_evict_idle()` + `_enforce_cap()` (evict đầu, cap sau ensure).
- `start_reaper(self, interval=60.0)` (greenlet/thread daemon) + dừng trong `shutdown`.
- **Retry-on-cold**: `execute`/`ensure_workspace` bắt `httpx.ConnectError`/404 → `ensure_daemon()` + `ensure_workspace()` (cho execute) rồi thử lại 1 lần.
- Module-level `MANAGER = DaemonManager()` + `MANAGER.start_reaper()` + `atexit.register(MANAGER.shutdown)`.

- [ ] **Step 1: test thất bại (fake clock — không cần daemon cho evict logic; dùng client giả)**

`tests/tools/test_daemon_evict.py`:
```python
from tools._daemon import DaemonManager

class FakeResp:
    def __init__(self, code=201): self.status_code = code; self.text = ""
    def json(self): return {"exit_code": 0, "stdout": "", "stderr": ""}

class FakeClient:
    def __init__(self): self.deleted = []
    def ensure_running(self, **k): pass
    def request(self, method, path, **k):
        if method == "DELETE": self.deleted.append(path)
        return FakeResp(200 if method != "POST" or "execute" in path else 201)

class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def adv(self, d): self.t += d

def test_idle_evict_deletes():
    clk = Clock(); fc = FakeClient()
    m = DaemonManager(idle_ttl=100.0, client=fc, clock=clk)
    m.ensure_workspace("ws-a", {}); clk.adv(101); m._evict_idle()
    assert any("ws-a" in p for p in fc.deleted)

def test_lru_cap_deletes_oldest():
    clk = Clock(); fc = FakeClient()
    m = DaemonManager(max_workspaces=2, client=fc, clock=clk)
    m.ensure_workspace("ws-a", {}); clk.adv(1)
    m.ensure_workspace("ws-b", {}); clk.adv(1)
    m.ensure_workspace("ws-c", {})   # over cap -> evict ws-a
    assert any("ws-a" in p for p in fc.deleted)
```

- [ ] **Step 2: chạy fail** (AttributeError `_evict_idle`).

- [ ] **Step 3: cài đặt** — thêm `import atexit` + methods; sửa `ensure_workspace`/`execute` để gọi evict/cap; retry-on-cold; singleton. (Evict/cap: gom wid quá hạn TRONG lock, DELETE NGOÀI lock — như v1.) Ví dụ:
```python
    def _evict_idle(self):
        now = self._clock()
        with self._lock:
            stale = [w for w, t in self._last_used.items() if now - t > self._idle_ttl]
        for w in stale:
            self.delete(w)

    def _enforce_cap(self):
        with self._lock:
            if len(self._last_used) <= self._max:
                return
            ordered = sorted(self._last_used.items(), key=lambda kv: kv[1])
            drop = [w for w, _ in ordered[: len(self._last_used) - self._max]]
        for w in drop:
            self.delete(w)

    def start_reaper(self, interval=60.0):
        self._reaper_stop = threading.Event()
        def loop():
            while not self._reaper_stop.wait(interval):
                try: self._evict_idle()
                except Exception: pass
        self._reaper = threading.Thread(target=loop, name="mirage-v2-reaper", daemon=True)
        self._reaper.start()
```
`ensure_workspace`: gọi `self._evict_idle()` đầu; sau khi tạo xong gọi `self._enforce_cap()`. `shutdown`: set `_reaper_stop` nếu có, rồi DELETE hết. Retry-on-cold trong `execute`: bọc `try/except (httpx.ConnectError, RuntimeError-404)` → `self.ensure_daemon(); self.ensure_workspace(wid, ...)`... (execute cần config để tạo lại — chấp nhận: nếu 404 workspace, ném lỗi rõ để tool-layer tạo lại; hoặc execute nhận thêm `config` optional để tự tạo lại). **Quyết định:** cho `execute(wid, command, config=None, timeout=...)`; nếu 404 và có config → ensure_workspace rồi thử lại. Cập nhật interface + test tương ứng.
Cuối file: `MANAGER = DaemonManager(); MANAGER.start_reaper(); atexit.register(MANAGER.shutdown)`.

- [ ] **Step 4: chạy pass** (2 passed) + full `tests/tools/` (foreground).
- [ ] **Step 5: commit.**

---

### Task 6: `tools/_daemon.py` — snapshot (POST → đọc file → bytes, gzip client-side)

**Files:** Modify `tools/_daemon.py`; Test `tests/tools/test_daemon_snapshot.py`

**Interfaces:** `snapshot(self, wid: str, compress: str|None=None, timeout=110.0) -> bytes` — POST `/v1/workspaces/{wid}/snapshot` (body có `path` tương đối, vd `f"{wid}.tar"`), nhận `{path,size}`, đọc file tại `path` → bytes; nếu `compress=="gz"` → gzip bytes ở client.

- [ ] **Step 1: test thất bại (daemon thật, RAM)**

`tests/tools/test_daemon_snapshot.py`:
```python
import gzip, pytest
from tools._daemon import DaemonManager
from tools._config import build_config_dict, make_workspace_id

RAM_W = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"

@pytest.fixture
def mgr():
    m = DaemonManager(); m.ensure_daemon(); yield m; m.shutdown()

def test_snapshot_tar_bytes(mgr):
    cfg = build_config_dict(RAM_W, env={}); wid = make_workspace_id(RAM_W, {}, "ram", None, "snap")
    mgr.ensure_workspace(wid, cfg); mgr.execute(wid, "echo hi > /data/x")
    blob = mgr.snapshot(wid)
    assert isinstance(blob, bytes) and len(blob) > 0

def test_snapshot_gz(mgr):
    cfg = build_config_dict(RAM_W, env={}); wid = make_workspace_id(RAM_W, {}, "ram", None, "snapgz")
    mgr.ensure_workspace(wid, cfg)
    blob = mgr.snapshot(wid, compress="gz")
    assert blob[:2] == b"\x1f\x8b" and len(gzip.decompress(blob)) > 0
```

- [ ] **Step 2: chạy fail** (AttributeError snapshot).

- [ ] **Step 3: cài đặt** — thêm:
```python
    def snapshot(self, wid, compress=None, timeout=110.0):
        r = self._req("POST", f"/v1/workspaces/{wid}/snapshot",
                      json={"path": f"{wid}.tar"}, timeout=timeout)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"snapshot failed: {r.status_code} {r.text[:200]}")
        path = r.json()["path"]
        with open(path, "rb") as f:
            data = f.read()
        if compress == "gz":
            import gzip
            data = gzip.compress(data)
        return data
```
Ghi chú: xác minh `SnapshotWorkspaceRequest` field (`path`?) trong `mirage/server/schemas/workspaces.py`; daemon `_run_snapshot` KHÔNG nén → nén gz ở client (đã làm). File `path` là absolute (đọc cùng host được).

- [ ] **Step 4: chạy pass** (2 passed).
- [ ] **Step 5: commit.**

---

### Task 7: Provider — credentials + validation

**Files:** Create `provider/mirage.yaml`, `provider/mirage.py`; Test `tests/test_provider.py`

**Interfaces:** `MirageProvider._validate_credentials(credentials)` — `env` bắt buộc, parse được; nếu `cache_backend=="redis"` thì `redis_url` bắt buộc; lỗi → `ToolProviderCredentialValidationError`.

- [ ] **Step 1: `provider/mirage.yaml`** (identity + credentials_schema `env`/`cache_backend`/`redis_url`; `tools: [tools/execute.yaml, tools/snapshot.yaml]`; `extra.python.source: provider/mirage.py`). *(Dùng đúng cấu trúc v1 Task 6.)*

- [ ] **Step 2: test** `tests/test_provider.py`:
```python
import pytest
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from provider.mirage import MirageProvider

def _p(): return MirageProvider.__new__(MirageProvider)
def test_ok(): _p()._validate_credentials({"env": "A=1"})
def test_redis_needs_url():
    with pytest.raises(ToolProviderCredentialValidationError):
        _p()._validate_credentials({"env": "A=1", "cache_backend": "redis"})
def test_redis_ok(): _p()._validate_credentials({"env": "A=1", "cache_backend": "redis", "redis_url": "redis://h:6379/0"})
```

- [ ] **Step 3: chạy fail.**
- [ ] **Step 4: cài đặt** `provider/mirage.py` (như v1: parse_env_block, require env non-empty, redis→url). 
- [ ] **Step 5: chạy pass** (3 passed).
- [ ] **Step 6: commit.**

---

### Task 8: Tool `execute`

**Files:** Create `tools/execute.yaml`, `tools/execute.py`; Test `tests/tools/test_execute_tool.py`

**Interfaces:** `ExecuteTool._invoke(tool_parameters)` — đọc `workspace_yaml`,`command`; creds; `conv = self.session.conversation_id or self.session.message_id or "global"`; build config + wid; `MANAGER.ensure_daemon/ensure_workspace/execute`; yield text + json.

- [ ] **Step 1: `tools/execute.yaml`** — params `workspace_yaml` (form llm) + `command` (form llm); `llm_description` của `workspace_yaml` NÊU RÕ: *READ-only mặc định; thêm `mode: WRITE` để ghi*; `extra.python.source`.

- [ ] **Step 2: test (integration, RAM)** `tests/tools/test_execute_tool.py`:
```python
from tools.execute import ExecuteTool

def _tool(conv="c1"):
    t = ExecuteTool.__new__(ExecuteTool)
    class R: credentials = {"env": "", "cache_backend": "ram"}
    class S: conversation_id = conv; message_id = None
    t.runtime = R(); t.session = S()
    return t

def test_execute_tool_ram():
    t = _tool()
    msgs = list(t._invoke({
        "workspace_yaml": "mode: WRITE\nmounts:\n  /data: {resource: ram}\n",
        "command": "echo hi > /data/x && cat /data/x",
    }))
    assert any("hi" in str(getattr(m, "message", m)) for m in msgs)
```
Ghi chú: nếu `create_text_message`/`create_json_message` cần runtime đầy đủ → fallback: gọi `MANAGER` trực tiếp trong test và chỉ smoke `_invoke` (báo lại). Cần daemon (fixture/conftest lo spawn) — teardown gọi `MANAGER.shutdown()`.

- [ ] **Step 3: chạy fail.**
- [ ] **Step 4: cài đặt** `tools/execute.py` (theo spec 6.5): parse creds → conv → build_config_dict → make_workspace_id → ensure_daemon → ensure_workspace → execute → yield; bọc try/except trả text lỗi.
- [ ] **Step 5: chạy pass.**
- [ ] **Step 6: commit.**

---

### Task 9: Tool `snapshot`

**Files:** Create `tools/snapshot.yaml`, `tools/snapshot.py`; Test `tests/tools/test_snapshot_tool.py`

**Interfaces:** `SnapshotTool._invoke` — `workspace_yaml` + `compress` (none|gz); ensure ws; `blob = MANAGER.snapshot(wid, compress)`; `create_blob_message(blob, meta)`.

- [ ] **Step 1: `tools/snapshot.yaml`** (params `workspace_yaml` form llm + `compress` select form form; ghi chú live-only bỏ qua).
- [ ] **Step 2: test (RAM, daemon)** — snapshot ra bytes, gz có magic `1f 8b`.
- [ ] **Step 3: chạy fail.**
- [ ] **Step 4: cài đặt** `tools/snapshot.py` (build config + wid + ensure + snapshot → blob message; mime `application/x-tar` / `application/gzip`).
- [ ] **Step 5: chạy pass.**
- [ ] **Step 6: commit.**

---

### Task 10: Wiring + packaging + full-suite gate

**Files:** verify `manifest.yaml`, `provider/mirage.yaml`.

- [ ] **Step 1:** xác nhận `manifest.plugins.tools = [provider/mirage.yaml]`, provider liệt kê cả 2 tool.
- [ ] **Step 2: import-check**: `.venv/bin/python -c "import provider.mirage, tools.execute, tools.snapshot, tools._daemon, tools._config, tools._env; print('imports ok')"`.
- [ ] **Step 3: FULL suite (gate quan trọng — dưới gevent)**: `.venv/bin/python -m pytest -v` (foreground). Tất cả PASS; đặc biệt không treo (chứng minh client-side an toàn dưới gevent). Nếu treo → điều tra như v1 (faulthandler).
- [ ] **Step 4: package thử**: `../dify-plugin plugin package ./ 2>&1 | tail -20` → tạo `.difypkg` không lỗi.
- [ ] **Step 5: commit.**

---

## Ghi chú vận hành (không phải task)
- **Điều kiện tiên quyết B:** deployment Dify phải cho plugin spawn subprocess + bind 127.0.0.1 port. Nếu không → chuyển Option A (xem `docs/superpowers/BLOCKER-gevent-asyncio.md`).
- **Verify backend thật:** trước khi release, test với S3/bucket thật qua credentials `.env` (daemon chạy asyncio thuần nên kỳ vọng OK) — làm thủ công ngoài CI.
- **Snapshot cùng host:** plugin đọc file daemon ghi ra; đúng vì daemon là subprocess cùng máy.
- **An toàn:** mặc định READ; `mode: WRITE` để ghi. Nêu trong tài liệu marketplace.
