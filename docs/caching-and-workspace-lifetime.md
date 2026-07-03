# Caching & workspace lifetime — notes and a Redis collision caveat

- **Ngày:** 2026-07-03
- **Phạm vi:** vòng đời workspace (khi không chạy command) + hành vi cache (RAM vs Redis), đặc biệt **rủi ro collision cache khi dùng Redis** giữa các workspace/backend khác nhau.
- Dựa trên đọc trực tiếp mirage đã cài (đường dẫn `.venv/.../mirage/...`).

## 1. Workspace sống bao lâu nếu không chạy command

Quyết bởi **eviction phía client (`DaemonManager` của plugin)**, KHÔNG phải daemon:
- **idle-TTL = 600s (10 phút):** reaper (mỗi 60s) DELETE workspace không dùng > 10'. Mỗi lần `ensure_workspace` mới cũng quét idle.
- **LRU cap = 6:** tạo workspace thứ 7 → xoá ngay cái ít-dùng-nhất bất kể tuổi.
- **Daemon KHÔNG tự evict từng workspace idle.** Nó chỉ tự tắt **~600s sau khi TOÀN BỘ registry rỗng** (plugin đặt `MIRAGE_IDLE_GRACE_SECONDS=600` trong `tools/_daemon.py` trước khi spawn; mặc định mirage là 30s). Timeline: workspace sống tới ~600s (idle-TTL client) → reaper xoá → registry rỗng → daemon đợi thêm ~600s → tắt (≈20 phút tổng khi hoàn toàn rảnh).

→ Không chạy command: workspace sống **~10 phút** rồi reaper xoá. Tăng grace của daemon chỉ giảm số lần **respawn daemon** (cost ~vài giây import mirage), KHÔNG làm hội thoại mới ấm hơn (workspace mới vẫn nguội — do keying Map 2). (600s / 6 / grace là hằng số plugin đặt, chỉnh được.)

## 2. Cache share giữa các workspace không?

- **RAM cache (mặc định):** KHÔNG share. Mỗi Workspace có object cache riêng → hai conversation (dù cùng config) không dùng chung cache/connection. (Đây là lý do "cùng config vẫn nguội" ở Map 2.)
- **Redis cache:** CÓ share xuyên workspace. Key theo `key_prefix + resource_path`, **không** theo workspace/conversation id:
  - File: `{prefix}data:{key}` / `{prefix}meta:{key}` (`cache/file/redis.py:34-35`).
  - Index: `{prefix}mirage:idx:entry:{resource_path}` / `...children:{resource_path}` (`cache/index/redis.py:42-43,74-78`).
  - `BaseResource.set_index` dùng **một prefix global** cho mọi resource (`resource/base.py:65-72`) — không kèm workspace id.

## 3. ⚠️ Rủi ro collision khi dùng Redis (ca: `/data`→HF ở conv1, `/data`→S3 ở conv2)

Chung Redis + chung `key_prefix` + cùng `resource_path`, khác backend → có thể lẫn. Tách 2 tầng:

### File bytes — AN TOÀN (fingerprint bảo vệ)
`is_fresh(key, remote_fingerprint)` được gọi trong đường đọc (`workspace/dispatcher.py:66`, `workspace/mount/registry.py:313`): so fingerprint đã cache với **fingerprint stat LIVE từ backend thật** (`cache/file/redis.py:84-90` trả `fp == remote_fingerprint`).
→ conv2 (S3) đọc `/data/x` → stat S3 cho fingerprint khác entry cache (của HF) → **miss → fetch lại từ S3**. **Không trả nhầm bytes.**

### Index / metadata (ls, stat, find) — KHÔNG an toàn
Index key = `prefix + resource_path` + TTL, **không fingerprint, không định danh backend**. Trong TTL: conv1 (HF) ghi listing cho một path → conv2 (S3) đọc cùng path + cùng prefix → **nhận listing/metadata của HF** → `ls`/`stat`/`find` SAI. Đây là chỗ collision thật.

### mirage CÓ đề cập lỗi này
Docstring `RedisIndexCacheStore` (`cache/index/redis.py:39-40`):
> "Multiple stores can share one Redis server by using distinct key_prefix values (e.g. 'gdrive:', 's3:')."

→ mirage thừa nhận và giao cách tránh cho **người gọi: mỗi resource/store một `key_prefix` riêng**. mirage **không tự làm** (set_index dùng một prefix global). Caller phải namespace.

## 4. Hệ quả cho plugin & khuyến nghị

- **Giữ RAM cache (mặc định) → né hoàn toàn** collision (cache per-workspace, không share). Đổi lại không có warm-share xuyên conversation.
- **Nếu bật Redis:** BẮT BUỘC namespace `key_prefix` để không lẫn:
  - Theo **config** (nhét hash config vào prefix) → config khác (HF vs S3) → prefix khác → không đụng; cùng config → cùng prefix → share ấm (đúng ý muốn warm-share).
  - Lý tưởng thêm: namespace theo **từng resource/mount** để trong một config mount nhiều backend cũng không đụng (vì index key chỉ theo `resource_path`, hai mount khác backend cùng relative-path có thể đụng nếu chung prefix).
- Cân nhắc: chỉ file-bytes được fingerprint bảo vệ; **index/listing thì không** → đây là bề mặt lỗi chính khi share Redis. Nếu chấp nhận `ls`/`stat` có thể sai giữa các config chung prefix thì mới không cần namespace — nhưng thường KHÔNG chấp nhận được.

## 5. Con trỏ mã
- Workspace lifetime (client): `tools/_daemon.py` (idle-TTL 600s, cap 6, reaper 60s); daemon idle self-exit khi registry rỗng ~30s (`mirage/server/registry.py`, `server/daemon.py`).
- File cache Redis + fingerprint: `mirage/cache/file/redis.py:34-90`; is_fresh callers `mirage/workspace/dispatcher.py:66`, `mirage/workspace/mount/registry.py:313`.
- Index cache Redis (không fingerprint) + docstring cảnh báo: `mirage/cache/index/redis.py:33-78`.
- set_index dùng prefix global: `mirage/resource/base.py:60-74`.
