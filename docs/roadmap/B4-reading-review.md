# B4 · 阅读队列 + 间隔复习

| 项目 | 值 |
|---|---|
| 批次 | B4 |
| 状态 | TODO |
| 依赖 | B3 全部 DONE（`BookCard` 已预留队列徽标插槽）；G3 迁移授权（`0010_reading_queue_reviews`） |
| 真实数据写入 | 只写新表 `reading_queue_items`、`annotation_reviews`；**不改 `documents`/`annotations` 任何字段** |
| 迁移 | `0010_reading_queue_reviews` |
| 预计改动 | 后端约 9 个文件、前端约 12 个文件 |
| 完成标志 | B4-01 ~ B4-09 全部 DONE，B4 报告已写 |

## 1. 目标

现在的产品把「读到哪里」记下来了（`read_progress`、`last_read_at`），但没回答两个问题：

1. **接下来读哪本** —— 没有队列，1297 本里靠缘分点开。
2. **划过的线还记不记得** —— `/review` 只是把最近 200 条标注轮播一遍，今天看完和三个月前看完没有区别。

本批加上：

- 阅读队列：手动加入 + 打开书自动入队、优先级/拖拽排序/暂停/完成。
- 间隔复习：SM-2 lite 调度，每条划线/批注变成到期卡片，四键评分（忘记/困难/良好/简单），到期数进侧栏与每日笔记。
- 修掉「仅今天」用 UTC 日期比较的时区 bug（本机 UTC+8，早上 8 点前会显示 0 条）。

## 2. 不做什么

- 不引入 Anki 式的图片/音频模板、不做卡组（deck）概念。
- 不做生成式复习提示、不做语义推荐（B5）。
- 不硬删除任何标注；复习行随标注删除由 FK 级联。
- 不改 `annotations` 表结构与写入语义（只在创建后**追加**一条复习行）。
- 不为本地（IndexedDB）模式实现复习引擎：本地模式明确显示「需要连接后端」。
- 不新增依赖：调度是纯函数，拖拽用原生 HTML5 DnD + 键盘上下按钮。

## 3. 前置条件

1. B3 全部 DONE，[progress.md](progress.md) 当前批次为 B4。
2. 已获得 **G3 迁移授权**。
3. 备份 + 校验：

   ```bash
   python server/scripts/incremental_backup.py --apply --keep 7
   ```

   ```bash
   E:/zy/python/python.exe - <<'PY'
   import sqlite3
   con = sqlite3.connect(r"E:\code\projects\library\server\knowledge_base.db")
   print("integrity", con.execute("pragma integrity_check").fetchone())
   print("annotations", con.execute("select count(*) from annotations").fetchone())
   print("reading", con.execute("select count(*) from documents where deleted_at is null and read_progress > 0 and read_progress < 1").fetchone())
   PY
   ```

4. 记录回填预期行数：`highlight+note` 标注数、`0 < progress < 1` 的书数（当前库为基线，写进报告）。

## 4. 推荐执行顺序

```text
B4-01 (表/迁移/回填) → B4-02 (调度纯函数) → B4-03 (队列 API) → B4-04 (复习 API)
  → B4-05 (标注创建挂钩) → B4-08 (时区工具 + 修仅今天)
  → B4-06 (队列 UI) → B4-07 (复习 UI) → B4-09 (测试/报告)
```

B4-08 排在 UI 之前：UI 里的「今天」计数要用对的时间工具，先改 UI 会写两遍。

## 5. 任务

### B4-01 队列/复习模型 + 迁移 `0010_reading_queue_reviews` + 回填

**文件**：

- 修改：[server/app/models.py](../../server/app/models.py)
- 新增：[server/alembic/versions/0010_reading_queue_reviews.py](../../server/alembic/versions/0010_reading_queue_reviews.py)
- 新增：[server/tests/test_queue_review_migration.py](../../server/tests/test_queue_review_migration.py)

**表结构以 [data-model.md](data-model.md) 第 4 节为准**：`reading_queue_items`（`document_id` UNIQUE）与 `annotation_reviews`（`annotation_id` PK）。

**回填（迁移内，幂等）**：

1. 队列：`documents` 中 `deleted_at IS NULL AND type IN ('book','pdf') AND 0 < read_progress < 1` → `priority=100, sort_order=0, status='active', note=''`。
2. 复习：`annotations` 中 `type IN ('highlight','note')` 且所属文档未软删除 → **到期时间打散**，避免几万条标注同一天涌出：

   ```python
   # 按 created_at 升序取行号 rn（从 0 起）
   due_at = now_utc + timedelta(days=1 + rn % 14)   # 未来 14 天内均匀铺开
   interval_days = 0; ease = 2.5; repetitions = 0; lapses = 0; status = 'active'
   ```

   - 用 Python 取 id 列表 + 分块 500 行 `insert().values([...])`，**不用窗口函数**（可移植，且天然满足分块要求）。
   - `WHERE NOT EXISTS` 语义由 Python 侧先查已有 `annotation_id` 集合实现，重复执行迁移不产生重复行。
3. 书签（`bookmark`）不建复习行。

**实现要求**：

- `revision = "0010_reading_queue_reviews"`，`down_revision = "0009_library_indexes"`。
- 建表/建索引前用 `sa.inspect(bind)` 判断存在，保持与 `0006`~`0009` 一致的幂等风格。
- 时间列一律 `DateTime(timezone=True)`，写入值用现有 [models.utcnow()](../../server/app/models.py#L28)（tz-aware UTC）。
  - **必须知道的事实**：SQLite 落库是 `2026-08-16 17:32:09.790231` 这种**naive UTC 文本**，读回来是 naive datetime。所有跨表比较前统一走 B4-02 的 `as_utc_naive(dt)` / `from_utc_naive(dt)`，不要在路由里裸比 tz-aware 与 naive。
- `downgrade()`：先删 `annotation_reviews` 的 2 个索引 + 表，再删 `reading_queue_items` 的 1 个索引 + 表。

**测试**：

- 临时库造数据：1 本在读、1 本未读、1 本读完、1 本软删除在读、3 条 highlight、1 条 note、1 条 bookmark、1 条属于软删除文档的 highlight。
- 断言回填：队列 1 行（只有未删除的在读本）、复习行 4 行（3+1，排除 bookmark 与软删除文档的标注）。
- 断言 `due_at` 落在 `now+1d ~ now+14d` 且互不相同。
- 迁移重复执行（同一 bind 跑两次 upgrade 分支）行数不变。
- 删标注 → 复习行级联消失；删文档 → 队列行级联消失。
- `downgrade` 后两张表不存在，`annotations`/`documents` 行数不变。

**验收**：真实库执行后 `reading_queue_items` 行数 = 预统计的「在读」数；`annotation_reviews` 行数 = 预统计的 highlight+note 数；`documents`/`annotations` 行数与 `updated_at` 分布完全不变。

---

### B4-02 `review_scheduler.py` · SM-2 lite 纯函数

**新增**：

- [server/app/services/review_scheduler.py](../../server/app/services/review_scheduler.py)
- 新增：[server/tests/test_review_scheduler.py](../../server/tests/test_review_scheduler.py)

**契约**：

```python
Rating = Literal["again", "hard", "good", "easy"]

@dataclass(frozen=True)
class ReviewState:
    interval_days: int
    ease: float
    repetitions: int
    lapses: int

def next_state(state: ReviewState, rating: Rating, *, now: datetime) -> tuple[ReviewState, datetime]:
    """返回 (新状态, 新到期时间 UTC)"""

def preview_intervals(state: ReviewState, *, now: datetime) -> dict[Rating, str]:
    """给四张按钮算出 '10 分钟' / '1 天' / '8 天' 这样的文案，卡片上先展示"""

def as_utc_naive(dt: datetime) -> datetime: ...
def from_utc_naive(dt: datetime) -> datetime: ...
```

**算法（SM-2 的精简版，参数写进模块 docstring）**：

| 评分 | `repetitions` | `interval_days` | `ease` |
|---|---|---|---|
| `again` | 归 0 | `lapses += 1`；`lapses >= 3` → 1 天，否则**同日重学**（到期 = `now + 10 分钟`） | `ease - 0.20` |
| `hard` | `+1` | `interval = max(1, round(interval * 1.2))` | `ease - 0.15` |
| `good` | `+1` | `r == 0 → 1`；`r == 1 → 6`；否则 `round(interval * ease)` | 不变 |
| `easy` | `+1` | `r == 0 → 3`；`r == 1 → 10`；否则 `round(interval * ease * 1.3)` | `ease + 0.15` |

- `ease` 恒定夹在 `[1.3, 2.8]`；`interval_days` 上限 365，超过取 365。
- 到期时间一律用 `now`（调用方传入，便于测试固定时钟），**不在函数里读系统时间**。
- `preview_intervals` 与 `next_state` 必须共用同一份计算逻辑（内部调 `next_state`），否则按钮显示的间隔和点完之后的间隔不一致。
- 首次评分（`interval_days=0, repetitions=0`）：`again → 同日重学`、`hard → 1 天`、`good → 1 天`、`easy → 3 天`。
- 纯函数，不 import SQLAlchemy、不碰 DB。

**测试**（用 `datetime(2026,10,26,12,0,tzinfo=timezone.utc)` 固定 `now`）：

- 四条完整链路：`good,good,good` → 间隔 1/6/15 天（ease 2.5）。
- `again` 后 `repetitions` 归 0、`lapses+1`，第三次 `again` 直接 1 天。
- `ease` 边界：连续 `again` 不会低于 1.3；连续 `easy` 不会高于 2.8。
- 365 天封顶生效。
- `preview_intervals('good')` 与 `next_state(good)` 的间隔一致（回归保护）。

**验收**：单测全绿；`python -c` 手工跑一遍 good 链路，与文档表格一致。

---

### B4-03 阅读队列 API

**文件**：

- 新增：[server/app/routers/reading_queue.py](../../server/app/routers/reading_queue.py)
- 修改：[server/app/schemas.py](../../server/app/schemas.py)（在文件末尾追加队列 schema，与现有 `DocumentOut` 等风格一致）
- 修改：[server/app/main.py](../../server/app/main.py)（注册路由，前缀 `/api`，走现有 `X-API-Token` 依赖）
- 新增：[server/tests/test_reading_queue_api.py](../../server/tests/test_reading_queue_api.py)

**端点**（契约见 [api-contracts.md](api-contracts.md) 第 6 节）：

| 端点 | 说明 |
|---|---|
| `GET /api/reading-queue` | `status`（默认 `active`）、`limit`（默认 50，≤200）、`offset`；按 `priority, sort_order, created_at` 升序 |
| `POST /api/reading-queue` | `{document_id, priority?, note?}`；已存在返回 `200 {created:false,item}`，新建 `201 {created:true,item}` |
| `PATCH /api/reading-queue/{item_id}` | 只允许 `priority`/`status`/`note` |
| `DELETE /api/reading-queue/{item_id}` | 只删队列项，204 |
| `POST /api/reading-queue/reorder` | `{ordered_ids: [...]}`，1–500，写 `sort_order = 0..n-1` |
| `POST /api/reading-queue/auto-add` | 阅读进度上报时的幂等自动入队（见下） |

**要求**：

- 队列项响应体带书目投影，前端不需要再查一次文档：
  ```json
  { "id": "uuid", "document_id": "uuid", "priority": 100, "sort_order": 0,
    "status": "active", "note": "", "created_at": "...",
    "document": { "title": "...", "author": "...", "format": "epub",
                  "cover_path": "...", "read_progress": 0.42, "last_read_at": "..." } }
  ```
- 校验：文档存在且未软删除 → 否则 404；`priority` 0–1000；`note` ≤500 字；`status ∈ {active,paused,done}`。
- `reorder`：所有 id 必须都存在且 `status=active`，否则 400 并回滚整批（一个事务内完成）；>500 → 400。
- `auto-add` 语义（**这是唯一会被高频调用的写接口**）：
  - 入参 `{document_id}`。
  - 文档 `read_progress >= 1` 或已软删除或类型不在 `book/pdf` → 直接 `{created:false, skipped:"not_applicable"}`，不建行。
  - 已有队列项（任意 status）→ `{created:false, skipped:"exists"}`，**不把 `paused/done` 复活成 `active`**（用户主动暂停过就得尊重）。
  - 否则建 `priority=100, sort_order=0, status='active', note=''`。
  - 并发重复调用靠 `document_id UNIQUE` 兜底：捕获 `IntegrityError` 后回查并返回 `{created:false, skipped:"exists"}`，不返回 500。
- 软删除文档不进队列列表（join `documents` 过滤 `deleted_at IS NULL`），但其队列行**保留不删**：恢复文档后队列还在。

**测试**：幂等、404、字段校验、reorder 越权 id、并发 unique、auto-add 四种分支、`done/paused` 不被复活。

**验收**：`POST /api/reading-queue/auto-add` 对同一本书连打 3 次，库里只有 1 行，后 2 次 `created=false`。

---

### B4-04 复习 API

**文件**：

- 新增：[server/app/routers/reviews.py](../../server/app/routers/reviews.py)
- 修改：[server/app/main.py](../../server/app/main.py)
- 新增：[server/tests/test_reviews_api.py](../../server/tests/test_reviews_api.py)

**端点**（契约见 [api-contracts.md](api-contracts.md) 第 7 节）：

| 端点 | 说明 |
|---|---|
| `GET /api/reviews/due` | `limit`（默认 20，≤100）、`new_limit`（默认 20，≤100）、`document_id?` |
| `POST /api/reviews/{annotation_id}/rate` | `{rating}` |
| `POST /api/reviews/{annotation_id}/snooze` | `{days}` ∈ {1,3,7} |
| `GET /api/reviews/stats` | `tz_offset_minutes`（-840..840，默认 0） |

**`due` 取卡规则**：

- 只取 `status='active'`、`due_at <= now`、标注类型 ∈ `highlight/note`、所属文档未软删除。
- 排序：`interval_days = 0`（新卡）优先按 `created_at` 升序，其余按 `due_at` 升序；两组拼接，新卡总量受 `new_limit` 限制，避免一次涌出上千条。
- 卡片带 `preview`（由 B4-02 `preview_intervals` 生成），前端按钮文案直接来自后端，避免前后端各写一份算法。

**`rate` 规则**：

- 复习行不存在时自动创建（兼容迁移后新建的标注），初始状态用默认值再走 `next_state`。
- 写入 `interval_days/ease/repetitions/lapses/due_at/last_rated_at`。
- `again` 的同日重学：`due_at = now + 10 分钟`，所以刷新页面后这张卡还会回来（这是有意的，不是 bug）。
- 返回调度后的完整状态 + 新 `preview`。

**`stats` 口径（必须与 [api-contracts.md](api-contracts.md) 第 7 节逐字一致）**：

| 字段 | 定义 |
|---|---|
| `due_now` | `due_at <= now_utc` 的 active 复习行总数（含逾期） |
| `due_today` | `now_utc < due_at <= 本地今日结束` 的总数（今天稍后才到期，不含逾期） |
| `new_today` | 今日新增的 highlight/note 标注数（`created_at` 落在本地今日） |
| `reviewed_today` | `last_rated_at` 落在本地今日的复习行数 |
| `upcoming_7d` | `now_utc < due_at <= now_utc + 7d` 的总数 |

- 本地日界用 SQL 表达式做偏移，**不要把行全读进 Python**：
  ```python
  def local_day_expr(col, offset_minutes: int):
      sign = "+" if offset_minutes >= 0 else "-"
      return func.strftime("%Y-%m-%d", col, f"{sign}{abs(offset_minutes)} minutes")
  ```
  - 单位固定是 **`minutes`**：入参就是分钟，写成 `hours` 会把 +08:00 算成 +480 小时，日界全错且测试很难发现（跨天偏移看起来仍"有数字"）。
  - SQLite 修饰符必须带显式符号，所以拼 `+N minutes` / `-N minutes`，不要输出 `+/-` 两个字符或无符号数字。
  - `offset_minutes` 必须是整数且在 -840..840，用 FastAPI `Query(ge=-840, le=840)` 校验；非数字直接被 422 挡掉，**不得把用户输入拼进 SQL 字符串**。
  - PostgreSQL 分支用 `func.date(func.col(at_time_zone(...)))`；本批只需保证 SQLite 正确、PG 分支有实现且不报语法错（测试跳过并注明）。

**测试**：

- 造 3 张逾期 + 2 张今日稍后 + 1 张 8 天后：断言 5 个统计字段各自数字。
- `tz_offset_minutes=480` 与 `0` 下 `due_today` 不同（证明偏移真的生效）。
- `rate` 四种评分改变 `interval_days/due_at`；重复 `rate` 以最后一次为准。
- `snooze` 只改 `due_at`，不改 `ease/repetitions/interval_days`。
- 标注删除后不出现在 `due`，`stats` 计数同步下降。
- 非法 `days=2` → 422/400；非法 `rating` → 422。

**验收**：真实库 `/api/reviews/stats?tz_offset_minutes=480` 的 `new_today` 与只读 SQL（按 `+480 minutes` 手算）一致；同时记录 `tz_offset_minutes=330`（印度 +05:30）也成立，防止误用小时。

---

### B4-05 标注创建挂钩

**文件**：

- 修改：[server/app/routers/annotations.py](../../server/app/routers/annotations.py)（[create_annotation:92](../../server/app/routers/annotations.py#L92)）
- 新增：[server/tests/test_annotation_review_hook.py](../../server/tests/test_annotation_review_hook.py)

**要求**：

```python
@router.post("", response_model=AnnotationOut, status_code=201)
def create_annotation(payload: AnnotationCreate, db: Session = Depends(get_db)):
    ...
    db.add(obj)
    db.commit()
    ensure_review_row(db, obj)   # 新增
    return obj
```

- `ensure_review_row(db, annotation)` 放 `reviews.py` 或 `review_scheduler.py` 旁边（服务层，不在路由里内联）：
  - `annotation.type == 'bookmark'` → 不建行，直接返回。
  - 所属文档已软删除 → 不建行。
  - 已存在行 → 不重复创建（幂等）。
  - 新行 `due_at = now_utc`（**立即到期**，新标注当天就该复习一次）。
- **失败不能影响标注本身**：挂钩包在 `try/except Exception` 里，失败只 `logger.warning`，标注照常 201。复习是派生数据，不该让划线功能因此报错。
- `PATCH /annotations/{id}` 改内容/颜色 **不重置** 调度（复习进度属于「这条记忆」，不属于「这段文字」）。
- `DELETE` 靠 FK `ON DELETE CASCADE`；[database.py](../../server/app/database.py) 已开 `foreign_keys=ON`，但测试必须显式覆盖，因为 SQLite 默认不开。

**测试**：highlight 建行、bookmark 不建、软删文档不建、重复调用幂等、挂钩抛异常时标注仍 201、删标注级联删复习行。

**验收**：在阅读器新建一条划线 → `GET /api/reviews/due` 立即可见该卡。

---

### B4-06 队列 UI

**文件**：

- 新增：[src/app/queue/page.tsx](../../src/app/queue/page.tsx)
- 新增：[src/components/queue/QueueList.tsx](../../src/components/queue/QueueList.tsx)
- 新增：[src/components/queue/QueueItem.tsx](../../src/components/queue/QueueItem.tsx)
- 新增：[src/lib/api/queue.ts](../../src/lib/api/queue.ts)（client 函数与类型，单独文件，避免把 [client.ts](../../src/lib/api/client.ts) 撑到更大）
- 修改：[src/app/review/page.tsx](../../src/app/review/page.tsx)（顶部 Tab：`复习` / `队列`）
- 修改：[src/components/Nav.tsx](../../src/components/Nav.tsx)（`队列` 入口，移动端只图标）
- 修改：[src/lib/dataSource.ts](../../src/lib/dataSource.ts)（`saveReadingProgress` 挂钩、`addDocumentToQueue`、`autoAddToQueue`）
- 修改：[src/components/BookReader.tsx](../../src/components/BookReader.tsx)（工具栏「加入阅读队列」按钮）
- 修改：[src/components/library/BookCard.tsx](../../src/components/library/BookCard.tsx)（用 B3 预留的 `queueBadge` 插槽）

**队列页**：

- 三段：`在读队列`（active）、`已暂停`（paused）、`最近完成`（done，最多 20 条）。
- 每项：封面（复用 `BookCover`）、书名（链到阅读器）、作者、进度条、队列备注、状态按钮（开始/暂停/完成/移出队列）、优先级。
- 排序：拖拽（原生 DnD）+ 每行「上移/下移」按钮（键盘可达，`aria-label="上移《书名》"`）。松手或点击后 `POST /reorder` 提交当前完整可见顺序；失败回滚到旧顺序并 toast。
- 「下一本」卡：顶部固定显示 `GET /reading-queue/next` 的结果 +「继续阅读」按钮。
- `done` 状态：同时把 `read_progress` 顶到 1？ **不做**。完成队列项只是移出队列视图，不改文档字段（本批不写 `documents`）。

**阅读器按钮**：

- API 模式：无队列项时显示「加入队列」，点击后变「已在队列」；本地模式不显示该按钮。

**自动入队**：

- 在 [saveReadingProgress](../../src/lib/dataSource.ts#L576) 的 API 成功分支后追加 fire-and-forget：
  ```ts
  void autoAddToQueue(bookId).catch(() => {});
  ```
  - 只在 `progressPercent > 0` 时调用，避免「打开就退」也入队。
  - 每本书每次会话最多尝试一次（`Set` 去重），进度上报是高频调用，不能每次都打后端。
  - 失败静默：队列是增值功能，不能因为 500 而让用户以为进度没存上（进度已成功）。

**本地模式**：队列 Tab 显示 `StatePanel variant="info"`「阅读队列需要连接后端」+「去设置」，不发请求。

**测试**：

- `queue.ts`：mock fetch 断言 URL/方法/请求头/`created` 解析。
- `QueueList`：拖拽后调用 `reorder` 的 id 顺序正确；失败回滚；上移/下移按钮边界（首/尾禁用）。
- `saveReadingProgress`：进度为 0 不触发 auto-add；同一本书不重复触发；auto-add 失败不影响进度返回。

**验收**（浏览器）：打开一本未读过的书读 5 分钟 → 队列里自动出现该书且 `priority=100`；拖到第 1 位刷新后仍是第 1 位；点「暂停」后自动读同一本不会把它拉回队列。

---

### B4-07 复习 UI

**文件**：

- 修改：[src/app/review/page.tsx](../../src/app/review/page.tsx)
- 新增：[src/components/review/ReviewCard.tsx](../../src/components/review/ReviewCard.tsx)
- 新增：[src/components/review/ReviewStatsBar.tsx](../../src/components/review/ReviewStatsBar.tsx)
- 新增：[src/lib/api/reviews.ts](../../src/lib/api/reviews.ts)
- 新增：[src/components/__tests__/ReviewCard.test.tsx](../../src/components/__tests__/ReviewCard.test.tsx)

**页面结构（Tab 内）**：

```text
[到期 42 · 今天稍后 8 · 今日已复习 7]   [刷新] [全部回顾 →]
┌──────────────────────────────┐
│ 《书名》 · 章节 3 / 第 42 页      │
│ 划线原文（大字，衬线）              │
│ 我的批注                          │
│ [忘记 1] [困难 2] [良好 3] [简单 4] │  ← 下方小字显示各自间隔
└──────────────────────────────┘
剩余 41 张 · 进度 1/42
```

**交互要求**：

- 「显示答案」两步式：先只给原文（批注隐藏），空格/`Enter`/点击显示答案后再评分——直接给四个按钮会让「记得」变成「看到答案才记得」。
- 快捷键：`1`=忘记、`2`=困难、`3`=良好、``4`=简单、`S`=推迟 1 天、`←`/`→` 翻页（沿用现有轮播键位）、`Esc` 停止评分。**输入框聚焦时不响应**（复用现有 keydown 逻辑要加 `e.target` 判断）。
- 评分后**乐观移除该卡**，失败回滚并 toast；同一张卡连续点击用 `mutation` 的 `isPending` 禁用，不靠去重。
- 卡片显示后端返回的 `preview` 文案，不自行计算间隔。
- 「跳回原文」保留现有 `jumpToSource`；「存入笔记」保留现有 `convertAnnotationToNote`（转笔记后该卡按已复习处理，评分为 `good` 并标记，避免同一句反复出现）。
- 到期为 0：显示 `StatePanel variant="empty"`「今天的复习做完了」+ 下一张到期时间（来自 `due` 的空结果 + `stats.upcoming_7d`）。
- 队列 Tab 与复习 Tab 共用 `?tab=` URL 参数，可深链；沿用 B3 的 `useLibraryFilters` 风格，不复用其代码。
- 移动端：四个按钮一行不换行；长按卡片可复制原文。

**数据获取**：

- `useQuery(['reviews','due'])` + `staleTime: 30s`；`refetchOnWindowFocus: false`（否则切窗口会打断评分）。
- `useQuery(['reviews','stats', tzOffset])`，`tzOffset` 由 `getTimezoneOffset()` 取反。

**测试**：快捷键只在答案揭示后可用；`isPending` 期间按钮禁用；失败回滚恢复卡片；`preview` 文案来自响应；本地模式不请求 reviews。

**验收**：键盘 `1/2/3/4` 全流程打完 5 张卡；刷新后到期数减少；早上 8 点前打开页面「今天新增」不为 0（配合 B4-08）。

---

### B4-08 时区工具、修「仅今天」、每日笔记接入

**文件**：

- 新增：[src/lib/datetime.ts](../../src/lib/datetime.ts)
- 修改：[src/app/review/page.tsx](../../src/app/review/page.tsx)（[todayIso:45](../../src/app/review/page.tsx#L45)、[todayCount:81](../../src/app/review/page.tsx#L81)、[visible/onlyToday:91](../../src/app/review/page.tsx#L91)）
- 修改：[src/lib/dailyNote.ts](../../src/lib/dailyNote.ts)
- 修改：[src/app/notes/page.tsx](../../src/app/notes/page.tsx)（[handleDailyNote:222](../../src/app/notes/page.tsx#L222)）
- 新增：[src/lib/__tests__/datetime.test.ts](../../src/lib/__tests__/datetime.test.ts)

**先确认 bug**：`todayIso()` 用 `new Date().toISOString().slice(0,10)`（UTC 日期），而 `created_at` 是后端返回的 naive UTC 串，于是 UTC+8 用户在**本地 00:00–08:00** 之间看到的「今天新增」恒为 0，「仅今天」也筛不出刚划的线。这是真实存在的显示错误，不是偏好问题。

**`datetime.ts`**：

```ts
export function localTodayKey(now = new Date()): string;      // 'YYYY-MM-DD'，本地
export function utcKeyFromIso(iso: string): string;           // 取 naive UTC 串的日期部分
export function localDayKeyFromUtcIso(iso: string, offsetMinutes = -new Date().getTimezoneOffset()): string;
export function startOfLocalDayIso(now = new Date()): string; // 供 since 参数（UTC ISO）
export function tzOffsetMinutes(): number;                    // -new Date().getTimezoneOffset()
```

- `localDayKeyFromUtcIso` 的实现：把 naive UTC 串当 UTC 时间，加 offset 后再取本地日期；**不要**用 `new Date('2026-10-26 01:00:00')`（空格分隔在 Safari/部分环境不可靠），要显式补 `Z`。
- 「仅今天」改为**服务端过滤**：`reviewAnnotations({ since: startOfLocalDayIso(), limit })`，而不是取回 200 条再在前端按日期切片。前端切片受 `limit=200` 截断，今天标注多时本身就会漏。
- `todayCount` 改为「服务端返回的今日条数」，不再二次遍历。

**每日笔记接入**：

- `buildDailyNoteContent(hits, dateLabel, extra?)` 增加**可选第三参**（保持向后兼容，现有测试不改也应通过）：
  ```ts
  extra?: { dueCount: number; reviewedToday: number; queueNextTitle?: string | null }
  ```
  生成的骨架在「今日划线」之前插入：
  ```markdown
  ## 今日复习

  - 待复习 N 条 · 今日已完成 M 条
  - 下一本：《XXX》
  ```
  `extra` 缺省或全 0 时**不加这一段**（不要写「待复习 0 条」的噪音）。
- [notes/page.tsx handleDailyNote](../../src/app/notes/page.tsx#L222) 在生成内容前并发取 `getReviewStats(tzOffsetMinutes())` 与 `getQueueNext()`，任一失败就用 `undefined`，不阻塞笔记创建。
- 笔记标题继续用本地日期（现状正确），但把散落的 `getFullYear/padStart` 手工拼接换成 `localTodayKey()`。

**测试**：

- `localDayKeyFromUtcIso('2026-10-25 18:00:00', 480) === '2026-10-26'`（UTC+8 跨日）。
- `localDayKeyFromUtcIso('2026-10-26 01:00:00', -300) === '2026-10-25'`（UTC-5 跨日）。
- `startOfLocalDayIso()` 用 `vi.setSystemTime` 固定后等于本地零点转 UTC。
- `buildDailyNoteContent` 传 `extra` 与不传的差异；`extra` 全 0 时无该段。
- 「仅今天」走 `since` 参数（mock fetch 断言查询串包含 `since=`）。

**验收**：把系统时区改成 `UTC-5` 与 `UTC+8` 各跑一次（或 mock），「今天新增」与「仅今天」结果一致且符合本地日；生成每日笔记含复习小节。

---

### B4-09 测试、验证与 B4 报告

**后端**：

```bash
cd server
E:/zy/python/python.exe -m pytest tests -q
E:/zy/python/python.exe -m pytest tests/test_queue_review_migration.py tests/test_review_scheduler.py tests/test_reading_queue_api.py tests/test_reviews_api.py tests/test_annotation_review_hook.py -q
```

**前端**：

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

**e2e**（`e2e/queue-review.spec.ts`，临时库）：

1. 上传一本 EPUB → 阅读器划线 → 复习 `due` 出现该卡 → 评 `good` → 剩余数 -1。
2. 阅读 1 分钟后队列自动出现 → 拖拽重排 → 刷新顺序保持 → 暂停后不再自动复活。
3. 本地模式（不带 token）打开 `/review?tab=queue` 显示「需要连接后端」，不报错。

**真实库只读检查**（不写数据，允许）：

```bash
E:/zy/python/python.exe - <<'PY'
import sqlite3
con = sqlite3.connect(r"E:\code\projects\library\server\knowledge_base.db")
for sql in [
    "select count(*) from reading_queue_items",
    "select count(*), min(due_at), max(due_at) from annotation_reviews",
    "select count(*) from documents where deleted_at is null and read_progress>0 and read_progress<1",
    "select count(*) from annotations a join documents d on d.id=a.document_id where a.type in ('highlight','note') and d.deleted_at is null",
]:
    print(sql, con.execute(sql).fetchone())
PY
```

**报告**：`docs/roadmap/reports/B4-2026-10-26.md`，按 [00-execution-rules.md](00-execution-rules.md) 第 6 节模板，额外包含：

- 回填行数 vs 预统计（逐项对齐表）。
- 迁移耗时、`integrity_check`、备份路径。
- `documents`/`annotations` 行数与 `updated_at` 在迁移前后完全一致的证明（SQL 对比）。
- 调度链路样例（同一张卡 good→good→good 的实际间隔）。
- 时区 bug 修复前后对照（同一时刻的「今天新增」数字）。
- 未做项：本地模式无复习、`done` 不改 `read_progress`、PG 的 `stats` 分支未在真实 PG 上验证。

更新 [progress.md](progress.md) 与 [README.md](README.md)。

## 6. 验收清单

- [ ] `0010` 迁移幂等、可降级；回填行数与预统计一致；`documents`/`annotations` 未被改写。
- [ ] 复习到期时间在本地时区下正确，跨日样本验证通过。
- [ ] SM-2 lite 四个评分的间隔与 `ease` 边界符合文档表格，且有单测。
- [ ] 队列 CRUD + reorder + auto-add 幂等；`paused/done` 不被自动复活。
- [ ] 新建划线/批注立即产生复习卡；书签不产生；挂钩失败不影响标注。
- [ ] 删除标注级联删除复习行（FK 生效已测试）。
- [ ] `/queue` 与 `/review?tab=` 可用，拖拽与键盘重排均可达。
- [ ] 复习两步式（先回忆后评分）+ 快捷键 + 乐观更新 + 失败回滚。
- [ ] 「仅今天」改走服务端 `since`，UTC+8/UTC-5 结果正确。
- [ ] 每日笔记嵌入复习小节，取数失败不阻塞建笔记。
- [ ] 本地模式对队列/复习给出明确提示，不发请求、不白屏。
- [ ] 全量测试 + e2e + build 通过；B4 报告与 progress.md 已更新。

## 7. 回滚

1. 代码：`git revert <B4 commit>`。
2. 迁移：`alembic downgrade 0009_library_indexes`（删两张新表，回填数据一并消失；不触碰 `documents`/`annotations`）。
3. 回滚前确认：没有比 `0010` 更新的迁移依赖这两张表（B5 的 `0011` 不依赖）。
4. 用户已产生的复习进度会丢失——**这是唯一不可逆的部分**，回滚前必须在报告里写明「N 条复习记录、M 条已评分」，并取得确认。
5. 前端 localStorage 无队列/复习偏好，无需清理。

## 8. 边界与已知限制

- 复习只在 API 模式可用；本地模式（IndexedDB）没有调度数据。
- 「完成」不改 `read_progress`：进度 100% 才是读完，两个概念分开，避免用户点「完成」却其实没读完。
- SM-2 lite 不是真 Anki：没有独立学习步骤（learning steps）队列、没有疲劳度、不做卡片间间隔优化；`again` 的 10 分钟重学在同一次会话里也会算「已复习」。
- 到期数以 `limit=100` 为单页上限，长队列靠分页打完，不做「今日上限自动截断」。
- 时区偏移由浏览器上报，服务端不做 DST 计算；跨夏令时的用户在偏移变化当天可能有一天的统计偏差。

## 9. 交给 B5 的东西

- `annotation_reviews` 与 `reading_queue_items` 与语义索引无关，B5 不需要动这两张表。
- 复习卡的「语义相似标注推荐」（B5 可选增强）直接复用 `GET /api/reviews/due` 的卡片投影结构，不再新开端点。
- B5 的向量索引覆盖 `documents.content`；`note` 类型文档（标注转笔记产生的）也在覆盖范围内，不要排除。
