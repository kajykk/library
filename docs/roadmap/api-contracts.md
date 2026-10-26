# API 契约（B1–B5 新增）

> 所有端点都在 `/api` 下，除 `/api/health` 外均要求 `X-API-Token`（由 `main.py` 统一挂载）。未列出的现有端点行为不变。
>
> 约定：
> - id 一律为 UUID 字符串；时间为 ISO 8601 UTC。
> - 分页参数 `offset >= 0`、`limit` 按端点限幅；超出返回 422。
> - 业务失败用 4xx + `{"detail": "..."}`；校验失败用 FastAPI 默认 422。
> - **需要 `confirm: true` 的端点**（批量治理与入库类，风险高、影响面大）：`POST /api/documents/batch`、`POST /api/documents/batch/delete`、`POST /api/imports/execute`、`POST /api/documents/reextract`、`POST /api/documents/normalize-language`、`POST /api/documents/orphans/quarantine`、`POST /api/operations/{id}/undo`，以及 `POST /api/semantic/index` 中 `full=true` 的整库重建。缺少 `confirm` 返回 400，不执行任何写。
> - **不要求 `confirm` 的端点**：B4 的队列增删改与评分/推迟（用户单次点击、可逆、只影响自己）。这些动作不给它们加二次确认弹窗，否则「加入队列」这类轻操作会被打断。
> - 批量端点单次最多 500 个文档/文件；超过返回 400。
> - 新增端点不得改变现有端点的默认行为。

## 1. 质量中心（B1）

### `GET /api/quality/summary`

```json
{
  "total": 1298,
  "audited": 1298,
  "stale": false,
  "last_audited_at": "2026-10-26T12:00:00Z",
  "min_content_chars": 500,
  "counts": {
    "empty_content": 581,
    "short_content": 602,
    "no_cover": 515,
    "unclassified": 32,
    "needs_ocr": 2,
    "missing_file": 0,
    "duplicate": 6,
    "language_variant": 32,
    "orphan_cover": 0
  },
  "duplicate_groups": 3,
  "orphan_files": 0,
  "orphan_covers": 0
}
```

- `stale=true` 当任一存活文档 `updated_at > last_audited_at`，或从未审计。
- `audited` 是 `document_quality` 中对应存活文档的行数。
- `counts.empty_content` 等为存活 `book/pdf` 文档计数；`duplicate` 是「处于重复组中的文档数」，`duplicate_groups` 是组数。

### `GET /api/quality/issues`

查询参数：`issue`（必填，问题码或 `all`）、`limit`（默认 50，最大 200）、`offset`。

```json
{
  "total": 581,
  "items": [
    {
      "document_id": "uuid",
      "title": "书名",
      "type": "book",
      "format": "pdf",
      "language": "zh",
      "content_chars": 0,
      "file_size": 64995328,
      "cover_path": null,
      "collection_id": null,
      "issues": ["empty_content", "needs_ocr"],
      "checked_at": "2026-10-26T12:00:00Z"
    }
  ]
}
```

- `issue=all` 返回 `issues` 非空的文档。
- 列表不返回正文，不返回绝对路径。

### `GET /api/quality/orphans`

```json
{
  "orphan_files": [{ "rel_path": "files/xxx.pdf", "size": 12345 }],
  "orphan_covers": [{ "rel_path": "covers/xxx.jpg", "size": 2345 }],
  "missing_files": [
    { "document_id": "uuid", "title": "书名", "rel_path": "files/xxx.pdf" }
  ],
  "total_bytes": 14690
}
```

- 「孤儿」= 磁盘上存在、但没有任何文档（含软删除）引用的文件。
- 只返回 `data/` 内相对路径。

### `POST /api/quality/audit`

```json
{ "ids": ["uuid"], "full": false }
```

- `ids` 省略或 `full=true`：审计全部存活 `book/pdf`。
- 后台执行，返回 202 `{ "status": "started" }`；已在运行返回 409。
- 审计只写 `document_quality`，不改 `documents`。

### `GET /api/quality/audit-status`

```json
{
  "name": "quality_audit",
  "running": true,
  "total": 1298,
  "done": 300,
  "error": null,
  "started_at": "2026-10-26T12:00:00Z",
  "finished_at": null
}
```

## 2. 书架筛选、排序与分面（B1/B3）

### `GET /api/documents` 新增参数

| 参数 | 值 | 说明 |
|---|---|---|
| `quality` | `issues` / 问题码 / `not_audited` | 质量筛选；与 `types`、`q` 等现有参数组合 |
| `format` | 逗号分隔多值（`epub,pdf`）取 OR | 文件格式，与 `types`（文档类型作用域）不同；未知值不报错，返回空集 |
| `language` | `zh` / `en` / `ja` / `und` … | **归一化后**比较：`lower()` 后取 `-` 前段，`en-US`、`ENG`、`EN-US` 都命中 `en` |
| `sort` | `created_desc`（默认）/ `created_asc` / `title_asc` / `author_asc` / `last_read_desc` / `progress_desc` / `file_size_desc` | 白名单 7 项（见 [B3-01](B3-library-ui.md)），未知值 400；每种排序末位追加 `Document.id` tiebreaker，保证 offset 分页不重复不丢失 |

`sort` 的 SQL 映射：`title_asc` = `title ASC, id ASC`；`author_asc` = `coalesce(author,'') ASC, title ASC, id ASC`；`last_read_desc` = `last_read_at IS NULL, last_read_at DESC, id DESC`；`progress_desc` = `coalesce(read_progress,0) DESC, id DESC`；`file_size_desc` = `file_size DESC, id DESC`。

`GET /api/documents/count` 必须接受同一组 `quality`/`format`/`language` 参数，保证「共 N 本」与列表一致。`count`、`facets`、`ids` 不接受 `sort`（传了忽略，不报错）。

### `GET /api/documents/facets`

查询参数与列表一致（`types`、`collection_id`、`q`、`author`、`status`、`quality`、`format`、`language`、`tag`），不含 `sort/offset/limit`。

```json
{
  "total": 1297,
  "formats": [{ "value": "pdf", "count": 364 }],
  "languages": [{ "value": "zh", "count": 1087 }],
  "tags": [{ "value": "历史", "count": 42 }],
  "authors": [{ "value": "张三", "count": 5 }],
  "categories": [{ "collection_id": "uuid", "name": "历史", "count": 30 }],
  "uncategorized": 32,
  "truncated": { "tags": false, "authors": false }
}
```

- **自排除规则**（必须实现）：每一维用「除自身以外」的全部当前筛选统计；`types`、`q`、`status`、`quality` 永远参与；只有 `format`/`language`/`tag`/`author`/`collection_id` 在统计自己那一维时排除。否则选中 `format=pdf` 后其他格式计数归零，用户无法切换。
- 每维 `ORDER BY count DESC, value ASC`、`LIMIT 100`；`tags`/`authors` 命中超过 100 时对应 `truncated` 为 `true`。
- `authors` 排除空/纯空白；`languages` 按归一化值分组；`tags` 用 `COUNT(DISTINCT document_id)`，避免多标签 join 放大。
- `categories` 走 `LEFT JOIN collections`；未分类不进列表，只进 `uncategorized`，且与 `documents.stats` 的同名字段一致。
- 全部走 SQL 聚合，不得把文档读进 Python 再计数。
- 只统计存活 `book/pdf`；`total` 与同参数下的 `documents/count` 一致。

### `GET /api/documents/ids`

查询参数与列表一致；额外 `max`（默认 5000，最大 5000）。

```json
{ "ids": ["uuid"], "total": 1298, "truncated": false }
```

- 由 [B2-04](B2-import-batch.md) 交付；B2-12（书架多选）与 [B3-03](B3-library-ui.md)（「全选筛选结果」前端 hook）共用。单次批量操作仍最多 500。
- 超过 `max` 时 `truncated=true`，前端提示缩小筛选并把已选截断到 500，不直接发起 >500 的批量请求。

## 3. 导入（B2）

### `GET /api/imports/dirs`

```json
{
  "dirs": [
    { "id": "downloads", "label": "Downloads", "path": "C:\\Users\\k\\Downloads", "exists": true, "readable": true }
  ]
}
```

- 只返回 `KB_IMPORT_DIRS` 白名单中的目录；未配置时返回空数组。
- `id` 由服务端对路径做稳定哈希生成，客户端只回传 id。

### `POST /api/imports/scan`

```json
{ "dir_id": "downloads", "recurse": true, "compute_hashes": true }
```

- 202 `{ "scan_id": "uuid", "status": "running" }`；同一目录已有扫描在跑返回 409。
- 扫描只读，不移动/复制源文件。

### `GET /api/imports/scans/{scan_id}`

```json
{
  "scan_id": "uuid",
  "status": "done",
  "dir": "C:\\Users\\k\\Downloads",
  "started_at": "2026-10-26T12:00:00Z",
  "finished_at": "2026-10-26T12:00:20Z",
  "counts": { "ready": 12, "duplicate_in_db": 3, "duplicate_in_scan": 0, "broken": 19, "unsupported": 4, "too_large": 0 },
  "items": [
    {
      "rel_path": "book.epub",
      "size": 1234567,
      "mtime": "2026-10-20T08:00:00Z",
      "format": "epub",
      "sha256": "…",
      "status": "ready",
      "reason": null
    }
  ]
}
```

`status`：`running` / `done` / `failed`。扫描结果保存在 `data/import_scans/{scan_id}.json`，24 小时后可清理；过期执行返回 409。

### `POST /api/imports/execute`

```json
{
  "scan_id": "uuid",
  "rel_paths": ["book.epub"],
  "collection_id": null,
  "confirm": true
}
```

- `rel_paths` 省略 = 导入扫描中所有 `ready` 项；最多 500。
- 202 `{ "operation_id": "uuid", "status": "running" }`。
- 每导入成功一本写一条 `operation_logs`（kind=`directory_import`，可撤销=软删除）。
- 失败项不中断整批；逐项返回原因。
- 源文件永不删除；落盘失败会清理本次新建的 data 文件。

### `GET /api/imports/operations/{operation_id}`

```json
{
  "operation_id": "uuid",
  "status": "done",
  "total": 12,
  "done": 12,
  "imported": 11,
  "skipped": 1,
  "failed": 0,
  "results": [
    { "rel_path": "book.epub", "status": "imported", "document_id": "uuid", "title": "书名" },
    { "rel_path": "dup.pdf", "status": "skipped", "reason": "duplicate_in_db" }
  ]
}
```

## 4. 批量操作与撤销（B2）

### `PATCH /api/documents/batch`

```json
{
  "ids": ["uuid"],
  "patch": {
    "collection_id": "uuid-or-null",
    "language": "zh",
    "add_tags": ["历史"],
    "remove_tags": ["待整理"]
  },
  "confirm": true
}
```

- `ids` 1–500；所有文档必须存在且未软删除，否则 400 并返回 `missing_ids`。
- 事务内全成或全败；成功写 `operation_logs`（kind=`batch_update`，undoable=true）。
- 返回 `{ "updated": 12, "operation_id": "uuid" }`。
- `patch` 至少包含一个字段；`collection_id: null` 表示移出分类。

### `POST /api/documents/batch/delete`

```json
{ "ids": ["uuid"], "confirm": true }
```

- 只做软删除（`deleted_at`），不删文件。
- 返回 `{ "deleted": 12, "operation_id": "uuid" }`。

### `GET /api/operations`

查询：`kind`、`status`、`limit`（默认 20，最大 100）、`offset`。

```json
{
  "total": 5,
  "items": [
    {
      "id": "uuid",
      "kind": "batch_update",
      "status": "applied",
      "summary": "批量移入 分类A：12 本",
      "document_count": 12,
      "undoable": true,
      "created_at": "2026-10-26T12:00:00Z",
      "undone_at": null
    }
  ]
}
```

### `GET /api/operations/{operation_id}`

返回同上单条，额外包含 `document_ids`、`before_state`、`after_state`（已脱敏，不含正文）。

### `POST /api/operations/{operation_id}/undo`

```json
{ "confirm": true }
```

- 撤销属于真实数据写入，遵循第 2 行的全局约定：缺少 `confirm: true` 返回 400，不执行任何写。
- 预览（不落库）由质量中心/书架 UI 在点击「撤销」前用确认弹窗承担；本端点不提供 dry-run。

```json
{
  "status": "undone",
  "restored": 12,
  "skipped": [{ "document_id": "uuid", "reason": "changed_since_operation" }]
}
```

- `status`：`undone` / `partial` / `not_undoable` / `already_undone`。
- 不可撤销操作返回 400 + `{"detail": "operation is not undoable"}`。

## 5. 封面、重提取、语言归一化、孤儿隔离（B2）

### `PUT /api/documents/{doc_id}/cover`

`multipart/form-data`，字段 `file`。

- 接受 JPEG/PNG/WebP/GIF，按**魔数**判断，不信任扩展名和 Content-Type。
- 最大 8 MB；超限 413。
- 保存到 `data/covers/{doc_id}.{ext}`，更新 `cover_path`，写 `operation_logs`（kind=`cover_upload`，undoable=true，记录旧路径）。
- 返回 `DocumentOut`。

### `POST /api/documents/reextract`

```json
{
  "ids": ["uuid"],
  "timeout_seconds": 30,
  "overwrite": "only_if_longer",
  "confirm": true
}
```

- `ids` 1–500；`overwrite`：`only_if_longer`（默认）/ `always`。
- 后台任务；`GET /api/documents/reextract-status` 返回 `JobState` 快照，额外字段 `updated/skipped/empty/failed/current_title`。
- 不改变 `content_extract.extract_text_from_file` 签名；使用新增的带预算函数。
- 新文本为空时**绝不覆盖**旧正文；`only_if_longer` 时也更长才写。
- `operation_logs` kind=`reextract`，undoable=false，记录前后字数。

### `POST /api/documents/normalize-language`

```json
{ "dry_run": true, "ids": ["uuid"], "confirm": false }
```

- `ids` 省略 = 全部存活 `book/pdf`，但按 500 一批拆分执行。
- `dry_run=true` 只返回变更预览，不写库，不需要 `confirm`。
- 应用时 `confirm=true`；每批写一条 `operation_logs`（kind=`language_normalize`，undoable=true）。
- 映射：`en*` → `en`，`zh*` → `zh`，`ja*` → `ja`，空 → `und`；其他值保持不变并列入 `unchanged`。

响应：

```json
{
  "dry_run": true,
  "total": 32,
  "changes": [{ "document_id": "uuid", "title": "书名", "from": "en-US", "to": "en" }],
  "unchanged": 5,
  "batches": 1
}
```

### `GET /api/documents/orphans`

同 `GET /api/quality/orphans`（B2 复用），供隔离动作选文件。

### `POST /api/documents/orphans/quarantine`

```json
{ "rel_paths": ["files/xxx.pdf"], "confirm": true }
```

- 1–500 个文件；目标移动到 `data/quarantine/files/{timestamp}/{原相对路径}`。
- 写 `manifest.json`（原路径、大小、sha256、移动时间）和 `operation_logs`（kind=`orphan_quarantine`，undoable=true）。
- 目标已存在则跳过；永不删除文件。
- 返回 `{ "moved": 12, "skipped": 0, "operation_id": "uuid", "quarantine_dir": "..." }`。

## 6. 阅读队列（B4）

### 队列项响应

```json
{
  "id": "uuid",
  "document_id": "uuid",
  "priority": 100,
  "sort_order": 0,
  "status": "active",
  "note": "想先读这本",
  "created_at": "2026-10-26T12:00:00Z",
  "updated_at": "2026-10-26T12:00:00Z",
  "document": {
    "id": "uuid",
    "title": "书名",
    "author": "作者",
    "format": "epub",
    "cover_path": "covers/uuid.jpg",
    "read_progress": 0.42,
    "last_read_at": "2026-10-20T08:00:00Z"
  }
}
```

### `GET /api/reading-queue`

查询：`status`（默认 `active`）、`limit`（默认 50，最大 200）、`offset`。

返回 `{ "total": 3, "items": [队列项] }`，按 `priority, sort_order, created_at` 升序。

### `POST /api/reading-queue`

```json
{ "document_id": "uuid", "priority": 100, "note": "" }
```

- 文档必须存在且未软删除。
- 幂等：已存在则返回 200 + `{ "created": false, "item": 队列项 }`；新建返回 201 + `{ "created": true, "item": 队列项 }`。

### `PATCH /api/reading-queue/{item_id}`

```json
{ "priority": 10, "status": "paused", "note": "稍后读" }
```

- 只允许 `priority`、`status`、`note`；返回队列项。
- `status`：`active` / `paused` / `done`。

### `DELETE /api/reading-queue/{item_id}`

- 只删除队列项，不删除文档；204。

### `POST /api/reading-queue/reorder`

```json
{ "ordered_ids": ["uuid", "uuid"] }
```

- 1–500；必须全部属于当前队列且 `status=active`，否则 400。
- 按数组顺序写入 `sort_order=0..n-1`；返回 `{ "items": [队列项] }`。

### `POST /api/reading-queue/auto-add`

```json
{ "document_id": "uuid" }
```

阅读进度上报时的幂等自动入队（前端 fire-and-forget，失败静默）：

- 文档 `read_progress >= 1`、已软删除、或类型不在 `book`/`pdf` → `{ "created": false, "skipped": "not_applicable" }`，不建行。
- 已存在队列项（任意 `status`）→ `{ "created": false, "skipped": "exists" }`；**不把 `paused`/`done` 复活成 `active`**。
- 否则新建 `priority=100, sort_order=0, status='active', note=''`，返回 `{ "created": true, "item": 队列项 }`。
- 并发重复靠 `document_id UNIQUE` 兜底：捕获 `IntegrityError` 后回查并返回 `skipped:"exists"`，不返回 500。
- 重复返回一律 200；仅真正新建返回 201。

### `GET /api/reading-queue/next`

返回第一个 `active` 队列项；空队列 204。

## 7. 间隔复习（B4）

### 复习卡

```json
{
  "annotation_id": "uuid",
  "document_id": "uuid",
  "document_title": "书名",
  "document_type": "book",
  "type": "highlight",
  "anchor": { "chapter": 3, "page": 42 },
  "quote": "划线原文",
  "content": "我的批注",
  "created_at": "2026-10-01T08:00:00Z",
  "due_at": "2026-10-26T00:00:00Z",
  "interval_days": 3,
  "repetitions": 2,
  "ease": 2.5,
  "preview": {
    "again": "1 天",
    "hard": "2 天",
    "good": "8 天",
    "easy": "10 天"
  }
}
```

### `GET /api/reviews/due`

查询：`limit`（默认 20，最大 100）、`new_limit`（默认 20，最大 100）、`document_id`（可选，只看某本书）。

```json
{ "total": 42, "cards": [复习卡] }
```

- 只取 `status=active`、`due_at <= now_utc`、文档未软删除、标注类型 ∈ `highlight`/`note` 的复习行。
- 排序：**新卡优先**——`interval_days = 0` 的按 `created_at` 升序排在前面，其余按 `due_at` 升序，两组拼接；新卡总量单独受 `new_limit` 限制，避免一次涌出上千条。
- `total` 是**同筛选条件下的到期总数**（可大于 `cards` 长度），前端用于显示「还有 N 张」。
- 复习卡的 `preview` 由服务端调度函数生成，前端不自行计算间隔。

### `POST /api/reviews/{annotation_id}/rate`

```json
{ "rating": "good" }
```

`rating`：`again` / `hard` / `good` / `easy`。

```json
{
  "annotation_id": "uuid",
  "due_at": "2026-11-03T00:00:00Z",
  "interval_days": 8,
  "ease": 2.5,
  "repetitions": 3,
  "lapses": 0,
  "next_preview": { "again": "1 天", "hard": "7 天", "good": "20 天", "easy": "26 天" }
}
```

- 复习行不存在时自动创建（兼容 0010 之前/迁移后新增的标注）。
- 重复点击由前端禁用以外的并发不做幂等保证；以最后一次评分为准。

### `POST /api/reviews/{annotation_id}/snooze`

```json
{ "days": 3 }
```

- `days` 只能是 1 / 3 / 7；`due_at = now + days`，不改变 `interval_days/ease/repetitions`。
- 返回同 rate 的调度字段。

### `GET /api/reviews/stats`

查询：`tz_offset_minutes`（默认 0，范围 -840..840，表示「本地时间 = UTC + offset」）。

```json
{
  "due_now": 42,
  "due_today": 50,
  "new_today": 20,
  "reviewed_today": 7,
  "upcoming_7d": 120
}
```

**字段口径（与 [B4-04](B4-reading-review.md) 逐字一致，不得自行改定义）**：

| 字段 | 定义 |
|---|---|
| `due_now` | `due_at <= now_utc` 的 active 复习行总数（含逾期） |
| `due_today` | `now_utc < due_at <= 本地今日结束` 的总数（今天稍后才到期，**不含逾期**） |
| `new_today` | 今日新增的 `highlight`/`note` 标注数（`created_at` 落在本地今日） |
| `reviewed_today` | `last_rated_at` 落在本地今日的复习行数 |
| `upcoming_7d` | `now_utc < due_at <= now_utc + 7d` 的总数 |

- 本地日界必须在 SQL 里用偏移表达式完成，不得把复习行全读进 Python 计数。
- 偏移单位是 **`minutes`**：`strftime('%Y-%m-%d', col, '+480 minutes')`。写成 `hours` 会把 +08:00 当成 +480 小时，是必须避免的实现错误。
- 五个字段互不重叠的语义要写进测试：同一张逾期卡计入 `due_now`，不计入 `due_today`。

## 8. 语义检索与 RAG（B5）

> 所有端点默认关闭；`KB_SEMANTIC_ENABLED=0` 时返回 503 + `{"detail": "semantic search disabled"}`。关闭时不得下载模型、不得加载索引。

### `GET /api/semantic/status`

```json
{
  "enabled": true,
  "provider": "fastembed",
  "model": "intfloat/multilingual-e5-small",
  "dim": 384,
  "vector_backend": "numpy",
  "documents_indexed": 717,
  "documents_total": 1298,
  "chunks": 15230,
  "last_indexed_at": "2026-10-26T12:00:00Z"
}
```

### `POST /api/semantic/index`

```json
{ "ids": ["uuid"], "full": false, "confirm": true }
```

- 后台任务；409 表示已在运行。
- `ids` 省略 + `full=false`：增量（只处理 `content_sha256` 变化的文档）。
- `full=true`：删除并重建所有 shard（需要 `confirm=true`）。
- 返回 202 `{ "status": "started" }`；`GET /api/semantic/index-status` 返回 `JobState` + `current_title`。

### `GET /api/search/semantic`

查询：`q`（必填）、`mode`（`semantic` 默认 / `hybrid`）、`type`、`limit`（默认 20，最大 100）。

```json
{
  "query": "为什么人会高估自己",
  "mode": "hybrid",
  "hits": [
    {
      "id": "uuid",
      "title": "书名",
      "type": "book",
      "snippet": "……相关片段……",
      "score": 0.83,
      "channels": ["semantic", "lexical"]
    }
  ]
}
```

- `hybrid` 用 RRF（k=60）融合现有 FTS/ILIKE 结果与语义结果；同 id 合并。
- 语义结果片段来自 `document_chunks.text`，不返回正文全文。
- 索引为空时：`semantic` 返回 200 + 空数组 + `"index_empty": true`；`hybrid` 退化为现有全文结果。

### `POST /api/rag/answer`

```json
{ "q": "为什么人会高估自己", "top_k": 5 }
```

```json
{
  "mode": "extractive",
  "query": "为什么人会高估自己",
  "answer": "根据《书名》第 3 章：……（原文引用）",
  "citations": [
    {
      "document_id": "uuid",
      "title": "书名",
      "chunk_index": 12,
      "quote": "原文片段",
      "score": 0.83
    }
  ]
}
```

- `mode`：`extractive`（默认）或 `generative`（仅当 `KB_RAG_GENERATIVE=1`）。
- 生成式只允许本地 LLM 端点（默认 `http://127.0.0.1:11434`）；远端地址需 `KB_RAG_ALLOW_REMOTE=1` 且用户书面同意。
- 引用必须至少 1 条；没有可用片段返回 200 + `{"answer": "", "citations": []}`。
- 任何情况下不把未引用的内容写成答案。

## 9. 健康检查扩展（B5，可选）

`GET /api/health` 的 `checks` 增加：

```json
{ "semantic": "disabled" }
```

取值：`disabled`（功能关闭）/ `ok` / `missing`（开启但索引不存在）/ `error`。该字段不改变现有 `status` 语义；索引 missing 不把整体状态降级为 error。

## 10. 限流与规模

- 复用现有 `RateLimitMiddleware`。新增 CPU 密集端点建议路径级限流：
  - `/api/quality/audit`：2/60s
  - `/api/imports/scan`、`/api/imports/execute`：2/60s
  - `/api/documents/reextract`：2/60s
  - `/api/semantic/index`：2/60s
  - `/api/rag/answer`：20/60s
- 未在 B0–B4 新增依赖的前提下，限流规则写在现有 middleware 配置中，不引入新库。

## 11. 兼容性检查

- 现有前端 `src/lib/api/client.ts` 的 `request()` 已兼容 `detail` 为字符串或对象。
- 新增响应字段一律追加，不删除/重命名旧字段。
- `GET /api/documents` 不传 `sort/quality` 时行为与当前完全一致（`created_at.desc()`）。
- `GET /api/search` 行为不变；语义检索走独立端点。
- `POST /api/annotations` 响应不变；复习行在服务端事务内创建，不改变响应体。
