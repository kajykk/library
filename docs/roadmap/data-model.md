# 数据模型与迁移（0007–0011）

> 本文件是 B1–B5 的数据库契约。实现时必须与这里的表名、列名、索引名、回滚方式一致；需要偏离时先写进 [progress.md](progress.md) 的「任务书偏离记录」。
>
> 迁移链：`0006_add_sha256` → `0007_document_quality` → `0008_operation_logs` → `0009_library_indexes` → `0010_reading_queue_reviews` → `0011_semantic_search`。

## 0. 通用规则

- 所有迁移必须**幂等**：先 `sa.inspect(bind)` 检查表/列/索引是否存在，再创建。参考 [0006_add_sha256.py](../../server/alembic/versions/0006_add_sha256.py)。
- 所有迁移必须先备份（G3），并在 `alembic upgrade head` 后跑 `pragma integrity_check`。
- 新增表一律使用 `Uuid` 主键、`DateTime(timezone=True)`、`JSONVariant`（见 [models.py](../../server/app/models.py)）。
- 新增外键必须显式写 `ondelete`：
  - 质量/向量/复习数据跟随文档删除：`CASCADE`
  - 队列项跟随文档删除：`CASCADE`
  - 操作日志不跟随文档删除：保留审计痕迹
- 禁止在迁移里做全量正文回填、模型下载、网络请求。重活放后台任务。
- 所有批量回填必须可重复执行（`WHERE NOT EXISTS` 或唯一约束）。
- 迁移 `downgrade()` 必须能删除本迁移创建的对象；不能删除其他迁移的对象。

## 1. `0007_document_quality` · 文档质量快照

### 目的

把「文件是否存在、正文多少字、是否需要 OCR、是否重复」这些**不适合每次列表都算**的检查结果缓存下来。质量页与质量筛选读取它；它不替代 `documents` 的实时字段。

**重要**：B1 的「只读」指不改 `documents` 表。审计任务只写 `document_quality`；迁移本身需要 G3。

### 表结构

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `document_id` | Uuid | PK, FK `documents.id` ON DELETE CASCADE | 一本文档一行 |
| `content_chars` | Integer | NOT NULL, default 0 | `length(trim(coalesce(content,'')))`，审计时快照 |
| `file_exists` | Boolean | NOT NULL, default true | `file_path` 为空时也记 false |
| `cover_exists` | Boolean | NOT NULL, default true | `cover_path` 为空时记 false |
| `needs_ocr` | Boolean | NOT NULL, default false | 启发式：PDF + `content_chars < quality_min_content_chars` + 文件存在 |
| `duplicate_group_size` | Integer | NOT NULL, default 0 | 0/1 = 不重复；>1 = 同 `sha256` 的存活文档数 |
| `issues` | JSON | NOT NULL, default `[]` | 问题码列表，见下 |
| `scan_error` | Text | NULL | 文件读取/路径校验失败时的短错误，不含绝对路径 |
| `checked_at` | DateTime(tz) | NOT NULL | UTC |

问题码（稳定字符串，前后端共用）：

```text
missing_file        文件记录存在但磁盘不存在
orphan_cover        封面路径存在但磁盘不存在
empty_content       正文为空
short_content       正文 1 ~ quality_min_content_chars-1 字
no_cover            cover_path 为空
unclassified        collection_id 为空
needs_ocr           疑似扫描版 PDF
duplicate_sha       同 sha256 的存活文档 > 1
language_variant    语言不在 {zh, en, ja, und}
```

### 索引

| 索引名 | 列 |
|---|---|
| `ix_document_quality_needs_ocr` | `(needs_ocr)` |
| `ix_document_quality_duplicate_group_size` | `(duplicate_group_size)` |
| `ix_document_quality_checked_at` | `(checked_at)` |

### 写入规则

- `quality_audit.audit_one(db, doc, threshold)` 计算并 **upsert**。
- `quality_audit.audit_all()` 按批 100 条处理，使用独立 Session；通过 `JobState` 报告进度。
- 文档被软删除、硬删除时：
  - 软删除：质量行保留（便于恢复后仍可查看），但质量页默认只看存活文档。
  - 硬删除：`ON DELETE CASCADE` 自动清理。
- 文档 `updated_at > max(checked_at)` 时，质量页显示「数据已变更，建议重新审计」；筛选仍可用，但标注为基于快照。

### 回滚

`downgrade()`：先删索引，再 `drop_table("document_quality")`。这是纯派生数据，删除不需要备份内容。

### 容量估算

1298 行 × 约 200 B ≈ 0.3 MB；`issues` JSON 很小。可忽略。

## 2. `0008_operation_logs` · 批量操作审计与撤销

### 目的

所有真实数据写入（批量修改、软删除、导入、补封面、语言归一化、孤儿隔离）都必须留下可撤销记录。操作日志是 B2 的安全阀，也是 B6 数据清理的证据。

### 表结构

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `kind` | String(32) | NOT NULL, index | `batch_update` / `batch_soft_delete` / `directory_import` / `cover_upload` / `language_normalize` / `orphan_quarantine` / `reextract` |
| `status` | String(16) | NOT NULL, default `applied` | `applied` / `undone` / `partial` / `failed` |
| `document_ids` | JSON | NOT NULL, default `[]` | 影响的文档 id，最多 500 |
| `before_state` | JSON | NOT NULL, default `{}` | 只存可撤销字段，禁止存 `content` |
| `after_state` | JSON | NOT NULL, default `{}` | 同上；用于撤销时判断是否被后续修改 |
| `summary` | String(500) | NOT NULL, default `""` | 人类可读摘要，例如「批量移入 分类A：12 本」 |
| `undoable` | Boolean | NOT NULL, default false | 重提取等不可撤销操作为 false |
| `created_at` | DateTime(tz) | NOT NULL, default now | |
| `undone_at` | DateTime(tz) | NULL | |

### 撤销语义

- `batch_update`：逐文档比对当前字段与 `after_state`；相等才恢复 `before_state`；不等则 skipped（避免覆盖用户后续编辑）。
- `batch_soft_delete`：只恢复仍处于软删除状态的文档；已硬删除的 skipped。
- `directory_import`：软删除本次导入且仍存活的文档；**不删除源文件，也不删除已落盘文件**。
- `cover_upload`：恢复旧 `cover_path`；旧封面文件保留在磁盘，不立即删除。
- `language_normalize`：同 `batch_update`。
- `orphan_quarantine`：按 manifest 把文件移回原相对路径；目标已存在则 skipped。
- `reextract`：`undoable=false`，只记录前后字数与文档 id，不保存旧正文。

### 索引

| 索引名 | 列 |
|---|---|
| `ix_operation_logs_kind` | `(kind)` |
| `ix_operation_logs_created_at` | `(created_at)` |
| `ix_operation_logs_status` | `(status)` |

### 容量估算

每次最多 500 个文档；`before_state`/`after_state` 只存少量字段，单条通常 < 200 KB。保留全部日志，不自动清理。

### 回滚

`downgrade()`：`drop_table("operation_logs")`。删除日志会失去撤销能力，必须在报告中说明；生产环境不建议降级这个迁移。

## 3. `0009_library_indexes` · 书架查询索引

### 目的

B3 增加排序、分面、全选筛选结果后，现有仅按 `created_at` 的查询会出现全表扫描。迁移只加索引，不改表结构。

### 索引

| 索引名 | 表 | 列 | 服务的查询 |
|---|---|---|---|
| `ix_documents_alive_type_created` | documents | `(deleted_at, type, created_at)` | 默认书架 / 最近添加 |
| `ix_documents_alive_type_collection` | documents | `(deleted_at, type, collection_id, created_at)` | 分类筛选 |
| `ix_documents_alive_type_author` | documents | `(deleted_at, type, author)` | 作者筛选 / 分面 |
| `ix_documents_alive_type_last_read` | documents | `(deleted_at, type, last_read_at)` | 最近阅读排序 |
| `ix_documents_alive_type_progress` | documents | `(deleted_at, type, read_progress)` | 在读/读完 |
| `ix_documents_alive_updated` | documents | `(deleted_at, updated_at)` | 最近更新排序 |

说明：

- SQLite 索引包含 NULL，`deleted_at IS NULL` 可以使用这些索引。
- 不建 `lower(title)` 表达式索引：当前排序使用数据库默认 collation，跨 SQLite/PostgreSQL 行为一致优先。
- 不在本迁移里加 FTS 或 trigram 索引；FTS 由 0004 管理。

### 构建成本

真实库 765 MB、1298 行存活文档，索引构建预计秒级到分钟级。迁移前必须备份；若 `alembic upgrade` 超过 10 分钟，停止并记录到 progress.md。

### 回滚

`downgrade()`：按相反顺序 `drop_index`。索引可重建，不涉及数据。

## 4. `0010_reading_queue_reviews` · 阅读队列与间隔复习

### 4.1 `reading_queue_items`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `document_id` | Uuid | UNIQUE, FK `documents.id` ON DELETE CASCADE | 一本书最多一个队列项 |
| `priority` | Integer | NOT NULL, default 100 | 越小越靠前；0–1000 |
| `sort_order` | Integer | NOT NULL, default 0 | 拖拽/重排用；同一时刻允许重复，读取时 `priority, sort_order, created_at` |
| `status` | String(16) | NOT NULL, default `active` | `active` / `paused` / `done` |
| `note` | Text | NOT NULL, default `""` | 为什么读这本，最多 500 字（API 校验） |
| `created_at` | DateTime(tz) | NOT NULL | |
| `updated_at` | DateTime(tz) | NOT NULL, onupdate | |

索引：

| 索引名 | 列 |
|---|---|
| `ix_reading_queue_status_priority` | `(status, priority, sort_order, created_at)` |
| `ix_reading_queue_document_id` | `(document_id)`（UNIQUE 已隐含，显式命名便于迁移） |

### 4.2 `annotation_reviews`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `annotation_id` | Uuid | PK, FK `annotations.id` ON DELETE CASCADE | 一条标注一行 |
| `due_at` | DateTime(tz) | NOT NULL | UTC |
| `interval_days` | Integer | NOT NULL, default 0 | 0 = 新卡 |
| `ease` | Float | NOT NULL, default 2.5 | 1.3–2.8 |
| `repetitions` | Integer | NOT NULL, default 0 | 连续答对次数 |
| `lapses` | Integer | NOT NULL, default 0 | 遗忘次数 |
| `status` | String(16) | NOT NULL, default `active` | `active` / `suspended` |
| `last_rated_at` | DateTime(tz) | NULL | |
| `created_at` | DateTime(tz) | NOT NULL | |
| `updated_at` | DateTime(tz) | NOT NULL, onupdate | |

索引：

| 索引名 | 列 |
|---|---|
| `ix_annotation_reviews_status_due` | `(status, due_at)` |
| `ix_annotation_reviews_due_at` | `(due_at)` |

### 4.3 回填

迁移内只做**结构化回填**，不调用调度算法：

1. 阅读队列：
   ```sql
   INSERT INTO reading_queue_items (...)
   SELECT ... FROM documents d
   WHERE d.deleted_at IS NULL
     AND d.type IN ('book','pdf')
     AND d.read_progress > 0
     AND d.read_progress < 1
     AND NOT EXISTS (SELECT 1 FROM reading_queue_items q WHERE q.document_id = d.id)
   ```
   `priority=100`，`status='active'`，`note=''`。
2. 复习行：为所有 `annotation.type IN ('highlight','note')` 且文档未删除的标注建行：
   - **到期时间打散**（避免几万条同一天涌出）：按 `created_at` 升序取行号 `rn`（从 0 起），
     `due_at = now_utc + timedelta(days=1 + rn % 14)`，即未来 14 天内均匀铺开。
   - `interval_days=0`、`ease=2.5`、`repetitions=0`、`lapses=0`、`status='active'`
   - 书签（`bookmark`）不建复习行；书签是位置标记，不是记忆卡。
3. 实现方式（与 [B4-01](B4-reading-review.md) 一致）：用 Python 取 id 列表 + 分块 500 行 `insert().values([...])`，**不用窗口函数**（跨 SQLite/PostgreSQL 可移植，且天然满足分块、避免长事务）。幂等由 Python 侧先查已有 `annotation_id` 集合实现，重复执行迁移不产生重复行。
4. **时区注意**：SQLite 落库的是 naive UTC 文本，读回也是 naive；回填与后续比较统一走 `as_utc_naive()` / `from_utc_naive()`（B4-02 提供），不要在迁移或路由里裸比 tz-aware 与 naive。

### 回滚

`downgrade()`：先删 `annotation_reviews` 索引和表，再删 `reading_queue_items`。回填数据随表删除；这是新功能数据，不影响 `documents`/`annotations`。

## 5. `0011_semantic_search` · 语义检索元数据

### 目的

只把**可重建的派生数据**放进库。向量本体不强制进 SQLite，避免迁移依赖 `sqlite-vec`；B5 spike 决定最终存储后端。

### 5.1 `document_chunks`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | 同时也是向量行 id |
| `document_id` | Uuid | FK `documents.id` ON DELETE CASCADE, index | |
| `chunk_index` | Integer | NOT NULL | 0 开始 |
| `text` | Text | NOT NULL | 切片原文，最多 1200 字 |
| `start_char` | Integer | NOT NULL | 在 `documents.content` 中的起点 |
| `end_char` | Integer | NOT NULL | 终点（不含） |
| `created_at` | DateTime(tz) | NOT NULL | |

唯一约束：`uq_document_chunks_doc_index` on `(document_id, chunk_index)`。

索引：

| 索引名 | 列 |
|---|---|
| `ix_document_chunks_document_id` | `(document_id)` |
| `ix_document_chunks_doc_index` | `(document_id, chunk_index)`（唯一约束已隐含，显式命名） |

### 5.2 `document_embedding_state`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `document_id` | Uuid | PK, FK `documents.id` ON DELETE CASCADE | |
| `content_sha256` | String(64) | NOT NULL | `documents.content` 的哈希；变化才重嵌入 |
| `model` | String(128) | NOT NULL | 例如 `intfloat/multilingual-e5-small` |
| `dim` | Integer | NOT NULL | 向量维度 |
| `chunk_count` | Integer | NOT NULL | |
| `indexed_at` | DateTime(tz) | NOT NULL | |

索引：`ix_document_embedding_state_indexed_at` on `(indexed_at)`。

### 5.3 向量存储（spike 后定稿）

默认方案（不依赖新 SQLite 扩展）：

```text
{data_dir}/semantic/{model_slug}/
  manifest.json                 # schema_version, model, dim, 创建/更新时间
  shards/{document_id}.npy      # float32 [chunk_count, dim]
  shards/{document_id}.ids.json # chunk id 顺序，可选（也可按 chunk_index 排序）
```

- 每个文档一个 shard，增量索引只重写该文档的 shard。
- 查询时把所有 shard 载入内存做点积；1298 本 × 约 20 chunks × 384 维 × 4 B ≈ 40 MB，可接受。
- 若 B5-01 证明 numpy 暴力检索超过 500 ms，再在 B5-03 引入 `sqlite-vec` 或 `hnswlib`；届时本文件追加「向量后端变更」一节，不在迁移里硬编码。
- 向量目录必须加入 `.gitignore`；不得提交到仓库。

### 回滚

`downgrade()`：先删 `document_embedding_state`，再删 `document_chunks`。向量目录由 B5 的清理脚本删除；数据库降级不会自动删文件，报告里要写明手动清理命令。

## 6. 迁移执行顺序（每批一次）

| 批次 | 迁移 | 前置授权 | 备份 | 验证 |
|---|---|---|---|---|
| B1 | `0007` | G3 | B0-02 备份 + 本次备份 | `alembic upgrade head`；`integrity_check`；表/索引存在 |
| B2 | `0008` | G3 | 同上 | 同上 |
| B3 | `0009` | G3 | 同上 | `EXPLAIN QUERY PLAN` 抽查 3 条书架查询 |
| B4 | `0010` | G3 | 同上 | 回填行数 = 预期；抽样 5 条 |
| B5 | `0011` | G3 + B5 go | 同上 | 表存在；无向量数据（索引任务未跑） |

## 7. 真实库操作前后检查清单

每次迁移或批量写入前后都要记录：

```python
# 只读检查
alive = select count(*) from documents where deleted_at is null
soft_deleted = select count(*) from documents where deleted_at is not null
integrity = pragma integrity_check
page_count / freelist_count = pragma page_count / pragma freelist_count
```

把结果写进对应批次报告。计数变化超出预期时，停止并进入 [progress.md](progress.md) 的「发现」区。
