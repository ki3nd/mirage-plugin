# BLOCKER: dify_plugin ép gevent, xung đột với mô hình async của mirage

**Ngày phát hiện:** 2026-07-02 (trong lúc tích hợp Task 6/7).
**Mức độ:** Nghiêm trọng — chặn kiến trúc "nhúng in-process" hiện tại của plugin.
**Trạng thái:** ✅ ĐÃ CÓ HƯỚNG GIẢI (2026-07-03) — Option A (bridge asyncio↔gevent) đã spike thành công. Xem mục dưới.

---

## ✅ GIẢI PHÁP CHỐT — Option A: in-process bridge asyncio↔gevent

Đã spike thành công (RAM workspace): reuse qua các call + concurrency (serialized) đều chạy, không deadlock.

Công thức:
```python
import asyncio_gevent                                   # thêm vào requirements
asyncio.set_event_loop_policy(asyncio_gevent.EventLoopPolicy())
LOOP = asyncio.new_event_loop(); asyncio.set_event_loop(LOOP)   # 1 loop gevent-backed, sống suốt process
LOCK = gevent.lock.BoundedSemaphore(1)

def run(coro):
    with LOCK:                       # serialize: 1 mirage op tại 1 thời điểm
        return LOOP.run_until_complete(coro)
```
Vì sao chạy: loop là gevent-backed (chạy trên hub) → KHÔNG phải asyncio-loop-trong-OS-thread (thứ deadlock). Loop giữ nguyên → connection/cache tái dùng.

**Thay đổi so với code Task 3–5 (giữ gần hết pool/eviction/keying):**
1. Bỏ `mirage.WorkspaceRunner` (thread) → module bridge `tools/_loop.py` giữ LOOP + LOCK + `run(coro)`.
2. `execute`/`snapshot`/`close` dùng `run(coro)` thay `runner.call_sync`.
3. Key pool += `conversation_id` (xem D1) → mỗi hội thoại 1 workspace; **bỏ ensure-session** (dùng default session).
4. Reaper (greenlet) đóng workspace qua `run(ws.close())`.

**Concurrency:** serialize toàn cục qua LOCK ⇒ Race D (execute song song cùng workspace) tự hết; dict-lock manager lo Race A/B/C. Đánh đổi: mọi lệnh mirage chạy tuần tự trong process (chấp nhận v1; nâng cấp `run_forever`+task ở v2 nếu cần song song thật).

**CÒN PHẢI KIỂM CHỨNG trước khi tin hoàn toàn:** mới test RAM (offline). Phải thử **backend mạng thật (S3/aioboto3, redis.asyncio)** trên loop gevent với cred thật. Nếu fail → rớt về Option B (daemon subprocess, mục dưới).

Spike script tham chiếu: `docs/superpowers/spikes/gevent_asyncio_bridge_spike.py` (phiên làm việc 2026-07-03).

---

## Tình trạng tiến độ khi dừng
- Task 1–5: xong + đã review sạch (deps/setup, `.env` parser, `WorkspaceManager` build+reuse, `execute`+ensure-session, eviction+reaper+singleton). Commits `cd879e1`..`4ac0efd`.
- Task 6 (provider): commit `62921bf`, 3/3 test provider pass.
- Task 7 (execute tool), 8 (snapshot tool), 9 (wiring/package): **chưa làm.**
- Nhánh: `feat/mirage-plugin`. Cây git sạch.

## Vấn đề
`dify_plugin/__init__.py` dòng 1 import `_gevent` → gọi `gevent.monkey.patch_all(sys=True)` **ngay khi import**. Ở runtime plugin thật, `threading`/`asyncio` bị monkey-patch.

`WorkspaceManager` hiện dựa trên `mirage.WorkspaceRunner` = chạy một asyncio event loop trong `threading.Thread` + gọi chéo bằng `run_coroutine_threadsafe(...).result(timeout)`. Dưới gevent:
- `threading.Thread` thành greenlet; `loop.run_forever()` block trên epoll không nhường hub → **deadlock**.

## Bằng chứng
1. Chạy từng file `tests/tools/*.py` riêng: PASS (0.7s) — vì không import `dify_plugin`.
2. Full suite `pytest -q` (có `tests/test_provider.py` import `dify_plugin` chạy trước): **treo** tại `test_workspace_build.py::test_different_yaml_different_runner`. faulthandler cho thấy main thread kẹt ở `gevent/hub.py:647`.
3. Repro (`import dify_plugin` rồi chạy mirage RAM execute):
   - Approach A (mirage `WorkspaceRunner`): deadlock, không trả kể cả `call_sync(timeout=8)`.
   - Approach B (asyncio loop trên native OS thread qua `gevent.monkey.get_original("threading","Thread")`): `RuntimeError: Cannot run the event loop while another loop is running`.

## Hệ quả
Toàn bộ cách tiếp cận "nhúng mirage in-process + WorkspaceRunner-thread" (Task 3–5) **không chạy được dưới runtime gevent của Dify**. Code Task 3–5 vẫn đúng về logic (test đơn lẻ pass) nhưng không dùng được ở production như hiện tại.

## Các hướng cần đánh giá khi resume (chưa quyết)
1. **Cầu asyncio↔gevent in-process:** dùng `asyncio_gevent` (CHƯA cài; thêm dependency) để asyncio chạy trên gevent hub, rồi drive coroutine của mirage từ greenlet `_invoke`. Giữ được thiết kế nhúng. Cần valid: mirage có chạy đúng trên loop gevent không, connection-reuse còn không.
2. **Mirage chạy như daemon/subprocess riêng (KHUYẾN NGHỊ đánh giá trước):** mirage có sẵn `mirage/server/`, `mirage/cli/`, và console script `mirage`. Chạy daemon ở process riêng (không gevent), plugin thành client gọi qua HTTP/socket (sync — gevent làm cooperative, OK). Sidestep hoàn toàn gevent. Redesign lớn hơn nhưng khớp đúng kiến trúc mirage ("CLI + daemon"). Phải xử lý vòng đời daemon + workspace_id + truyền secret.
3. **Đào tiếp fix native-thread:** hiểu vì sao Approach B báo "another loop is running" (có thể gevent cài policy asyncio global). Rủi ro cao, có thể không có lời giải sạch.

## Quyết định thiết kế chốt trong lúc pause

### D1 — Keying theo conversation (2026-07-03)
- `self.session.conversation_id` CÓ THẬT và ổn định theo hội thoại (`dify_plugin/core/runtime.py:130-163`; nguồn từ payload request, `plugin.py:557-568`). Ngược lại `session.session_id`/`runtime.session_id` là uuid MỚI mỗi request (`plugin.py:554`) — không dùng làm khoá hội thoại.
- **Chốt:** key pool = `sha256(workspace_yaml + env + cache_backend + redis_url + conversation_id)`. Mỗi (conversation × config) một Workspace riêng; trong hội thoại tái dùng, khác hội thoại KHÔNG share.
- `_invoke` lấy: `conv = self.session.conversation_id or self.session.message_id or "global"`.
- Hệ quả: **bỏ multiplex mirage-session** (Task 4 cũ) — mỗi Workspace = một conversation → dùng default session; bỏ ensure-session. Đơn giản hoá code.
- (think-tool đọc `runtime.session_id` là chưa đúng — đừng bắt chước chỗ đó.)

## Việc cần làm khi quay lại
- Chọn hướng (1/2/3) → cập nhật spec (`docs/superpowers/specs/...`) & plan (`docs/superpowers/plans/...`).
- Nếu hướng 2: viết lại Task 3–5 (bỏ WorkspaceRunner-in-thread), thêm quản lý daemon.
- Nếu hướng 1: thêm `asyncio_gevent` vào requirements, đổi `WorkspaceManager` để dùng loop gevent thay vì runner-thread; giữ lại phần lớn logic pool/eviction.
- Chạy lại full suite `pytest -q` (KHÔNG chỉ file lẻ) như một cổng kiểm tra — full suite phải xanh dưới gevent.

---

## Option B (daemon subprocess) — FEASIBILITY ĐÃ XÁC MINH (2026-07-03)

Spike `docs/superpowers/spikes/daemon_option_b_spike.py` (mô phỏng plugin dưới gevent): spawn `uvicorn mirage.server.daemon:app` qua `DaemonClient.ensure_running()` → tạo RAM workspace (`POST /v1/workspaces {config,id}`) → execute 2 lệnh (`POST /v1/workspaces/{id}/execute {command,session_id?}`) → call 2 đọc được cái call 1 ghi (reuse OK). Tất cả chạy dưới gevent-patched parent.

Ưu điểm lớn của B: daemon là process asyncio thuần (KHÔNG gevent) ⇒ backend mạng (aioboto3/redis.asyncio) chắc chắn chạy — giải rủi ro lớn nhất còn lại của Option A.

Chi tiết API daemon:
- `WorkspaceRegistry`: map `workspace_id -> WorkspaceRunner` (một daemon host nhiều workspace). `register(config, workspace_id=...)` đặt id cố định.
- Create: `POST /v1/workspaces` body `{config: <WorkspaceConfig dump>, id}`. Env `${}` interpolation làm CLIENT-side (`load_config(yaml, env)` rồi `.model_dump()`), gửi config đã resolve qua localhost.
- Execute: `POST /v1/workspaces/{id}/execute` body `{command, session_id?}` → JSON `{kind:"io", exit_code, stdout, stderr}` (đã materialize). `session_id` cũng KHÔNG tự tạo (phải tạo qua sessions router trước, hoặc bỏ trống dùng default).
- Snapshot: `POST /v1/workspaces/{id}/snapshot` → ghi tar ra `snapshot_root`, trả `{path,size}` (KHÔNG trả bytes → plugin phải đọc file, cùng host).
- Delete: `DELETE /v1/workspaces/{id}`.
- Auth: Bearer token (`DaemonClient` tự lo qua auth_storage).
- Idle: daemon chỉ tự tắt khi registry RỖNG > `idle_grace_seconds` (30s); KHÔNG evict từng workspace → client tự DELETE workspace nhàn rỗi (giữ TTL/LRU của WorkspaceManager, đổi close→DELETE).

### Nhược điểm/nhiệm vụ của B
- Client-side eviction (DELETE idle workspaces) — vẫn cần WorkspaceManager-style TTL/LRU.
- Snapshot: daemon ghi file → plugin đọc file (cần snapshot_root chung).
- Vòng đời: daemon tự tắt khi rỗng → respawn trong suốt (`ensure_running`). Cần lo daemon chết giữa chừng.
- RỦI RO PROD: sandbox Dify thật có cho spawn subprocess + bind cổng localhost không (local: OK).

## So sánh A vs B (cả hai đã validated)
| Yếu tố | A (in-process bridge) | B (daemon) |
|---|---|---|
| gevent conflict | né bằng asyncio_gevent (lib niche) | LOẠI BỎ hẳn (daemon không gevent) |
| backend mạng | chưa test trên loop gevent | chắc chắn chạy (asyncio thuần) |
| process | 1 | 2 (plugin nhẹ + daemon nặng ≈ RAM tương đương) |
| code | giữ Task 3/5 + bridge | tái dùng registry/execute/sessions của mirage; plugin = thin client |
| eviction | in-proc (đã có) | client tự DELETE (giữ logic cũ) |
| snapshot | bytes in-proc (dễ) | daemon ghi file → plugin đọc |
| dep thêm | asyncio_gevent | không |
| rủi ro prod | network-on-gevent-loop | sandbox cho spawn subprocess+port? |
