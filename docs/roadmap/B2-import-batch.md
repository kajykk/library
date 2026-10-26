# B2 · 目录扫描导入 + 批量治理

| 项目 | 值 |
|---|---|
| 批次 | B2 |
| 状态 | TODO |
| 依赖 | B1 全部 DONE；G1 每一步单独授权；G3 迁移授权 |
| 真实数据写入 | **是**（导入、批量修改、软删除、补封面、重提取、语言归一化、孤儿隔离） |
| 迁移 | `0008_operation_logs` |
| 预计改动 | 后端约 14 个文件、前端约 12 个文件 |
| 完成标志 | B2-01 ~ B2-13 全部 DONE，B2 报告已写；所有真实写入都有 operation log 与前后计数 |

## 1. 目标

把「库里有 1298 本，但 581 本没正文、515 本没封面、32 本没分类」从**看得见**变成**修得动**，同时保证每一步都能撤销或至少留痕：

1. 从白名单目录扫描并导入新书，坏文件/重复文件先分类，不盲目入库。
2. 批量改分类/语言/标签、软删除、补封面。
3. 对 0 字/短正文的书按需重提取，不误覆盖已有正文。
4. 语言变体归一化。
5. 孤儿文件移到隔离区，不删除。
6. 每个写操作都有预览、确认、operation log、撤销入口。

## 2. 不做什么

- 不安装 tesseract/OCR；扫描件只标记 `needs_ocr`，不自动识别。
- 不硬删除任何 `documents` 行，不删除 `server/data/` 下任何文件。
- 不自动批量修复；所有 `--apply` 级动作必须用户逐次授权。
- 不做语义索引（B5）。
- 不重构 `extract_text_from_file` 的公共签名；只新增带预算的入口。
- 不把目录扫描做成浏览器 `webkitdirectory` 上传：服务端只扫描 `KB_IMPORT_DIRS` 白名单。

## 3. 授权与备份（每个真实写入动作前）

对每个动作（导入、批量修改、软删除、补封面、重提取、语言归一化、隔离）分别执行：

1. 向用户展示 **dry-run 预览**：影响数量、前 20 条标题、将改哪些字段。
2. 获得用户明确确认（G1）。
3. 备份：
   ```bash
   python server/scripts/incremental_backup.py --apply --keep 7
   ```
4. `pragma integrity_check` 为 `ok`。
5. 记录执行前计数。
6. 执行。
7. 记录执行后计数、`operation_id`、回滚方式。
8. 写入 [progress.md](progress.md) 的「阻塞/授权」区。

未获得确认时，只能做：只读扫描、dry-run、临时库测试、前端静态改动。

## 4. 推荐执行顺序

任务编号不代表执行顺序。按依赖执行：

```text
B2-05 (operation log 表/服务)
  → B2-01 (ingest 共用)
  → B2-02 (目录扫描)
  → B2-03 (imports 路由)
  → B2-04 (批量 + ids)
  → B2-06 (封面)
  → B2-07 (重提取)
  → B2-08 (语言)
  → B2-09 (孤儿隔离)
  → B2-10 (导入向导)
  → B2-11 (质量动作 UI)
  → B2-12 (书架多选)
  → B2-13 (测试/扫描复核/报告)
```

B2-05 必须先做，因为 B2-03/04/06/07/08/09 都要写 `operation_logs`。

## 5. 任务

### B2-05 `operation_logs` 模型/迁移 + undo 服务

> 先做这一项，后续所有写操作复用。

**新增**：

- [server/alembic/versions/0008_operation_logs.py](../../server/alembic/versions/0008_operation_logs.py)
- [server/app/services/operation_log.py](../../server/app/services/operation_log.py)
- [server/app/routers/operations.py](../../server/app/routers/operations.py)
- [server/tests/test_operation_log.py](../../server/tests/test_operation_log.py)

**修改**：

- [server/app/models.py](../../server/app/models.py) 增加 `OperationLog`
- [server/app/main.py](../../server/app/main.py) 注册 `operations.router`

**模型/迁移**：严格按 [data-model.md](data-model.md) 第 2 节。`0008` 的 `down_revision = "0007_document_quality"`，幂等。

**服务契约**：

```python
class UndoResult(TypedDict):
    status: str
    restored: int
    skipped: list[dict]

def record(
    db: Session,
    *,
    kind: str,
    document_ids: list[uuid.UUID],
    before_state: dict,
    after_state: dict,
    summary: str,
    undoable: bool,
) -> OperationLog: ...

def undo(db: Session, operation_id: uuid.UUID) -> UndoResult: ...
```

**实现要求**：

- `record()` 只 `db.add()`，不 commit；调用方在自己的事务里 commit，保证「业务变更 + 日志」原子。
- `document_ids` 去重、最多 500；超过抛 `ValueError`。
- `before_state/after_state` 的形状统一：
  ```json
  {
    "docs": {
      "<doc_id>": { "collection_id": "...", "language": "en" }
    }
  }
  ```
  禁止放 `content`；`reextract` 只放 `{"content_chars": 123}`。
- `undo()` 按 `kind` 分派，支持 B2-04/06/08/09 与导入；`reextract` 返回 `not_undoable`。
- 撤销是写操作：只有 `status='applied'` 且 `undoable=true` 才能撤销；`undone/partial` 再撤销返回 `already_undone`/400。
- 撤销过程中再次校验文档存在与字段未被后续修改；skipped 要给出原因。
- 撤销本身不再写一条 operation log（避免递归）；在 `undone_at`/`status` 留痕。

**路由**：

- `GET /api/operations`
- `GET /api/operations/{operation_id}`
- `POST /api/operations/{operation_id}/undo`，请求体 `{ "confirm": true }`

**测试**：

- 每种 kind 的 before/after 序列化不含正文。
- `batch_update` 撤销：字段一致才恢复；被后续改过则 skipped。
- `batch_soft_delete` 撤销：恢复 `deleted_at=null`。
- `directory_import` 撤销：只软删除仍存活的导入文档，不碰文件。
- `cover_upload` 撤销：恢复旧 `cover_path`，旧文件仍在。
- `language_normalize` 撤销：恢复原语言。
- `orphan_quarantine` 撤销：文件移回；目标存在则 skipped。
- 不可撤销/已撤销/不存在 → 正确状态码。

**验收**：临时库测试全绿；真实库迁移后表为空；`integrity_check=ok`。

---

### B2-01 抽取 `services/ingest.py`，上传与目录导入共用

**新增**：[server/app/services/ingest.py](../../server/app/services/ingest.py)

**修改**：

- [server/app/routers/documents/upload.py](../../server/app/routers/documents/upload.py)
- [server/app/services/file_storage.py](../../server/app/services/file_storage.py)
- [server/tests/test_ingest.py](../../server/tests/test_ingest.py)

**目标**：不复制上传逻辑。上传端点仍保持现有行为和错误信息；目录导入走同一套格式校验/去重/元数据/正文/自动分类。

**新增 `file_storage.import_file`**：

```python
def import_file(
    source: Path, doc_id: uuid.UUID, fmt: str, max_size_bytes: int
) -> tuple[str, int, str]:
    """流式复制源文件到 data/files，同时计算 SHA-256。
    返回 (rel_path, size, sha256)。超过 max_size 抛 ValueError。"""
```

要求：

- 1 MB 分块，不把整本读进内存。
- 目标路径仍用 `SAFE_EXT` 校验。
- 复制失败/超限时删除半成品。
- 不删除源文件。

**`ingest.py` 契约**：

```python
class IngestDuplicate(Exception):
    existing_id: uuid.UUID
    existing_title: str

class IngestError(Exception):
    code: str
    message: str

@dataclass
class IngestOutcome:
    document_id: uuid.UUID | None
    status: Literal["imported", "skipped"]
    title: str | None
    reason: str | None = None
    existing_id: uuid.UUID | None = None

def validate_format(filename: str) -> str: ...

def finalize_ingest(
    db: Session,
    *,
    doc_id: uuid.UUID,
    filename: str,
    fmt: str,
    rel_path: str,
    file_size: int,
    sha256: str,
    collection_id: uuid.UUID | None,
    source: str,                    # "upload" | "directory"
    source_path: str | None = None, # 相对源路径，仅目录导入
) -> IngestOutcome: ...
```

`finalize_ingest` 负责：

1. 再次检查格式白名单。
2. 按 `sha256` 查存活重复；重复则删除刚写入的文件，抛 `IngestDuplicate`。
3. `metadata_extract.extract_metadata_from_file`。
4. `content_extract.extract_text_from_file`（仅文本格式）。
5. 自动分类（`collection_id is None` 时），与现有上传一致；OCR 后台分类保留在上传端点，不在服务里启动后台任务。
6. 创建 `Document`；成功返回 `imported`。
7. 目录导入的 `source_path` 写入 `meta["import_source"]`（只存相对路径，便于排查；不存绝对路径）。

**上传端点改造**：

- 保留 `stream_file_to_disk`、`sha256_of`、限流、错误码。
- 去重/元数据/正文/创建文档改为调用 `finalize_ingest`。
- `IngestDuplicate` → 409，响应体保持现有 `{code, message, existing_id, existing_title}`。
- `IngestError` → 400/413/422，按 `code` 映射。
- 后台 OCR 分类仍在 upload.py 里，仅对上传路径生效。
- 行为测试必须与改造前一致（现有 `test_api.py` 覆盖上传）。

**测试**：

- 上传 EPUB/PDF/TXT 成功，元数据/正文/去重与改造前一致。
- 重复上传返回 409 且新文件已清理。
- 超限返回 413。
- 目录导入同一文件返回 `skipped:duplicate_in_db`，不产生第二条文档。
- 自动分类仍然生效；指定 `collection_id` 时不覆盖。

---

### B2-02 `directory_scan.py` 白名单扫描、坏文件/重复分类

**新增**：[server/app/services/directory_scan.py](../../server/app/services/directory_scan.py)

**修改**：

- [server/app/config.py](../../server/app/config.py) 增加：
  ```python
  import_dirs: str = ""                 # KB_IMPORT_DIRS，os.pathsep 分隔
  import_max_files: int = 5000
  import_max_depth: int = 8
  ```
- [server/tests/test_directory_scan.py](../../server/tests/test_directory_scan.py)

**目录解析**：

```python
@dataclass(frozen=True)
class ImportDir:
    id: str          # sha256(realpath)[:12]
    label: str       # basename
    path: str        # 绝对路径（仅服务端/设置页使用）
    exists: bool
    readable: bool

def configured_dirs() -> list[ImportDir]: ...
def resolve_dir(dir_id: str) -> tuple[ImportDir, Path]: ...  # 不在白名单/不可读 → ValueError
```

- `KB_IMPORT_DIRS` 用 `os.pathsep` 分隔（Windows 是 `;`）。
- `Path.resolve(strict=True)`；必须仍在配置根的实路径内，防止 symlink 逃逸。
- 重复配置去重。

**扫描**：

```python
def scan_directory(
    root: Path,
    *,
    recurse: bool,
    compute_hashes: bool,
    max_files: int,
    max_depth: int,
    max_bytes: int,
    job: JobState,
) -> dict: ...
```

- 用 `os.scandir` 递归；**跳过所有 symlink**，不跟随。
- 跳过隐藏文件/目录（`.` 开头）和临时文件（`~$`、`.tmp`、`.crdownload`、`.part`）。
- 只收文件；深度超过 `max_depth` 停止该分支并计数。
- 文件数超过 `max_files`：停止并 `truncated=true`。
- 格式：扩展名必须在 `UPLOAD_FORMAT_WHITELIST`；否则 `unsupported`。
- 大小：`> upload_max_size_mb` → `too_large`。
- 坏文件判定：
  - `size == 0` → `broken / empty_file`
  - `size < 512` 且头部包含 `<html`/`<!doctype` → `broken / html_error_page`
  - `.pdf` 且头部不是 `%PDF` → `broken / not_pdf`（避免把错误页当 PDF 入库）
  - `.epub` 且不是 ZIP 头（`PK\x03\x04`）→ `broken / not_epub`
- `compute_hashes=true`：流式 SHA-256；否则 `sha256=null`，重复检测降级为同目录同大小+文件名（标记 `duplicate_in_scan_weak`）。
- 与库内重复：查 `documents.sha256` 存活集合（一次查询）；`duplicate_in_db`。
- 扫描内重复：同 sha 第二个及以后 `duplicate_in_scan`。
- `reason` 只返回稳定码，不返回绝对路径/异常文本。

**结果持久化**：

- `data/import_scans/{scan_id}.json`，包含 `scan_id/root/status/items/counts/started_at/finished_at/truncated`。
- 文件权限与 `data/` 一致；不写入仓库。
- 读取时校验 JSON schema 与 TTL（24h）。

**测试**：

- 嵌套目录、隐藏文件、symlink 逃逸、0 字节、143 字节 HTML 错误页、假 PDF、超限、不支持格式。
- 两个同内容文件 → 一个 `ready`、一个 `duplicate_in_scan`。
- 库内已有同 sha → `duplicate_in_db`。
- `max_files/max_depth` 截断。
- 扫描不修改任何源文件，不写 `documents`。

---

### B2-03 `imports.py` 路由、扫描/导入后台任务

**新增**：

- [server/app/routers/imports.py](../../server/app/routers/imports.py)
- [server/tests/test_imports_api.py](../../server/tests/test_imports_api.py)

**修改**：[server/app/main.py](../../server/app/main.py) 注册路由（前缀 `/api`）

**端点**（契约见 [api-contracts.md](api-contracts.md) 第 3 节）：

- `GET /api/imports/dirs`
- `POST /api/imports/scan`
- `GET /api/imports/scans/{scan_id}`
- `POST /api/imports/execute`
- `GET /api/imports/operations/{operation_id}`

**实现要求**：

- 扫描/导入都用 `JobState` 单飞；同一时刻只允许一个扫描和一个导入。
- 扫描 id 在执行前生成并写入结果文件，状态 `running`；完成后更新为 `done/failed`。
- 执行前：
  - 读取 scan 文件，状态必须 `done`；
  - 未过期（24h）；
  - `rel_paths` 去重、最多 500；
  - 每个路径必须在扫描结果中且 `status=ready`；
  - 重新 `resolve` 到白名单根，拒绝 symlink/路径逃逸/文件已变化（size/mtime 与扫描不一致 → `stale_file`）。
- 执行时：
  - 每个文件生成新 `doc_id`，`file_storage.import_file` 复制 + hash；
  - 调 `ingest.finalize_ingest`；
  - 逐项结果写入 execution 结果文件；
  - 每成功一项，把 `document_id` 追加到 operation log 的 `document_ids` 并 commit；
  - 单文件失败不中断整批；
  - 结束后把 operation log 的 `status` 设为 `applied`，summary 写「目录导入 N 本」。
- 源文件永不删除；复制到 data 后若失败，清理本次新文件。
- 返回结果不包含绝对源路径，只包含 `rel_path`。
- 速率限制：`scan`、`execute` 各 2/60s（沿用现有 middleware 配置方式）。

**测试**：

- 未配置目录 → `dirs=[]`；伪造 dir_id → 404/400。
- 扫描临时目录 → 状态轮询到 done → 分类计数正确。
- 执行 2 个 ready → 2 条文档 + 1 条 operation log；重复执行同 scan → 全部 `duplicate_in_db`/skipped，不新增。
- 选中 `broken`/`duplicate_in_db` → 400。
- 路径逃逸/符号链接 → 400，不读文件。
- 超过 500 → 400。
- 未授权目录扫描不写任何文件。

---

### B2-04 批量修改/软删除 + `GET /documents/ids`

**新增**：[server/app/routers/documents/batch.py](../../server/app/routers/documents/batch.py)

**修改**：

- [server/app/routers/documents/__init__.py](../../server/app/routers/documents/__init__.py)：在 `crud` 之前 import `batch`，确保 `/batch`、`/ids` 先于 `/{doc_id}` 注册。
- [server/app/schemas.py](../../server/app/schemas.py) 或 `batch.py` 内部定义请求模型。
- [server/tests/test_batch.py](../../server/tests/test_batch.py)

**端点**：

- `GET /api/documents/ids`（查询参数与列表一致，`max<=5000`）
- `PATCH /api/documents/batch`
- `POST /api/documents/batch/delete`

**批量修改规则**：

- `ids` 1–500，去重；全部必须存活，否则 400 + `missing_ids`（不做部分执行）。
- `patch` 至少一个字段；允许：
  - `collection_id: UUID | null`
  - `language: str`（1–32 字符，去除首尾空白）
  - `add_tags: list[str]`（每项 1–255，最多 20）
  - `remove_tags: list[str]`（同上）
- 在同一个事务里：
  1. 读取每本 `before_state`（只含将改字段）。
  2. 应用修改；`add_tags` 用 `get_or_create_tag`，`remove_tags` 删除关联。
  3. 计算 `after_state`。
  4. `operation_log.record(...)`。
  5. commit。
- 不允许通过本接口改 `title/author/content/cover_path`；这些走单本 PATCH 或专门动作。
- 批量删除：
  - 只设 `deleted_at=utcnow()`，不删文件；
  - 记录 before/after；
  - 返回 `{deleted, operation_id}`。
- `confirm` 必须为 true，否则 400。

**`/documents/ids`**：

- 复用 `apply_document_filters`，确保与列表/计数同口径。
- 返回 `{ids, total, truncated}`；`total` 是完整计数，`ids` 最多 `max` 条。
- 超过 `max` 时截断，前端提示缩小筛选。

**测试**：

- 批量移入/移出分类、改语言、加/删标签；列表立即反映。
- 撤销后完全恢复；标签恢复（添加的移除、移除的加回）。
- 任一 id 不存在 → 全部不执行，数据库无变化。
- 501 个 id → 422/400。
- 软删除后列表不显示，回收站（`include_deleted=true`）仍可见；文件仍在。
- 撤销软删除后列表恢复。
- `/documents/ids` 与列表数量一致；筛选 + 排序不影响 ids 集合。

---

### B2-06 `PUT /documents/{id}/cover`，魔数校验

**修改**：

- [server/app/routers/documents/files.py](../../server/app/routers/documents/files.py) 增加 `PUT /{doc_id}/cover`
- [server/app/services/file_storage.py](../../server/app/services/file_storage.py) 复用 `save_cover`
- [server/tests/test_api.py](../../server/tests/test_api.py) 或新增 `test_cover_upload.py`

**实现**：

- `UploadFile`，最大 8 MB（独立于书籍 500 MB 限制）。
- 读取前 32 字节判断魔数：
  ```python
  JPEG: FF D8 FF
  PNG:  89 50 4E 47 0D 0A 1A 0A
  WebP: RIFF....WEBP
  GIF:  GIF87a / GIF89a
  ```
- 只接受以上四种；SVG/HTML/未知 → 400。
- 扩展名由魔数决定，不信任文件名。
- 保存到 `data/covers/{doc_id}.{ext}`；旧 `cover_path` 记入 operation log；旧文件不删除（便于撤销/清理）。
- 更新 `cover_path`，`updated_at` 自动更新。
- `operation_log.record(kind="cover_upload", undoable=True, before_state={cover_path}, after_state={cover_path})` 同事务。
- 返回 `DocumentOut`。

**测试**：

- 四种合法图片成功；SVG、文本、伪造 `.jpg` 的 HTML 失败。
- 超过 8 MB → 413。
- 无封面文档上传后 GET cover 200；替换后旧文件仍在磁盘。
- 撤销恢复旧路径；旧图仍可读（若之前有）。
- 文档不存在/已软删除 → 404。

---

### B2-07 重提取包装函数 + `reextract` 后台任务

**修改**：

- [server/app/services/content_extract.py](../../server/app/services/content_extract.py)
- [server/app/routers/documents/__init__.py](../../server/app/routers/documents/__init__.py)（先 import `reextract` 静态路由）
- [server/app/config.py](../../server/app/config.py) 增加 `reextract_timeout_seconds: int = 30`

**新增**：

- [server/app/services/reextract_job.py](../../server/app/services/reextract_job.py)
- [server/app/routers/documents/reextract.py](../../server/app/routers/documents/reextract.py)
- [server/tests/test_reextract.py](../../server/tests/test_reextract.py)

**`content_extract` 改造（签名不变）**：

- 把现有 `extract_text_from_file(fmt, path)` 的函数体抽成内部：
  ```python
  def _extract_text_bounded(fmt, path, timeout_seconds) -> str
  ```
- 新增公开函数：
  ```python
  def extract_text_from_file_with_budget(fmt: str, path: Path, timeout_seconds: int) -> str
  ```
- `extract_text_from_file(fmt, path)` 保持原签名，委托 `_extract_text_bounded(..., EXTRACT_TIMEOUT)`。
- PDF 进程隔离分支的 `_extract_pdf_isolated` 也接受预算参数；不改变 `KB_EXTRACT_PROCESS_ISOLATION` 默认行为。
- 新增测试：短预算超时返回空；长预算能提取《历史与自由意识》这类 37s 的 PDF（测试用合成大 PDF 或跳过真实文件，不把 61MB 书放进仓库）。

**任务**：

```python
def start(ids: list[uuid.UUID], timeout_seconds: int, overwrite: str) -> bool: ...
def run_sync(db: Session, ids, timeout_seconds, overwrite) -> dict: ...
def status() -> dict: ...
```

- `ids` 1–500；只处理 `book/pdf` 且有 `file_path`。
- 每本：
  - 文件不存在 → `failed: missing_file`；
  - 格式不在 `TEXT_FORMATS` → `skipped: unsupported`；
  - `new_text = extract_text_from_file_with_budget(...)`；
  - 空文本 → `empty`，**不覆盖**；
  - `overwrite=only_if_longer` 且 `len(new) <= len(old)` → `skipped: not_longer`；
  - 否则写入 `content`；
  - 更新该文档的 `document_quality`（调 `quality_audit.audit_one`），避免质量页继续显示旧字数。
- 独立 Session；每 10 本 commit；`JobState("reextract")`。
- 结束后写一条 `operation_logs`（kind=`reextract`，undoable=false，before/after 只记字数），summary 例如「重提取：更新 12，空 3，跳过 5」。
- 不修改 `title/author/cover`。

**路由**：

- `POST /api/documents/reextract`（body 见契约）
- `GET /api/documents/reextract-status`

**测试**：

- 长文不覆盖短文（`only_if_longer`）；`always` 覆盖。
- 新文本为空时不覆盖。
- 文件缺失/格式不支持/文档软删除 → 跳过并记录。
- 任务单飞 409。
- operation log undoable=false，undo 返回 not_undoable。
- 质量行字数同步更新。

**真实库动作（需 G1）**：

- 先用 `/quality/issues?issue=empty_content` 取前 20 本 dry-run。
- 重点验证《历史与自由意识》：`timeout_seconds=90`，执行后字数应 > 100000，`empty` 不再命中。
- 《读人像法官一样识人断事》：预期仍为空/极短（扫描件），标记 `needs_ocr`，不反复重试。

---

### B2-08 语言归一化批量动作

**新增**：

- [server/app/services/language_normalize.py](../../server/app/services/language_normalize.py)
- [server/app/routers/documents/language.py](../../server/app/routers/documents/language.py)
- [server/tests/test_language_normalize.py](../../server/tests/test_language_normalize.py)

**复用** B1-02 的 `normalize_language`，不要在第二处写映射表。

**规则**：

| 原值示例 | 归一化 |
|---|---|
| `en`, `en-US`, `en_US`, `eng`, `EN-US` | `en` |
| `zh`, `zh-CN`, `zh-Hans`, `zh-TW`, `zh_TW` | `zh` |
| `ja`, `ja-JP` | `ja` |
| 空/None | `und` |
| `fr`, `de` 等 | 保持不变（报告为 unchanged） |

**端点**：

- `POST /api/documents/normalize-language`

  ```json
  { "dry_run": true, "ids": ["uuid"], "confirm": false }
  ```

- `dry_run=true`：只读预览，不需要 `confirm`。
- 应用：
  - `ids` 省略 = 全部存活 book/pdf，但内部按 500 一批；
  - 每批写一条 operation log（undoable=true）；
  - 每批 commit；
  - 返回 `operation_ids`、`changes`、`unchanged`、`batches`。
- 每本改完调 `quality_audit.audit_one` 更新 `language_variant` 问题。
- 未知值不改，列在 `unchanged_details`。

**测试**：

- 所有映射样例。
- dry-run 不写库、不产生 operation log。
- 应用 600 本 → 2 条 operation log，每条 <=500。
- 撤销后全部恢复；被后续改过的文档 skipped。
- `language_variant` 质量 issue 数量下降。

**真实库动作（需 G1）**：

- 先 dry-run，报告 32 条变体明细。
- 用户确认后执行；执行前后统计 `language` 分布。

---

### B2-09 孤儿文件隔离动作（移动不删除 + manifest）

**新增**：

- [server/app/services/orphan_quarantine.py](../../server/app/services/orphan_quarantine.py)
- [server/app/routers/documents/orphans.py](../../server/app/routers/documents/orphans.py)
- [server/tests/test_orphan_quarantine.py](../../server/tests/test_orphan_quarantine.py)

**复用** B1-02 `scan_orphans` 的只读结果。

**端点**：

- `GET /api/documents/orphans`（复用 quality 的 orphan 报告）
- `POST /api/documents/orphans/quarantine`

**执行要求**：

- 只允许 `files/` 下的相对路径；`covers/` 暂不隔离。
- 执行时**重新计算孤儿集合**，不信任客户端提交的旧列表；提交项必须仍在当前孤儿集合中，否则 `skipped: not_orphan`。
- 路径必须通过 `file_storage.abs_path`；拒绝 `..`、绝对路径、symlink。
- 单次最多 500 个文件、总量最多 5 GB。
- 目标目录：`data/quarantine/files/{YYYYMMDD-HHMMSS}/`，保留原相对路径。
- 移动前写 manifest（先写临时文件再原子 rename）：
  ```json
  {
    "created_at": "...",
    "items": [
      { "rel_path": "files/xxx.pdf", "size": 123, "sha256": "...", "moved_to": "..." }
    ]
  }
  ```
- 使用 `shutil.move`；单个失败不中断，记录 `failed`。
- operation log kind=`orphan_quarantine`，undoable=true，after_state 存移动映射。
- 永不删除文件；隔离目录不参与 `scan_orphans`（只扫 `data/files`、`data/covers`）。

**测试**：

- 移动成功、manifest 正确、`data/files` 中消失、隔离目录存在。
- 非孤儿路径 → skipped/400。
- `../` 逃逸、symlink → 400。
- 撤销移回；目标已存在则 skipped。
- 软删除文档引用的文件不被当作孤儿。
- 超过 500/5GB → 400。

**真实库动作（需 G1）**：

- 真实库目前孤儿数可能为 0；先只读报告。若 P3-4 的隔离目录已有文件，不重复移动。

---

### B2-10 `/import` 三步向导 UI

**新增**：

- [src/app/import/page.tsx](../../src/app/import/page.tsx)
- [src/components/import/ImportWizard.tsx](../../src/components/import/ImportWizard.tsx)
- [src/components/import/ScanResultTable.tsx](../../src/components/import/ScanResultTable.tsx)
- [src/components/__tests__/ImportWizard.test.tsx](../../src/components/__tests__/ImportWizard.test.tsx)

**修改**：[src/lib/api/client.ts](../../src/lib/api/client.ts) 增加 imports 类型与函数

**三步**：

1. **选择目录**
   - 从 `GET /api/imports/dirs` 选择白名单目录。
   - 未配置：显示 `KB_IMPORT_DIRS` 配置说明（示例 `KB_IMPORT_DIRS=C:\Users\k\Downloads`），提供复制按钮；不提供「任意路径输入」。
   - 选项：递归子目录、计算哈希（默认开）。
2. **扫描预览**
   - 启动扫描后轮询 `GET /imports/scans/{id}`。
   - 显示计数卡：可导入、库内重复、扫描内重复、坏文件、不支持、超大。
   - 表格：相对路径、大小、格式、状态、原因；支持按状态筛选。
   - 默认勾选 `ready`；`broken/duplicate/unsupported/too_large` 不可勾选。
3. **确认导入**
   - 显示「将导入 N 本；源文件不会被删除；失败项会列出」。
   - 可选默认分类。
   - `confirm` 对话框后调用 execute。
   - 轮询执行结果，展示成功/跳过/失败；提供「查看导入的书」→ `/library`、「操作日志」链接。

**要求**：

- 超过 500 本 ready：提示分批，禁用一次性执行。
- 本地模式：整页 `StatePanel` 提示需要后端。
- 移动端卡片化，不横向溢出。
- 不显示服务器绝对路径给普通用户（当前为自托管单用户，可在目录选择处显示，但日志/结果只显示相对路径）。
- 导入完成后 invalidate `['documents']`、`['quality']`。

**测试**：

- 未配置目录空态。
- 扫描中轮询、完成后计数/表格正确。
- 只提交 ready 项；坏文件不可选。
- execute 后显示结果并 invalidate 查询。
- 500 上限提示。

---

### B2-11 质量中心动作 UI（预览/确认/日志/撤销）

**新增**：

- [src/components/quality/QualityActionBar.tsx](../../src/components/quality/QualityActionBar.tsx)
- [src/components/quality/BatchEditDialog.tsx](../../src/components/quality/BatchEditDialog.tsx)
- [src/components/quality/ReextractDialog.tsx](../../src/components/quality/ReextractDialog.tsx)
- [src/components/quality/OperationLogDrawer.tsx](../../src/components/quality/OperationLogDrawer.tsx)
- [src/components/quality/CoverUploadButton.tsx](../../src/components/quality/CoverUploadButton.tsx)

**修改**：[src/app/quality/page.tsx](../../src/app/quality/page.tsx)、[src/lib/api/client.ts](../../src/lib/api/client.ts)

**动作与入口**：

| 动作 | 入口 | 预览 | 确认 | 撤销 |
|---|---|---|---|---|
| 批量分类/语言/标签 | 选中行 + 动作条 | 列出字段变化 | 危险操作二次确认 | operation log |
| 补封面 | 单行「上传封面」 | 图片预览 | 直接上传 | operation log |
| 重提取 | 选中行 + 动作条 | 显示预计本数、超时、覆盖策略 | 确认 | 不可撤销（明示） |
| 语言归一化 | 动作条 | dry-run 变更表 | 确认 | operation log |
| 孤儿隔离 | 孤儿区 | 文件列表 + 大小 | 输入/勾选确认 | operation log |
| 软删除 | 选中行 + 动作条 | 书籍列表 | danger 确认 | operation log |

**要求**：

- 所有写请求必须 `confirm: true`，并且来自用户点击确认按钮。
- 操作进行中禁用重复提交；显示进度。
- 失败时保留对话框并显示错误，不清空选择。
- 成功后 invalidate `['quality']`、`['documents']`、`['operations']`，刷新当前 issue 列表。
- 操作日志抽屉：最近 20 条，显示 kind/summary/时间/状态；可展开看文档数；「撤销」按钮带确认。
- 重提取弹窗必须显示：「新文本为空不会覆盖；默认只在新文本更长时覆盖」。
- 补封面必须在前端预览并校验类型/大小，但服务端仍是最终校验。
- 不提供「一键修复全部」；每个动作都从明确筛选/选择出发。

**测试**：

- 选中 2 本 → 批量分类 → 请求体正确 → 成功后刷新。
- 取消确认 → 不发请求。
- 重提取提示覆盖策略；空结果不会显示成功更新。
- 撤销后列表恢复。
- 本地模式所有动作禁用。

---

### B2-12 书架多选与批量操作条

**修改**：

- [src/app/library/page.tsx](../../src/app/library/page.tsx)
- 新增 [src/components/library/SelectionToolbar.tsx](../../src/components/library/SelectionToolbar.tsx)
- 新增 [src/components/__tests__/SelectionToolbar.test.tsx](../../src/components/__tests__/SelectionToolbar.test.tsx)

**交互**：

- 工具栏「选择」按钮进入选择模式；再点「取消」退出。
- 卡片/列表行出现 checkbox；点击卡片其它区域仍打开阅读器；checkbox `stopPropagation`。
- 选择状态：
  - 单选/多选当前已加载项；
  - 「选择全部筛选结果」调用 `/documents/ids`（B2-04）；
  - 超过 500：提示「当前筛选有 N 本，超过单次 500 上限，请缩小筛选」，不自动截断。
- 底部/顶部 sticky 操作条：
  - 已选 N 本；
  - 批量分类（选择已有分类或新建）、批量加标签、批量软删除；
  - 清除选择。
- 批量删除：danger 确认，文案明确「只从书架移除，文件保留」。
- 操作成功后清除选择、invalidate、toast；提供「撤销」按钮（调用 operation undo）。
- 移动端：操作条固定在底部，按钮可横向滚动；不遮挡最后一本书（底部 padding）。

**测试**：

- 进入/退出选择模式；单选/全选当前页。
- 全选筛选结果超过 500 → 警告且禁用批量动作。
- 批量删除确认后请求体正确；撤销恢复。
- 切换筛选/搜索时清空选择（避免误操作）。

---

### B2-13 测试、Downloads 扫描复核、B2 报告

**1. Downloads 只读复核（不需要 G1）**

- 在 `.env` 或启动环境临时加：
  ```text
  KB_IMPORT_DIRS=C:\Users\k\Downloads
  ```
- 通过 `/import` 页面扫描，记录：
  - 总数、ready、duplicate_in_db、broken、unsupported、too_large；
  - 19 个坏文件是否仍被识别为 `broken`（5 个 0 字节 + 14 个 143 字节错误页）；
  - 已入库的书是否被识别为 `duplicate_in_db`。
- 扫描不写库；报告里附计数表。
- 不得把 `KB_IMPORT_DIRS` 指向 `C:\`、用户主目录或网络盘。

**2. 如需实际导入（G1）**

- 只导入 `ready` 且用户确认的条目；
- 导入前后计数；
- operation log id；
- 导入后 `quality` 审计一次（只写质量表）；
- 源文件仍在 Downloads。

**3. 测试**

```bash
cd server
E:/zy/python/python.exe -m pytest tests -q

cd ..
npm run typecheck
npm run lint
npm test
npm run build
npm run test:e2e
```

新增后端测试覆盖：ingest、scan、imports、batch、operation undo、cover、reextract、language、quarantine。
新增前端测试覆盖：import wizard、selection toolbar、quality actions。

**4. 浏览器验证**

- `/import` 扫描 Downloads → 预览 → 选择 2 本 → 导入 → 结果。
- 书架进入选择模式 → 全选 → 批量分类 → 撤销。
- `/quality` → 选中正文为空 → 重提取 1 本 → 进度 → 字数变化。
- 上传封面 → 书架显示新封面 → 撤销 → 旧封面恢复。
- 语言归一化 dry-run → 应用 1 批 → 撤销。
- 孤儿隔离（测试目录，不碰真实孤儿）→ 撤销。
- 移动端 390px 走通导入向导与选择操作条。

**5. 报告**

`docs/roadmap/reports/B2-2026-10-26.md`，额外包含：

- 每个真实动作的授权记录、前后计数、operation id、备份路径。
- Downloads 扫描分类表。
- 重提取前后字数对比（至少《历史与自由意识》）。
- 未做项（OCR 未安装、扫描件仍为空）。
- 风险与回滚。

更新 [progress.md](progress.md) 与 [README.md](README.md)。

## 6. 验收清单

- [ ] `0008` 迁移成功；operation log 可查询/撤销。
- [ ] 上传行为与改造前一致；目录导入复用同一套校验/去重。
- [ ] 扫描不写库、不跟随 symlink、不越白名单。
- [ ] 19 个坏文件被识别且未导入。
- [ ] 批量操作 <=500、全成或全败、有 operation log。
- [ ] 软删除不删文件；撤销可恢复。
- [ ] 封面魔数校验；SVG/伪造图片被拒。
- [ ] 重提取空文本不覆盖；默认只在新文本更长时覆盖。
- [ ] 语言归一化可 dry-run、分批、撤销。
- [ ] 孤儿只移动不删除，manifest 完整，可撤销。
- [ ] `/import` 与质量动作 UI 在桌面/移动端可用。
- [ ] 全量测试与浏览器验证通过。
- [ ] B2 报告、progress.md 已更新。

## 7. 回滚

1. 单个动作：`POST /api/operations/{id}/undo`（重提取除外）。
2. 导入：撤销 operation log → 软删除本次文档；源文件仍在。
3. 封面：撤销恢复旧 `cover_path`；旧文件未删除。
4. 隔离：撤销按 manifest 移回。
5. 代码：`git revert <B2 commit>`。
6. 迁移：`alembic downgrade 0007_document_quality` 会删除 operation log 表；执行前确认没有需要保留的撤销记录。
7. 整库：仅在用户确认并停止后端后，用备份覆盖；保留当前库副本。

## 8. 交给 B3 的东西

- `GET /api/documents/ids` 已可用；B3-03 只做书架端「全选筛选结果」接入，不重复实现后端。
- `sort`/`facets` 尚未实现（B3-01/02）。
- 批量接口的 500 上限、operation log 语义可被书架多选复用。
- 质量数据/动作 UI 已完成；B3 不再改质量页结构，只做样式与移动端适配。
