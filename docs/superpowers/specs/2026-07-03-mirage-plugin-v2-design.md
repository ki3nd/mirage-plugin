# Mirage Dify Plugin v2 — Thiết kế (Option B: local daemon)

- **Ngày:** 2026-07-03
- **Tác giả:** ki3nd
- **Trạng thái:** Design — sẵn sàng lập plan
- **Kế thừa:** v1 (`../mirage-plugin`) đã phát hiện blocker gevent — xem `docs/superpowers/BLOCKER-gevent-asyncio.md`. v2 chọn Option B để né hẳn gevent.

## 1. Mục tiêu

Đưa [mirage](https://docs.mirage.strukto.ai) (Unified Virtual File System cho AI agent) thành Dify tool plugin: chạy lệnh bash (`ls`, `cat`, `grep`, pipe...) **xuyên nhiều resource** (S3, Slack, Redis, RAM, Disk...) qua tool `execute`, và xuất snapshot qua tool `snapshot`. Connection + cache được tái dùng giữa các lần gọi tool.

Ngoài phạm vi v1/v2: FUSE, restore/`load` snapshot, adapter agent-framework của mirage, OAuth.

## 2. Vì sao Option B (daemon), không nhúng in-process

`dify_plugin/__init__.py` gọi `gevent.monkey.patch_all()` **ngay khi import**. mirage là **async-native** (`Workspace.execute` là async; lõi dùng async generator + aioboto3/redis.asyncio/aiofiles). Chạy asyncio event loop **in-process** dưới gevent gây **deadlock** (đã kiểm chứng ở v1: WorkspaceRunner-thread và native-thread đều fail).

Option A (bridge `asyncio_gevent`) chạy được nhưng: phụ thuộc lib niche, backend mạng trên loop-gevent chưa kiểm chứng, phải serialize. **Option B chạy toàn bộ async của mirage trong một daemon process riêng (KHÔNG gevent)** → asyncio chạy bình thường, backend mạng chắc chắn hoạt động. mirage vốn thiết kế cho cách này ("CLI + daemon"); daemon + client HTTP là **con đường "sync" chính thức** của mirage.

**Điều kiện tiên quyết của B (đã kiểm chứng local, phải đúng ở deployment thật):** sandbox plugin phải cho phép (a) spawn subprocess, (b) bind cổng 127.0.0.1, (c) đủ RAM cho daemon. Nếu deployment siết các quyền này → phải quay lại Option A (giữ nguyên hồ sơ blocker để chuyển hướng).

## 3. Kiến trúc

```
Dify (agent / workflow)
  └─ Tool._invoke(workspace_yaml, command)        # process plugin (DƯỚI gevent), thin client
       └─ DaemonManager (singleton tầng module)
            ├─ ensure_daemon()  → spawn `uvicorn mirage.server.daemon:app` @127.0.0.1 (1 lần)
            ├─ ensure_workspace(wid, config)  → POST /v1/workspaces {config, id}  (idempotent)
            └─ execute(wid, command)          → POST /v1/workspaces/{wid}/execute {command}
                                                 (httpx SYNC — dưới gevent thành cooperative)
  ↕ localhost HTTP + Bearer token
  ┌─────────────────────────────────────────────────────────────┐
  │ mirage daemon (subprocess, KHÔNG gevent, asyncio thuần)       │
  │   WorkspaceRegistry: workspace_id -> WorkspaceRunner          │
  │   giữ connection + cache ấm; chạy mọi async của mirage        │
  └─────────────────────────────────────────────────────────────┘
```

**Nguyên tắc bất biến:** daemon + workspace là **cache best-effort**. Tính đúng không phụ thuộc chúng còn sống: daemon chết/registry rỗng-tự-tắt → `ensure_daemon` respawn; workspace thiếu → `ensure_workspace` tạo lại. Mọi `_invoke` phải chạy được từ trạng thái nguội.

## 4. Phân tách secret vs cấu trúc (giữ như v1)

- **Provider credentials** = kho secret dạng `.env` (mã hoá). Không lộ cho LLM.
- **Tool param `workspace_yaml`** = cấu trúc mounts; secret ghi bằng `${TÊN_VIẾT_HOA}`.
- Plugin ghép: `load_config(workspace_yaml, env=parse_env(credentials))` → resolve secret → `config_dict = cfg.model_dump(mode="json")` → gửi cho daemon qua **localhost** (secret không rời host).

## 5. Keying — mỗi (conversation × config) một workspace (Map 2, mặc định)

```
workspace_id = "ws-" + sha256(workspace_yaml + sorted(env) + cache_backend + redis_url + conversation_id)[:32]
```
- `conversation_id` lấy từ **`self.session.conversation_id`** (ổn định theo hội thoại; `dify_plugin/core/runtime.py`), fallback `self.session.message_id` → `"global"`. KHÔNG dùng `runtime.session_id` (uuid mỗi request).
- Mỗi hội thoại có workspace **riêng** trong daemon → cô lập hoàn toàn (cwd/env/history/dữ liệu). Dùng **default session** của workspace → **không cần tạo mirage session** (né giới hạn "session không auto-create" của daemon).
- **Alternative (Map 1, không mặc định):** `workspace_id = hash(...không có conversation)` + `session_id=conversation_id` qua sessions router → chia sẻ cache/dữ liệu giữa các hội thoại, chỉ tách cwd/env/history. Chỉ chọn khi mounts toàn remote/read-only và muốn tối đa chia sẻ cache. v2 mặc định Map 2 cho an toàn/đơn giản.

## 6. Thành phần

### 6.1 Provider — `provider/mirage.yaml` + `provider/mirage.py`
`credentials_schema`: `env` (secret-input, block `.env`, required); `cache_backend` (select ram|redis, default ram); `redis_url` (text, optional). `_validate_credentials`: parse `.env`; nếu `cache_backend=redis` thì `redis_url` bắt buộc. (Giống v1.)

### 6.2 `tools/_env.py`
`parse_env_block(text) -> dict[str,str]` — parse `KEY=VALUE` (giống v1: bỏ blank/`#`, split `=` đầu, strip, bỏ nháy bao).

### 6.3 `tools/_config.py`
- `build_config_dict(workspace_yaml: str, env: dict) -> dict` — `raw = yaml.safe_load(...)`, `raw.setdefault("mode","READ")` (mặc định read-only; user tự khai `mode: WRITE`), `cfg = load_config(raw, env=env)`, ghi đè cache/index theo `cache_backend`+`cache_limit`, trả `cfg.model_dump(mode="json")`.
- `make_workspace_id(workspace_yaml, env, cache_backend, redis_url, conversation_id) -> str`.

### 6.4 `tools/_daemon.py` — DaemonManager (singleton tầng module)
Trái tim của v2. Bọc `mirage.cli.client.DaemonClient` (đã có: `ensure_running` spawn `uvicorn`, `request`, auth token).
- `ensure_daemon()`: `client.ensure_running(startup_timeout=~40s, allow_spawn=True)`. Idempotent, có lock (greenlet-safe) để nhiều `_invoke` đồng thời không spawn trùng.
- `ensure_workspace(wid, config_dict)`: `POST /v1/workspaces {config, id:wid}`; coi **409 (đã tồn tại)** là thành công (idempotent). Cập nhật `last_used[wid]`.
- `execute(wid, command, timeout=110) -> (stdout, stderr, exit_code)`: `POST /v1/workspaces/{wid}/execute {command}`; parse JSON `{exit_code, stdout, stderr}`.
- `snapshot(wid, compress) -> bytes`: `POST /v1/workspaces/{wid}/snapshot {path}` → nhận `{path,size}` → **đọc file tại path** (cùng host) → bytes. (Daemon ghi ra file, không trả bytes.)
- `delete(wid)`: `DELETE /v1/workspaces/{wid}`.
- **Client-side eviction** (vì daemon KHÔNG evict từng workspace): reaper greenlet ~60s + gọi trong `_invoke`: DELETE workspace `now-last_used > idle_ttl` (600s), và enforce LRU cap (6) → DELETE cái cũ nhất. Concurrency-safe (lock quanh dict `last_used`; HTTP call ngoài lock).
- Xử lý daemon-đã-tắt (registry rỗng >30s): bắt lỗi kết nối/404 → `ensure_daemon()` + `ensure_workspace()` rồi thử lại (cold rebuild trong suốt).

### 6.5 Tool `execute` — `tools/execute.yaml` + `.py`
Params: `workspace_yaml` (string, form llm), `command` (string, form llm). `_invoke`:
1. `env = parse_env_block(creds["env"])`; `cache_backend`, `redis_url` từ creds.
2. `conv = self.session.conversation_id or self.session.message_id or "global"`.
3. `config = build_config_dict(workspace_yaml, env, cache_backend, redis_url)`; `wid = make_workspace_id(...)`.
4. `MANAGER.ensure_daemon(); MANAGER.ensure_workspace(wid, config)`; `out, err, code = MANAGER.execute(wid, command)`.
5. yield `create_text_message(out or err)` + `create_json_message({command, exit_code, stdout, stderr})`.
6. Lỗi (daemon/HTTP/timeout/config) → yield text lỗi rõ ràng, không raise.
7. `llm_description` nêu rõ: mặc định READ-only; thêm `mode: WRITE` khi cần ghi.

### 6.6 Tool `snapshot` — `tools/snapshot.yaml` + `.py`
Params: `workspace_yaml` + `compress` (none|gz). `_invoke`: ensure ws → `blob = MANAGER.snapshot(wid, compress)` → `create_blob_message(blob, meta={mime_type, filename})`. Ghi chú: bỏ qua resource live-only (Slack/Gmail) + file chưa đụng.

### 6.7 Deps / manifest
- `requirements.txt`: `dify_plugin>=0.7.4`, `mirage-ai[<backends>]` (cho daemon), `httpx>=0.27`, `uvicorn>=0.30`.
- `manifest.yaml`: `resource.memory: 1073741824` (khai báo; không enforce). Python 3.12. Không cần storage permission.

## 7. Xử lý lỗi & timeout
- httpx timeout 110s (< MAX_REQUEST_TIMEOUT=120). Hết giờ → text "command timed out".
- execute trả `exit_code≠0` → trả stderr; không raise.
- Daemon không lên (`DaemonUnreachable`) → text lỗi hướng dẫn (kiểm tra quyền spawn/port); không sập plugin.
- Config sai / thiếu `${}` → text lỗi rõ.

## 8. An toàn — mặc định READ
Plugin ép `mode: READ` khi YAML không khai (build_config `setdefault("mode","READ")`). Ghi/xoá chỉ khi user chủ động `mode: WRITE` (cấp workspace hoặc mount). Mount-level mode vẫn thắng.

## 9. Concurrency
- Nhiều `_invoke` đồng thời chia sẻ `MANAGER` (singleton) + dict `last_used`. Bảo vệ bằng lock (threading.Lock → cooperative dưới gevent); HTTP I/O ngoài lock.
- `ensure_daemon` có lock riêng để không spawn trùng.
- Concurrency của các workspace do **daemon** lo (nó chạy nhiều WorkspaceRunner song song, asyncio thuần) → plugin không cần serialize như Option A.
- Race D (execute song song 1 workspace) hiếm (Map 2: cùng workspace = cùng conversation bắn parallel tool-call); daemon xử lý qua session/loop của nó — chấp nhận cho v1, không tự serialize ở client.

## 10. Caveats
- **Điều kiện tiên quyết** (mục 2): spawn subprocess + localhost port + RAM. Nếu không có → Option A.
- Snapshot qua file cùng host (cần daemon `snapshot_root` đọc được từ plugin).
- Multi-node Dify: mỗi node một plugin-process + daemon riêng; workspace không share cross-node (chấp nhận).
- Daemon tự tắt khi registry rỗng >30s → respawn trong suốt lần gọi sau (chậm hơn ở lần đó).
- RAM: daemon nạp mirage[backends đang dùng] (lazy import); plugin-side nhẹ (chỉ `load_config`/httpx) → tổng ≈ một process nặng, không phải gấp đôi.

## 11. Cấu trúc file dự kiến
```
mirage-plugin-v2/
├── manifest.yaml, main.py, requirements.txt
├── provider/ mirage.yaml, mirage.py
├── tools/
│   ├── _env.py          # parse_env_block
│   ├── _config.py       # build_config_dict, make_workspace_id
│   ├── _daemon.py       # DaemonManager (singleton) + client-side eviction
│   ├── execute.yaml / execute.py
│   └── snapshot.yaml / snapshot.py
├── tests/ ...
└── _assets/icon.svg
```

## 12. Hằng số hardcode v1
| Tham số | Giá trị |
|---|---|
| idle-TTL (client evict) | 600s (10') |
| LRU cap | 6 workspace |
| cache_limit / workspace | 128MB |
| reaper interval | 60s |
| command timeout (httpx) | 110s |
| daemon startup_timeout | 40s |
| daemon idle self-shutdown | 600s (plugin set `MIRAGE_IDLE_GRACE_SECONDS=600` trước khi spawn; mặc định mirage 30s) — chỉ tính sau khi registry rỗng |
