# Future Work — Cài dependency backend theo nhu cầu (không bundle full)

- **Ngày ghi:** 2026-07-03
- **Bối cảnh:** `requirements.txt` từng bundle `mirage-ai[s3,r2,gcs,oci,redis,postgres,mongodb,ssh,hf,chroma,lancedb,qdrant,nextcloud,databricks,email,parquet,pdf,hdf5]` → cài **rất lâu/nặng** (pyarrow, h5py, google-cloud, oci, databricks, lancedb, qdrant, chromadb...). Hiện đã tạm chuyển sang base gầy `mirage-ai` (bare) trong requirements.txt.
- **Mục tiêu:** chỉ cài dependency của backend **thực sự dùng**, không fix cứng danh sách full.

## Ràng buộc gốc (vì sao khó)

Dify cài `requirements.txt` **tĩnh, một lần lúc install plugin**. Nhưng *resource nào được dùng* chỉ biết **lúc runtime** — nằm trong `workspace_yaml` mỗi lần gọi tool / mỗi authorization. ⇒ Không thể "chỉ cài cái đang dùng" ở thời điểm Dify-install. Chỉ có 2 chỗ chèn được:
1. **Lúc build/package** (người đóng gói khai backend mình cần).
2. **Lúc runtime** (cài lazy khi lần đầu mount một resource).

## Chìa khóa: mirage tự báo đúng extra cần cài

Mỗi resource guard import và ném `ImportError` nêu **chính xác** lệnh cài, ví dụ:
```
RedisResource requires the 'redis' extra. Install with: pip install mirage-ai[redis]
```
(`.venv/.../mirage/resource/redis/redis.py:19-21`). ⇒ Runtime có thể **catch ImportError → parse `mirage-ai[<extra>]` → pip install → retry**. Không cần tự bảo trì map resource→extra (dù vẫn nên có map để cài chủ động trước khi dựng).

Lưu ý: không phải resource nào cũng có extra riêng (slack/github/notion/gdrive/gmail... dùng deps core như httpx/requests → không cần cài thêm). Chỉ các backend nặng mới map tới extra (`s3`→boto3, `redis`→redis, `postgres`→psycopg, `parquet`→pyarrow, `hdf5`→h5py, `gcs`→google-cloud, `oci`, `databricks`, `lancedb`, `qdrant`, `chroma`, `mongodb`, `ssh`, `hf`, `r2`, `pdf`, `email`).

## 3 phương án

### A — Base gầy + chọn extras lúc đóng gói (an toàn, ít rủi ro)
- `requirements.txt` = core: `dify_plugin`, `mirage-ai` (không extra), `httpx`, `uvicorn` → cài nhanh.
- Thêm `build.sh` / tài liệu: người đóng gói đặt `MIRAGE_EXTRAS=s3,redis,postgres` → sinh `mirage-ai[$MIRAGE_EXTRAS]` vào requirements → `dify-plugin plugin package`.
- **Ưu:** nhanh, tất định, không cần quyền runtime đặc biệt. **Nhược:** tĩnh theo package; thêm backend = đóng gói lại (thường OK vì 1 deployment có tập resource cố định).

### B — Cài lazy trong daemon lúc runtime (đúng "dùng gì cài nấy" nhất)
- Base = core-only. Khi daemon `ensure_workspace`:
  - **Chủ động:** đọc các `resource:` trong config → map ra extras → cài cái nào chưa import được.
  - **Hoặc phản ứng:** dựng workspace, bắt `ImportError` của mirage → parse `mirage-ai[extra]` → `subprocess pip install` vào venv → retry một lần.
- Cache: cài rồi thì lần sau instant. Chỉ cài backend thực sự mount.
- **Ưu:** đúng ý — chỉ cài cái dùng, tự động. **Nhược/rủi ro:**
  - Cần **network egress + site-packages ghi được lúc runtime** trong sandbox Dify (điều kiện MỚI, chưa xác minh — cùng nhóm rủi ro với spawn subprocess/bind port của Option B). Self-hosted nhiều khả năng OK; managed/serverless có thể chặn.
  - **Lần-đầu chậm:** extra nặng (pyarrow/h5py/google-cloud) cài lâu, có thể vượt `MAX_REQUEST_TIMEOUT=120s` → cần xử lý: cài ở bước setup có allowance dài hơn, hoặc trả message "đang cài dependency, thử lại".
  - Concurrency: 2 invoke cùng cần một extra → phải lock quanh pip install.
  - Reproducibility/audit: môi trường đổi lúc chạy, khó kiểm soát/log.

### C — Lai (UX tốt nhất, nhiều việc nhất)
- Base gầy + tập "common" nhẹ mặc định (vd `s3,redis`) để dùng ngay + cài nhanh.
- Daemon lazy-install phần còn lại (B). Nếu runtime-pip bị chặn → trả lỗi rõ: "workspace cần `mirage-ai[X]`, hãy đóng gói lại kèm nó" (fallback về A).

## Thắng nhanh (bất kể chọn gì)
Cắt các extra **nặng & ít dùng** khỏi default (`hdf5, parquet, gcs, oci, databricks, lancedb, qdrant, chroma`), chừa tập lean thường dùng (`s3, redis, postgres, mongodb, ssh`) → giảm thời gian cài rõ rệt ngay.

## Khuyến nghị & bước tiếp theo
1. **Làm trước (A + trim):** default = base gầy (đã làm) hoặc lean set; thêm `build.sh` chọn extras lúc đóng gói. Rẻ, an toàn.
2. **Spike B để quyết:** thử trong sandbox mục tiêu — daemon catch `ImportError` + `pip install mirage-ai[extra]` runtime có chạy không, đo latency. Nếu chạy → nâng lên **C** (dùng chính ImportError của mirage). Nếu bị chặn → giữ **A**.

## Con trỏ mã liên quan
- ImportError guard nêu extra: `mirage/resource/<name>/<name>.py` (vd `redis/redis.py:19-21`).
- Extras khai báo: `mirage/python/pyproject.toml` `[project.optional-dependencies]` (30 extras; `all` = tất cả trừ `camel`).
- Lazy import backend: `mirage/resource/registry.py` (`build_resource` import class khi dựng).
- Nơi daemon dựng workspace (chèn logic cài chủ động): endpoint `POST /v1/workspaces` → `WorkspaceConfig.to_workspace_kwargs()` → `Workspace(**kwargs)`.
