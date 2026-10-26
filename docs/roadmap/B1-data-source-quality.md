# B1 · 数据源可见化 + 质量中心（只读）

| 项目 | 值 |
|---|---|
| 批次 | B1 |
| 状态 | TODO |
| 依赖 | B0 全部 DONE |
| 真实数据写入 | 不修改 `documents`；新增 `document_quality` 表（需 G3）与质量快照写入 |
| 迁移 | `0007_document_quality` |
| 预计改动 | 后端约 8 个文件、前端约 10 个文件 |
| 完成标志 | B1-01 ~ B1-11 全部 DONE，B1 报告已写 |

## 1. 目标

让用户第一次打开应用就知道三件事：

1. **当前连的是哪份数据**：API、本地、还是「配了但连不上」。
2. **库里缺什么**：正文为空、无封面、未分类、需 OCR、文件丢失、重复。
3. **下一步去哪修**：质量中心给出可筛选的清单，但本批不提供修改动作（修改在 B2）。

质量中心是**只读**的：审计任务只写新建的 `document_quality` 表，不更新 `documents` 的任何字段，不移动/删除任何文件。

## 2. 不做什么

- 不修任何文档数据：不补封面、不重提取、不批量分类、不软删除。
- 不安装 OCR、不调用外部元数据、不新增依赖。
- 不做语义检索。
- 不改书架整体布局（B3 做）；B1 只加质量筛选 chips 和状态条。
- 不把「本地模式」当成错误：本地模式是合法降级，只提示，不阻断使用。

## 3. 前置条件

1. B0 全部 DONE；`progress.md` 当前批次为 B1。
2. 已获得 **G3 迁移授权**（`0007_document_quality`）。
3. 已执行本批备份：

   ```bash
   python server/scripts/incremental_backup.py --apply --keep 7
   E:/zy/python/python.exe - <<'PY'
   import sqlite3
   con = sqlite3.connect(r"E:\code\projects\library\server\knowledge_base.db")
   print(con.execute("pragma integrity_check").fetchone())
   PY
   ```

4. 记录执行前计数（alive / soft_deleted / 正文为空数 / 无封面数 / 未分类数）。

## 4. 任务

### B1-01 `DocumentQuality` 模型 + 迁移 `0007_document_quality`

**文件**：

- 修改：[server/app/models.py](../../server/app/models.py)
- 新增：[server/alembic/versions/0007_document_quality.py](../../server/alembic/versions/0007_document_quality.py)
- 新增：[server/tests/test_quality.py](../../server/tests/test_quality.py)

**模型**（字段定义以 [data-model.md](data-model.md) 第 1 节为准）：

```python
class DocumentQuality(Base):
    __tablename__ = "document_quality"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    content_chars: Mapped[int] = mapped_column(Integer, default=0)
    file_exists: Mapped[bool] = mapped_column(Boolean, default=True)
    cover_exists: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_group_size: Mapped[int] = mapped_column(Integer, default=0)
    issues: Mapped[list] = mapped_column(JSONVariant, default=list)
    scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

**迁移要求**：

- `revision = "0007_document_quality"`，`down_revision = "0006_add_sha256"`。
- 用 `sa.inspect(bind)` 检查表/索引是否已存在；存在则跳过。
- 三个索引名与 [data-model.md](data-model.md) 完全一致。
- `downgrade()` 先删索引再删表。
- 不在迁移中插入数据。

**测试**：

- 迁移后表存在、列类型正确、索引存在。
- 删除文档会级联删除质量行（临时库测试）。
- 迁移重复执行不报错（在临时库上跑两次 `upgrade` 或调用幂等分支）。

**验收**：`alembic upgrade head` 成功；真实库 `pragma integrity_check=ok`；表行数为 0（审计尚未跑）。

---

### B1-02 `quality_audit` 审计服务

**新增**：[server/app/services/quality_audit.py](../../server/app/services/quality_audit.py)

**修改**：[server/app/services/sql_utils.py](../../server/app/services/sql_utils.py)（增加跨方言的「去空白后长度」表达式）

**公开函数**（B2 会复用，命名固定）：

```python
def normalize_language(raw: str | None) -> str: ...

def trimmed_content_length_expr(db: Session):
    """SQLite: length(trim(coalesce(content,'')))
       PostgreSQL: length(btrim(coalesce(content,'')))"""

def audit_one(db: Session, doc: Document, threshold: int) -> DocumentQuality: ...

def start_audit(ids: list[uuid.UUID] | None = None) -> bool: ...

def status() -> dict: ...

def scan_orphans(db: Session) -> dict: ...

def list_issues(db: Session, issue: str, limit: int, offset: int) -> tuple[int, list[dict]]: ...
```

**审计步骤**：

1. 只处理存活 `book/pdf`（`deleted_at is null`）；`note/article/webclip` 不进质量中心。
2. 一次查询算出重复组：
   ```sql
   SELECT sha256, count(*) FROM documents
   WHERE deleted_at IS NULL AND sha256 IS NOT NULL AND sha256 != ''
   GROUP BY sha256 HAVING count(*) > 1
   ```
3. 每 100 条一批，逐文档计算：
   - `content_chars`：用 `trimmed_content_length_expr` 在 SQL 里算，**不要把全文读进 Python**。
   - `file_exists`：`file_path` 非空且 `file_storage.abs_path(file_path).is_file()`；异常记 `scan_error`，不抛整批。
   - `cover_exists`：`cover_path` 非空且文件存在。
   - `needs_ocr`：`format == 'pdf'` 且 `content_chars < threshold` 且 `file_exists`。
   - `duplicate_group_size`：来自第 2 步的 map；不重复记 0。
   - `issues`：按 [data-model.md](data-model.md) 的 9 个问题码生成；`short_content` 仅在 `0 < chars < threshold` 时出现。
   - `checked_at = utcnow()`。
4. upsert 用 SQLAlchemy `merge` 或「先查后更新/插入」，避免 SQLite/PostgreSQL 方言差异。
5. 每批 commit；使用独立 `SessionLocal`，不占用请求 Session。
6. 任务状态用 B0 的 `JobState("quality_audit")`；`start_audit` 单飞。

**`scan_orphans`**：

- 引用集合 = 所有 `documents.file_path` + `documents.cover_path`（**包含软删除**，避免把可恢复文件当孤儿）。
- 遍历 `data/files`、`data/covers`，只收普通文件；不跟随符号链接。
- 返回 `{orphan_files, orphan_covers, missing_files, total_bytes}`；`rel_path` 使用 `/` 分隔。
- 单次遍历最多 20000 个文件；超过时返回 `truncated=true` 并提示分批。

**测试**（`server/tests/test_quality_audit.py`）：

- 构造：空正文、短正文、无封面、未分类、`en-US`、重复 sha、缺失文件（`file_path` 指向不存在）、正常书。
- 断言每本书的 `issues` 集合与 `needs_ocr/duplicate_group_size`。
- 断言审计不修改 `documents.updated_at`。
- `scan_orphans` 能同时报告孤儿文件与缺失文件；软删除文档引用的文件不算孤儿。
- `normalize_language`：`en-US/eng/EN-US -> en`，`zh-TW/zh-Hans -> zh`，空 -> `und`，`fr` 保持不变。

**验收**：临时库全绿；真实库 dry-run（不写）先跑一次统计，数字与基线一致或在报告中解释差异。

---

### B1-03 `quality` 路由并注册

**新增**：[server/app/routers/quality.py](../../server/app/routers/quality.py)

**修改**：[server/app/main.py](../../server/app/main.py)（import + `include_router`，前缀 `/api`，继承鉴权）

**端点**（契约见 [api-contracts.md](api-contracts.md) 第 1 节）：

- `GET /api/quality/summary`
- `GET /api/quality/issues?issue=&limit=&offset=`
- `GET /api/quality/orphans`
- `POST /api/quality/audit`
- `GET /api/quality/audit-status`

**实现要求**：

- `summary` 的 `counts` 用 SQL 聚合 + `document_quality` 表，不逐文档 Python 循环。
- `stale`：
  ```sql
  SELECT max(checked_at) FROM document_quality;
  SELECT count(*) FROM documents d
  LEFT JOIN document_quality q ON q.document_id = d.id
  WHERE d.deleted_at IS NULL AND d.type IN ('book','pdf')
    AND (q.document_id IS NULL OR d.updated_at > q.checked_at);
  ```
  `stale = stale_count > 0`。
- `audit` 的 `ids` 最多 500；`full=true` 与 `ids` 同时出现返回 400。
- 任务运行中再次启动返回 409。
- 所有响应不返回服务器绝对路径；`orphans` 只返回 `data/` 相对路径。

**测试**（`server/tests/test_quality_api.py`）：

- 无 token → 401。
- 空库 summary 全 0、`stale=true`。
- 审计后 summary 计数正确；重复审计不产生重复行。
- `issues` 分页与 `total` 一致。
- 审计进行中再启动 → 409（可用 monkeypatch 让任务停留）。

---

### B1-04 `apply_document_filters` 支持 `quality`

**文件**：

- 修改：[server/app/routers/documents/filters.py](../../server/app/routers/documents/filters.py)
- 修改：[server/app/routers/documents/crud.py](../../server/app/routers/documents/crud.py)
- 修改：[server/app/routers/documents/stats.py](../../server/app/routers/documents/stats.py)
- 修改：[server/app/routers/documents/__init__.py](../../server/app/routers/documents/__init__.py)（导出新函数）
- 新增/修改：[server/tests/test_quality.py](../../server/tests/test_quality.py)

**新增**：

```python
def apply_quality_filter(stmt, db: Session, quality: str | None): ...
```

**语义**（必须写进代码注释，因为「快照 vs 实时」容易误解）：

| `quality` | 条件 |
|---|---|
| `issues` | 实时便宜条件（空/短正文、无封面、未分类、语言变体）**OR** 快照昂贵条件（`file_exists=0`、`needs_ocr=1`、`duplicate_group_size>1`） |
| `empty_content` | 实时：`trimmed_content_length_expr = 0` |
| `short_content` | 实时：`0 < length < threshold` |
| `no_cover` | 实时：`cover_path IS NULL OR cover_path = ''` |
| `unclassified` | 实时：`collection_id IS NULL` |
| `language_variant` | 实时：`language NOT IN ('zh','en','ja','und')` |
| `needs_ocr` | 快照：`q.needs_ocr = 1` |
| `missing_file` | 快照：`q.file_exists = 0` |
| `duplicate` | 快照：`q.duplicate_group_size > 1` |
| `not_audited` | `q.document_id IS NULL` |

实现要点：

- 需要 `document_quality` 的条件统一 `outerjoin(DocumentQuality, DocumentQuality.document_id == Document.id)`；不要重复 join。
- 便宜的实时条件不依赖审计，所以从未审计的文档也能被筛出来。
- `issues` 不能用 `q.issues != '[]'` 单独判断：未审计但正文为空的书也必须出现。
- `count_documents` 必须把同一 `quality` 参数传给 `apply_document_filters`；否则「共 N 本」与列表不一致。
- 未知 `quality` 值 → 400，与 `status` 的处理一致。

**测试**：

- 列表与 count 在每种 `quality` 值下返回数量一致。
- 未审计但正文为空的书能被 `empty_content` 和 `issues` 筛出。
- `needs_ocr` 只返回有质量行且标记为 true 的书。
- `not_audited` 在有审计行后正确减少。
- 组合 `quality=empty_content&types=book,pdf&status=unread` 条件同时生效。

**验收**：真实库 `GET /api/documents?quality=empty_content&limit=1` 与 `/count` 数量一致；与基线 581 对比，差异需在报告中解释。

---

### B1-05 配置 `KB_QUALITY_MIN_CONTENT_CHARS`

**文件**：[server/app/config.py](../../server/app/config.py)

新增字段：

```python
quality_min_content_chars: int = 500
```

- 环境变量名由 `KB_` 前缀自动生成为 `KB_QUALITY_MIN_CONTENT_CHARS`。
- 校验范围 1–100000（在 `Settings` 里用 `Field` 或 `field_validator`；不要静默接受 0/负数）。
- 审计服务与筛选都从 `settings.quality_min_content_chars` 取，不在代码里写死 500。
- `GET /api/quality/summary` 增加 `min_content_chars` 字段，前端用它显示「少于 N 字」。
- 单测：默认 500；环境变量覆盖生效；非法值启动失败或回退默认（二选一，建议启动失败并给出清晰错误）。

---

### B1-06 前端 API client 与类型

**文件**：

- 修改：[src/lib/api/client.ts](../../src/lib/api/client.ts)
- 修改：[src/lib/dataSource.ts](../../src/lib/dataSource.ts)
- 新增：[src/lib/api/__tests__/quality.test.ts](../../src/lib/api/__tests__/quality.test.ts)

**client.ts 新增类型**：

```ts
export type QualityIssueCode =
  | 'missing_file' | 'orphan_cover' | 'empty_content' | 'short_content'
  | 'no_cover' | 'unclassified' | 'needs_ocr' | 'duplicate_sha' | 'language_variant';

export type QualityFilter =
  | 'issues' | 'empty_content' | 'short_content' | 'no_cover' | 'unclassified'
  | 'needs_ocr' | 'missing_file' | 'duplicate' | 'not_audited';

export interface QualitySummary { ... } // 与 api-contracts 第 1 节一致
export interface QualityIssueItem { ... }
export interface QualityAuditStatus { ... }
export interface QualityOrphans { ... }
```

**client.ts 新增函数**：

```ts
getQualitySummary(): Promise<QualitySummary>
listQualityIssues(issue: QualityIssueCode | 'all', limit = 50, offset = 0): Promise<{ total: number; items: QualityIssueItem[] }>
startQualityAudit(ids?: string[]): Promise<{ status: string }>
getQualityAuditStatus(): Promise<QualityAuditStatus>
getQualityOrphans(): Promise<QualityOrphans>
```

**dataSource.ts 修改**：

- `LibraryPageQuery` 增加 `quality?: QualityFilter`。
- `loadLibraryPage` 在 API 模式把 `quality` 写入 `params`。
- 本地模式没有质量数据：**不要静默忽略**。在 `loadLibraryPage` 开头加：
  ```ts
  if (mode === 'local' && query.quality) {
    throw new Error('质量筛选需要连接后端服务');
  }
  ```
  UI 在本地模式禁用 chips（B1-10），所以正常路径不会触发。

**测试**：mock `fetch`，断言：
- 每个函数的 URL、查询参数、`X-API-Token` 头。
- `getQualitySummary` 解析数字/布尔字段。
- 408/503 等错误走 `ApiError`。

---

### B1-07 `DataSourceStatusBar` + 全局 layout

**新增**：

- [src/components/DataSourceStatusBar.tsx](../../src/components/DataSourceStatusBar.tsx)
- [src/components/__tests__/DataSourceStatusBar.test.tsx](../../src/components/__tests__/DataSourceStatusBar.test.tsx)

**修改**：[src/app/layout.tsx](../../src/app/layout.tsx)

**行为**：

| 状态 | 展示 |
|---|---|
| `mode=api` | 不渲染任何条 |
| `local/no_token` | 蓝色/灰色信息条：「未连接后端，当前使用浏览器本地数据」+「去设置」 |
| `degraded/health_failed` | 琥珀色警告条：「后端不可达（baseUrl），已回退本地数据」+「重试」+「去设置」 |
| `local/checking` 或 `status=null` | 不渲染，避免闪烁 |
| `unconfigured` | 与 `no_token` 相同文案 |

要求：

- 使用 B0 的 `useDataSourceStatus()`。
- `role="status"`（本地/未配置）、`role="alert"`（degraded）。
- 「重试」调用 `refresh()`；重试期间按钮禁用。
- 「关闭」写 `sessionStorage.setItem('kb_status_bar_dismissed', '1')`，仅当前会话隐藏；`degraded` 也允许关闭，但设置页仍显示真实状态。
- 不显示 token；`baseUrl` 可以显示（去掉末尾 `/`）。
- 移动端不换行溢出：长 URL 用 `truncate` + `title`。
- 放在 `Providers` 内、`Nav` 之下、`ErrorBoundary` 之外。

**测试**：

- mock `useDataSourceStatus`：四种状态分别渲染/不渲染。
- 点击关闭后 sessionStorage 有值。
- degraded 重试调用 `refresh`。
- 不出现 token 文本。

---

### B1-08 首次配置引导 + 书架错误/空态

**文件**：

- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 新增：[src/components/OnboardingBanner.tsx](../../src/components/OnboardingBanner.tsx)
- 新增：[src/components/__tests__/OnboardingBanner.test.tsx](../../src/components/__tests__/OnboardingBanner.test.tsx)

**引导显示条件**（全部满足）：

1. `status.mode !== 'api'`；
2. `metaQuery.data?.total === 0`；
3. 没有本地迁移提示（`migrationHint === 0`）；
4. 用户没有点过「不再提示」（`localStorage.kb_onboarding_dismissed !== '1'`）。

**内容**（三步，不自动改配置）：

1. 启动后端：`cd server && uvicorn app.main:app --port 8000`（文案里给出，不执行）。
2. 打开设置，填入 API 地址与 token。
3. 点击「保存并测试」，状态条变为绿色/消失即成功。

按钮：`去设置`、`以后再说`。后者写 localStorage。

**书架状态重构**：

- **加载中**：网格骨架（12 个占位卡），替换 `加载中...` 纯文字。
- **错误**：
  - `metaQuery.isError`：`StatePanel variant="error"`，标题「书架元数据加载失败」，描述包含错误信息，动作「重试」调用 `metaQuery.refetch()`。
  - `booksQuery.isError`：标题「书目加载失败」，动作「重试」调用 `booksQuery.refetch()`。
  - 两种错误都不白屏，侧栏/搜索框仍可用。
- **空态**：
  - API 模式且无筛选：`StatePanel variant="empty"`，标题「书架还是空的」，描述「导入 EPUB/PDF，或到导入中心扫描目录」，动作「导入书籍」。
  - 本地模式：标题「本地书架没有书」，描述「连接后端或先导入本地文件」。
  - 有筛选/搜索：标题「没有符合条件的书」，动作「清除筛选」。
- **导入中**：保留现有文案，但禁用重复点击。
- **不改变**现有导入/编辑/删除逻辑。

**验收**（浏览器）：

- localStorage 清空 → 书架显示引导，不显示 0 本空白。
- 填入错误 token → 状态条 degraded，书架仍可用本地数据，不白屏。
- 后端停掉 → 重试后出现错误态，恢复后重试成功。
- 搜索无结果 → 空态文案是「没有符合条件的书」，不是「书架还是空的」。

---

### B1-09 `/quality` 只读质量中心页面

**新增**：

- [src/app/quality/page.tsx](../../src/app/quality/page.tsx)
- [src/components/quality/QualitySummaryCards.tsx](../../src/components/quality/QualitySummaryCards.tsx)
- [src/components/quality/QualityIssueTable.tsx](../../src/components/quality/QualityIssueTable.tsx)
- [src/components/__tests__/QualitySummaryCards.test.tsx](../../src/components/__tests__/QualitySummaryCards.test.tsx)（可选）

**页面结构**：

1. 标题「质量中心」+ 最后审计时间 + `stale` 警告。
2. 操作区：
   - 「重新审计」按钮；运行中显示进度条（每 2 秒轮询 `audit-status`）。
   - 「配置阈值：少于 N 字」说明（只读，来自 summary）。
3. 汇总卡：正文为空、正文过短、无封面、未分类、需 OCR、文件丢失、重复、语言变体。点击卡片切换下方 issue。
4. Issue 表格：
   - 列：书名、类型/格式、字数、问题标签、分类、操作。
   - 操作：「在书架中查看」→ `/library?quality=<code>`；「打开」→ `/reader/{id}`（book/pdf）。
   - 分页：上一页/下一页，显示 `total`。
5. 孤儿文件区：只读列表 + 总大小；不提供隔离按钮（B2）。
6. 本地模式：整页显示 `StatePanel`，标题「质量中心需要连接后端」，动作「去设置」；不请求质量 API。

**要求**：

- 所有数据来自 B1-06 的 client。
- 不使用 mock 数据兜底；失败显示错误态与重试。
- 不做任何写操作（审计按钮除外，它只写质量表）。
- 页面在移动端可用：汇总卡 2 列，表格横向滚动或卡片化。
- 从书架质量 chips 旁提供「质量中心」链接。

**验收**：

- 真实库审计后，各卡片数字与 SQL 只读统计一致（允许 ±0，必须在报告中列出对比）。
- 审计进行中刷新页面，进度仍能恢复（轮询 status）。
- 断网时页面显示错误态，不白屏。

---

### B1-10 书架质量 chips + 设置页健康标签

**文件**：

- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 修改：[src/app/settings/page.tsx](../../src/app/settings/page.tsx)
- 修改：[src/lib/dataSource.ts](../../src/lib/dataSource.ts)（如 B1-06 未改完）
- 新增：[src/components/__tests__/LibraryQualityChips.test.tsx](../../src/components/__tests__/LibraryQualityChips.test.tsx)（可选）

**书架 chips**：

- 位置：搜索框下方、书籍网格上方，一行可横向滚动。
- 选项：`全部`、`有质量问题`(`issues`)、`正文为空`、`无封面`、`需 OCR`、`未分类`。
- 计数来自 `getQualitySummary()`；API 模式才显示。
- 使用 B0 的 `FilterChip`。
- 点击设置 `activeQuality`，进入 React Query key；同时保留现有分类/作者/搜索/书架筛选。
- 「全部」清除 quality，但不清除其他筛选。
- 本地模式：显示一行灰色提示「质量筛选需要连接后端」+ 设置链接；不渲染 chips。
- 质量中心链接：chips 行末尾「质量中心 →」。

**设置页**：

- 数据源卡片：显示 `mode`、`reason`、`baseUrl`、`tokenConfigured`（是/否），不显示 token。
- 健康检查标签补全：`fulltext_active`、`semantic`（B5 前为「未启用」）。
- 增加「质量中心」入口卡片，显示 `empty_content/no_cover/unclassified` 三个数字；点击进入 `/quality`。
- 保存配置后按 B0-04 失效缓存；此处只做展示，不重复改缓存逻辑。

**测试/验收**：

- 点击「正文为空」后列表数量与质量中心该 issue 的 `total` 一致。
- 切换分类/搜索时 quality 保留；点击「全部」只清除 quality。
- 本地模式没有可点击的质量 chips。
- 设置页不出现 token 明文。

---

### B1-11 后端/前端测试、浏览器验证、B1 报告

**后端**：

```bash
cd server
E:/zy/python/python.exe -m pytest tests -q
```

新增测试至少覆盖：

- 迁移幂等/级联。
- 审计问题码与阈值。
- 列表/计数 quality 一致性。
- 路由鉴权、分页、409。
- 孤儿扫描不把软删除引用算孤儿。

**前端**：

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

新增测试至少覆盖：

- client URL/错误。
- 状态条四种状态。
- 引导显示条件。
- 质量 chips 切换。

**浏览器验证**（必须实际操作）：

1. 清空 localStorage → 书架显示引导。
2. 设置正确 base + token → 状态条消失，书架显示 1297 本。
3. 打开 `/quality` → 点「重新审计」→ 进度到 100%，汇总数字出现。
4. 点「正文为空」→ 回书架 → 数量与质量中心一致。
5. 停后端 → 刷新 → degraded 状态条 + 书架本地回退不白屏。
6. 移动端宽度 390px：状态条、chips、质量页无横向溢出。

**报告**：`docs/roadmap/reports/B1-2026-10-26.md`，按 [00-execution-rules.md](00-execution-rules.md) 第 6 节模板，额外包含：

- 审计前后 `documents` 计数对比（应完全一致）。
- 质量汇总数字与基线对比表。
- 迁移耗时、备份路径、`integrity_check`。
- 未做/跳过项（例如 OCR 未安装）。

更新 [progress.md](progress.md) 与 [README.md](README.md) 状态。

## 5. 验收清单

- [ ] `0007` 迁移成功，`document_quality` 表/索引存在，`integrity_check=ok`。
- [ ] 迁移前后 `documents` 行数、`updated_at` 无变化。
- [ ] 审计任务单飞、可轮询、失败有 error。
- [ ] 9 类问题码都能产生并在 API/页面展示。
- [ ] `quality` 筛选与计数一致；未审计文档的便宜问题也能筛出。
- [ ] 质量中心只读，除审计外没有任何写操作。
- [ ] 状态条四种状态正确，不泄露 token。
- [ ] 书架错误/空态/引导可用；本地模式不白屏。
- [ ] 设置页显示数据源状态与健康标签。
- [ ] 全量测试与浏览器验证通过。
- [ ] B1 报告与 progress.md 已更新。

## 6. 回滚

1. 代码：`git revert <B1 commit>`。
2. 迁移：`alembic downgrade 0006_add_sha256`（会删除 `document_quality`；先确认没有 B2 依赖）。
3. 质量数据：随表删除，无需保留。
4. 不改 `documents`，无文档数据回滚。

## 7. 交给 B2 的东西

- `quality_audit.audit_one()` 可在批量修改后按需重算单本。
- `document_quality` 的 `needs_ocr/missing_file/duplicate_group_size` 是 B2 动作的输入。
- `scan_orphans()` 的只读结果可直接变成 B2 的隔离预览。
- `/quality` 页面预留动作区，B2 只加按钮和弹窗，不重写页面。
- `GET /api/documents/ids` 尚未实现（B2-04 提供）；B2 的多选依赖它。
