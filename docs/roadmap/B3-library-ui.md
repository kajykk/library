# B3 · 书架信息架构与性能

| 项目 | 值 |
|---|---|
| 批次 | B3 |
| 状态 | TODO |
| 依赖 | B1、B2 全部 DONE；G3 迁移授权（`0009_library_indexes`） |
| 真实数据写入 | 不写 `documents` 业务字段；只建索引、改查询与前端 |
| 迁移 | `0009_library_indexes` |
| 预计改动 | 后端约 7 个文件、前端约 16 个文件 |
| 完成标志 | B3-01 ~ B3-12 全部 DONE，B3 报告已写 |

## 1. 目标

当前书架页是一个 898 行的单文件组件，筛选用 `useState`、封面用一个全局 `coverUrls` map、排序写死 `created_at desc`、加载完 1297 本后全部塞进 DOM。本批把它改成**可分享 URL 的筛选架构 + 按需渲染的列表**，同时保留 B1/B2 已交付的质量 chips、状态条、多选与批量动作。

具体结果：

1. 后端支持排序、格式/语言筛选、分面计数，并为高频查询建索引。
2. 筛选状态进 URL：`/library?q=&shelf=&category=&author=&quality=&format=&language=&tag=&sort=&view=`，刷新/后退/收藏都保持一致。
3. 侧栏可搜索、可折叠、可隐藏 0 本分类、可置顶常用分类、可重命名分类。
4. 书目卡片组件化，封面按卡片生命周期加载并做 LRU 缓存，不再在页面里维护全局 map。
5. 大结果集只渲染可视区行 + 预取下一页，DOM 节点数与结果集规模解耦。
6. 移动端、暗色模式、键盘与读屏可用。

## 2. 不做什么

- 不改数据：不改标题/分类/正文，不做批量写（那是 B2）。
- 不新增 npm/Python 依赖：虚拟化用已装的 `@tanstack/react-virtual`。
- 不引入状态管理库；筛选状态用 hook + URL。
- 不做语义检索（B5）、不做阅读队列（B4）。
- 不改 `resolveMode()` 与 `extract_text_from_file()` 的签名。
- 不删 B1/B2 的 UI：质量 chips、`DataSourceStatusBar`、多选工具条、导入入口必须继续工作。
- 不把真实库当 e2e 数据源：Playwright 仍用临时库。

## 3. 前置条件

1. B1、B2 全部 DONE，[progress.md](progress.md) 当前批次为 B3。
2. 已获得 **G3 迁移授权**（`0009_library_indexes`）。
3. 已备份并校验：

   ```bash
   python server/scripts/incremental_backup.py --apply --keep 7
   ```

   ```bash
   E:/zy/python/python.exe - <<'PY'
   import sqlite3
   con = sqlite3.connect(r"E:\code\projects\library\server\knowledge_base.db")
   print(con.execute("pragma integrity_check").fetchone())
   print(con.execute("select count(*) from documents where deleted_at is null").fetchone())
   PY
   ```

4. 记录执行前查询耗时基线（默认列表、按分类、按作者、质量筛选各 3 次取中位数）。
5. 执行 `alembic current`，确认当前 head 是 `0008_operation_logs`。

## 4. 推荐执行顺序

```text
B3-01 (sort) → B3-02 (facets + format/language) → B3-04 (索引)
  → B3-05 (useLibraryFilters) → B3-08 (BookCard/BookCover)
  → B3-06 (侧栏) → B3-07 (工具栏/chips) → B3-03 (选择态迁移)
  → B3-09 (无限滚动+虚拟化) → B3-10 (状态) → B3-11 (响应式/暗色/无障碍)
  → B3-12 (测试/性能/报告)
```

B3-04 放在前端改造前：索引一旦就位，后续所有 UI 验证测到的耗时才是目标耗时。

## 5. 任务

### B3-01 `GET /documents` 增加 `sort`

**文件**：

- 修改：[server/app/routers/documents/filters.py](../../server/app/routers/documents/filters.py)
- 修改：[server/app/routers/documents/crud.py](../../server/app/routers/documents/crud.py)
- 新增：[server/tests/test_documents_sort.py](../../server/tests/test_documents_sort.py)

**新增**（放在 `filters.py`，列表与未来其他查询共用）：

```python
SORT_OPTIONS: dict[str, tuple] = {...}

def apply_document_sort(stmt, sort: str | None):
    """未传 sort 时保持既有行为：created_at desc, id desc"""
```

**排序白名单**（键名即 URL 与前端使用的值，不得改名）：

| `sort` | SQL 排序 |
|---|---|
| `created_desc`（默认） | `created_at DESC, id DESC` |
| `created_asc` | `created_at ASC, id ASC` |
| `title_asc` | `title ASC, id ASC` |
| `author_asc` | `coalesce(author,'') ASC, title ASC, id ASC` |
| `last_read_desc` | `last_read_at IS NULL, last_read_at DESC, id DESC` |
| `progress_desc` | `coalesce(read_progress,0) DESC, id DESC` |
| `file_size_desc` | `file_size DESC, id DESC` |

**实现要求**：

- 现 [crud.py:71](../../server/app/routers/documents/crud.py#L71) 的 `stmt.order_by(Document.created_at.desc())` 改为 `apply_document_sort(stmt, sort)`。
- 每个排序**必须带 `Document.id` 作为末位 tiebreaker**：否则同值行在 offset 分页里会重复/漏项（当前 `created_at` 相同已经能复现）。
- 未知 `sort` → `HTTPException(400, "不支持的 sort: ...")`，与 `status`/`quality` 的处理方式一致；不要用默认值静默兜底。
- `count`、`facets`、`ids` 不接受 `sort`（无意义）；如果传了，忽略并在文档里写明，不报错。
- `sort` 与 `status=recent` 的关系：`recent` 仍是"最近添加"视图，后端不特殊处理，由前端固定传 `sort=created_desc` 且只取一页。

**测试**：

- 默认不传 `sort` 的结果与迁移前一致（`created_at desc`）。
- 7 种 `sort` 各自顺序正确；构造 2 条 `created_at` 完全相同的文档，翻页不重复不丢失。
- `last_read_desc` 把 `last_read_at IS NULL` 排在最后。
- 非法 `sort` → 400。
- `sort` + `q` + `collection_id` + `quality` 组合仍与 `count` 一致。

**验收**：真实库 `GET /api/documents?sort=title_asc&limit=5` 返回按标题排序的前 5 本，`id` 单调；`/api/documents/count` 数字不受 sort 影响。

---

### B3-02 `format` / `language` 筛选与 `GET /api/documents/facets`

**文件**：

- 修改：[server/app/routers/documents/filters.py](../../server/app/routers/documents/filters.py)
- 修改：[server/app/routers/documents/crud.py](../../server/app/routers/documents/crud.py)
- 修改：[server/app/routers/documents/stats.py](../../server/app/routers/documents/stats.py)
- 新增：[server/app/routers/documents/facets.py](../../server/app/routers/documents/facets.py)
- 修改：[server/app/routers/documents/__init__.py](../../server/app/routers/documents/__init__.py)（导入顺序：`facets` 必须在 `crud` 的 `/{doc_id}` 之前，否则 `facets` 被当成 doc id）
- 修改：[src/lib/api/client.ts](../../src/lib/api/client.ts)
- 修改：[src/lib/dataSource.ts](../../src/lib/dataSource.ts)
- 新增：[server/tests/test_document_facets.py](../../server/tests/test_document_facets.py)

**筛选参数新增**：

| 参数 | 语义 |
|---|---|
| `format` | `Document.format == value`（逗号分隔多值取 OR）；未知格式不报错，返回空集 |
| `language` | `Document.language` 归一化后比较：`lower(...)` 后取 `-` 前段，值集 `zh/en/ja/und`；直接字符串等值会漏掉 `en-US`、`ENG` |

> `type`/`types` 是**文档类型作用域**（book/pdf/note/article/webclip），`format` 是**文件格式**（epub/pdf/txt/md…）。两者不能混用，书架始终传 `types=book,pdf`。

**`GET /api/documents/facets`**（契约见 [api-contracts.md](api-contracts.md) 第 2 节）：

```json
{
  "total": 1297,
  "formats": [{"value": "pdf", "count": 364}],
  "languages": [{"value": "zh", "count": 1087}],
  "tags": [{"value": "历史", "count": 42}],
  "authors": [{"value": "张三", "count": 5}],
  "categories": [{"collection_id": "uuid", "name": "历史", "count": 30}],
  "uncategorized": 32,
  "truncated": {"tags": false, "authors": false}
}
```

**语义（分面计数的自排除规则，必须照此实现并写注释）**：

- 每个维度用「除自身以外」的全部当前筛选条件统计，否则选中 `format=pdf` 后其他格式计数归零，用户无法切换。
- `types`（作用域）与 `q`、`status`、`quality` **永远参与**所有维度的统计；只有 `format`/`language`/`tag`/`author`/`collection_id` 在统计自己那一维时排除。
- 每个维度 `ORDER BY count DESC, value ASC`，`LIMIT 100`；`tags`/`authors` 命中超过 100 时 `truncated=true`。
- `authors` 过滤空字符串与仅空白；`languages` 用归一化后的值分组。
- `categories` 需要 `LEFT JOIN collections`，未分类不进列表，只进 `uncategorized`。
- `tags` 统计用 `COUNT(DISTINCT document_id)`，避免多标签 join 放大。
- 全部走 SQL 聚合，**不得把文档读进 Python 再 `Counter`**。

**客户端**：

```ts
export type LibrarySort =
  | 'created_desc' | 'created_asc' | 'title_asc' | 'author_asc'
  | 'last_read_desc' | 'progress_desc' | 'file_size_desc';

export interface FacetItem { value: string; count: number }
export interface CategoryFacet { collection_id: string; name: string; count: number }
export interface ApiDocumentFacets { total: number; formats: FacetItem[]; languages: FacetItem[]; tags: FacetItem[]; authors: FacetItem[]; categories: CategoryFacet[]; uncategorized: number; truncated: { tags: boolean; authors: boolean } }

export function getDocumentFacets(params?: Record<string, string>): Promise<ApiDocumentFacets>;
```

`LibraryPageQuery` 增加 `sort?: LibrarySort`、`format?: string | null`、`language?: string | null`、`tag?: string | null`；`loadLibraryPage` 与本地过滤同步支持：

- API 模式把这些都写进 `params`（list 与 count 共用同一份 params，沿用现有做法）。
- 本地模式在 [matchesLocalFilters](../../src/lib/dataSource.ts#L174) 增加 `format`（精确匹配 `book.format`）、`language`（同归一化规则，前端也要实现一份，抽到 `src/lib/language.ts` 供测试）、`tag`（`book.tags.includes`）；排序抽成 `sortBooksLocal(books, sort)`。
- **顺带修一个既有 bug**：本地 `shelf=recent` 现在是「先 `slice(0, limit)` 再过滤」（[dataSource.ts:254](../../src/lib/dataSource.ts#L254)），加了筛选条件后「最近添加」会莫名少于 20 本。改为「过滤后再截断到 `RECENT_SHELF_SIZE`」。API 模式不受影响。

**测试**：

- facets 与列表/count 在单维度筛选下一致；选中 `format=pdf` 时 `formats` 仍列出其他格式的真实计数。
- `language=en` 能命中 `en-US`、`ENG`、`EN-US`。
- `tags` 多标签文档不被重复计数。
- `uncategorized` 与 `documents.stats` 的 `uncategorized` 一致。
- 前端：mock `fetch` 断言 facets URL 参数与解析；本地归一化函数用例（`en-US→en`、`zh-TW→zh`、`''→und`、`fr→fr`）。

**验收**：真实库 facets 的 `total` 与书架「共 N 本」一致；`formats` 中 `pdf` 计数 = 364（基线值，若有变化在报告解释）。

---

### B3-03 书架选择态迁移（复用 B2-04 的 `/documents/ids`）

> 接口本身已由 [B2-04](B2-import-batch.md) 交付（`GET /api/documents/ids`，上限 5000）。本任务**只做前端**：把 B2-12 写在页面里的选择逻辑抽成 hook，并接到新的 URL 筛选架构上。

**文件**：

- 新增：[src/lib/useLibrarySelection.ts](../../src/lib/useLibrarySelection.ts)
- 修改：[src/lib/api/client.ts](../../src/lib/api/client.ts)（若 B2-04 未加 `getDocumentIds`，此处补 `getDocumentIds(params): Promise<string[]>`）
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 新增：[src/lib/__tests__/useLibrarySelection.test.ts](../../src/lib/__tests__/useLibrarySelection.test.ts)

**契约**：

```ts
export interface LibrarySelection {
  mode: 'none' | 'selecting';
  ids: Set<string>;
  count: number;
  enter(): void;
  exit(): void;
  toggle(id: string): void;
  selectAllFiltered(): Promise<{ selected: number; truncated: boolean }>;
  selectPage(): void;
  clear(): void;
}
export function useLibrarySelection(
  filteredQuery: Record<string, string>,
  loadedIds: string[],
): LibrarySelection;
```

**要求**：

- `selectAllFiltered()` 用**当前 URL 筛选条件**（含 quality/format/language/tag/author/category/q）请求 `/documents/ids`，不是「当前已加载的页」。
- 返回 5000 条即视为 `truncated=true`，UI 提示「超出单次批量上限 500，请缩小筛选范围」，并把已选截断到 500，禁止直接发起 >500 的批量请求（后端会 413/400）。
- 选择态不进 URL（避免 URL 爆炸），切换筛选时**自动清空**已选，防止「选了 A 类结果却对 B 类执行删除」。
- 键盘：`Esc` 退出选择模式；焦点回到触发按钮。
- 本地模式：不请求 `/documents/ids`，用当前已加载 ID 全选，并在提示里写明「本地模式只能选中已加载的书目」。

**测试**：`selectAllFiltered` 传参与筛选一致；截断行为；换筛选清空选择；`Esc` 退出。

**验收**：在真实库「有质量问题 + 分类=历史」下全选，选中的 ID 数与该筛选下 `count` 一致（≤500 时）。

---

### B3-04 书架索引迁移 `0009_library_indexes`

**文件**：

- 新增：[server/alembic/versions/0009_library_indexes.py](../../server/alembic/versions/0009_library_indexes.py)
- 新增：[server/tests/test_library_indexes_migration.py](../../server/tests/test_library_indexes_migration.py)

**索引定义以 [data-model.md](data-model.md) 第 3 节为准**，共 6 个，全部为 `WHERE deleted_at IS NULL` 的部分索引：

```text
ix_documents_alive_type_created      (type, created_at DESC)
ix_documents_alive_type_collection   (type, collection_id, created_at DESC)
ix_documents_alive_type_author       (type, author, created_at DESC)
ix_documents_alive_type_last_read    (type, last_read_at DESC)
ix_documents_alive_type_progress     (type, read_progress)
ix_documents_alive_updated           (updated_at)
```

**实现要求**：

- `revision = "0009_library_indexes"`，`down_revision = "0008_operation_logs"`。
- 用 `sa.inspect(bind)` 读取现有索引名，已存在则跳过（与 [0006_add_sha256.py](../../server/alembic/versions/0006_add_sha256.py) 的幂等风格一致）。
- SQLite 部分索引用 `op.create_index(name, "documents", [...], sqlite_where=sa.text("deleted_at IS NULL"))`；同时给 `postgresql_where` 传同样条件，便于将来换库。
- 建索引前先 `pragma user_version`？不需要。但脚本要记录耗时；765MB 库上建索引是分钟级操作，**必须在报告里写实测耗时**。
- `downgrade()` 逆序删除 6 个索引，逐个判断存在。
- 迁移**不改任何数据**，不加列。

**测试**：

- 临时库执行 `upgrade head` 后 `PRAGMA index_list('documents')` 含 6 个索引。
- 重复执行不报错（幂等分支）。
- `EXPLAIN QUERY PLAN` 对默认列表查询使用了 `ix_documents_alive_type_created`（SQLite 计划文本可能选 `sqlite_autoindex`，若优化器认为全表更快，断言放宽为「不报错且计划里没有 `SCAN documents` 之外的异常」，并在报告注明）。
- `downgrade 0008_operation_logs` 后 6 个索引消失，数据行数不变。

**验收**：真实库执行前后 `documents` 行数、`sum(length(coalesce(content,'')))` 抽样一致；`integrity_check=ok`；`analyze` 后默认列表查询耗时对比写入报告（若变慢，必须回滚并写「发现」）。

---

### B3-05 `useLibraryFilters`：筛选状态 URL 化

**文件**：

- 新增：[src/lib/libraryFilters.ts](../../src/lib/libraryFilters.ts)（纯函数：解析/序列化）
- 新增：[src/lib/useLibraryFilters.ts](../../src/lib/useLibraryFilters.ts)（React hook）
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 新增：[src/lib/__tests__/libraryFilters.test.ts](../../src/lib/__tests__/libraryFilters.test.ts)

**筛选模型**：

```ts
export interface LibraryFilters {
  q: string;
  shelf: ShelfFilter;          // all|reading|unread|done|recent
  category: string | null;     // 分类名
  author: string | null;
  quality: QualityFilter | null;
  format: string | null;
  language: string | null;     // 归一化后的 zh/en/ja/und 或原始值
  tag: string | null;
  sort: LibrarySort;
  view: 'grid' | 'list';
}

export const DEFAULT_FILTERS: LibraryFilters; // 全默认：q='' shelf='all' sort='created_desc' view='grid'

export function parseLibraryFilters(sp: URLSearchParams): LibraryFilters;
export function serializeLibraryFilters(f: LibraryFilters): URLSearchParams;
export function filtersEqual(a: LibraryFilters, b: LibraryFilters): boolean;
export function buildApiParams(f: LibraryFilters): Record<string, string>;
```

**规则**：

- 默认值**不写进 URL**（`/library` 保持干净）；只有非默认值才出现。
- 未知值（`shelf=xxx`、`sort=yyy`、`quality=zzz`）解析时回退默认，**不抛错**：URL 是用户可编辑输入。
- `serializeLibraryFilters` 输出顺序固定（`q,shelf,category,author,quality,format,language,tag,sort,view`），便于测试断言。
- `view` 允许在 URL，便于分享；同时首次进入时用 `localStorage.kb_library_view` 作为初值（URL 优先）。
- `shelf=recent` 与 `sort` 冲突时以 `recent` 为准：解析后 `sort` 强制 `created_desc`，序列化时不写 `sort`。
- `quality` 在本地模式无效：hook 接收 `mode`，本地模式解析时丢弃 `quality` 并在返回值里给 `droppedFilters: ['quality']` 供 UI 提示。

**Hook**：

```ts
export function useLibraryFilters(): {
  filters: LibraryFilters;
  searchInput: string;
  setSearchInput: (v: string) => void;
  setFilter: <K extends keyof LibraryFilters>(key: K, value: LibraryFilters[K]) => void;
  patchFilters: (next: Partial<LibraryFilters>) => void;
  clearFilters: () => void;   // 保留 sort/view
  isFiltered: boolean;
  activeCount: number;
  droppedFilters: string[];
};
```

**实现要求**：

- 搜索输入框保持**即时**本地 state，300ms 防抖后才写 `q` 到 URL（沿用现有防抖时长）。
- 导航一律 `router.replace(url, { scroll: false })`，不用 `push`：避免每次点筛选都往历史栈塞一条；后退仍由浏览器历史负责（因为 replace 不新增历史）。
- `clearFilters()` 清 `q/shelf/category/author/quality/format/language/tag`，保留 `sort/view`。
- 从 `/quality?quality=empty_content`、`/graph`、`/library?author=x` 跳入时能正确解析——即现有 `author` 查询参数必须继续工作（回归点）。
- React Query key 改为 `[...LIBRARY_QUERY_KEY, 'page', JSON.stringify(buildApiParams(filters))]`（[page.tsx:117](../../src/app/library/page.tsx#L117) 目前是手写 5 个变量，加字段就漏），并保证 key 与请求参数**同源**，不会再出现「切了筛选但缓存复用」。
- 筛选变化后 `window.scrollTo({ top: 0 })`，但 `view` 与 `sort` 变化不滚顶（用户通常想知道同一位置）。

**测试**（纯函数为主，不依赖 DOM）：

- 默认解析、全量解析、未知值回退、序列化顺序、round-trip 幂等。
- `recent` 强制 `created_desc`。
- 本地模式丢弃 `quality`。
- `clearFilters` 保留 `sort/view`。
- `buildApiParams` 输出与 B3-01/02 的后端参数名逐一对齐（回归保护）。

**验收**：手动设置 `?shelf=reading&format=pdf&sort=title_asc` 刷新后状态保持；浏览器后退回到上一个筛选；`/library?author=张三` 行为与改造前一致。

---

### B3-06 分类侧栏重构

**文件**：

- 新增：[src/components/library/CategorySidebar.tsx](../../src/components/library/CategorySidebar.tsx)
- 新增：[src/components/library/ShelfSidebar.tsx](../../src/components/library/ShelfSidebar.tsx)
- 新增：[src/lib/librarySidebarPrefs.ts](../../src/lib/librarySidebarPrefs.ts)（置顶/折叠/隐藏 0 本，纯函数 + localStorage）
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)（替换 [572–691 行](../../src/app/library/page.tsx#L572)的侧栏 JSX）
- 修改：[src/lib/api/client.ts](../../src/lib/api/client.ts)（新增 `updateCollection(id, payload)`）
- 新增：[src/components/__tests__/CategorySidebar.test.tsx](../../src/components/__tests__/CategorySidebar.test.tsx)

**功能**：

1. **书架区**（从页面搬出）：`全部/在读/未读/读完/最近添加` + 计数，点击写 `shelf`。
2. **分类区**：
   - 顶部搜索框，按分类名**子串**过滤本地列表（不发请求）。
   - 「隐藏 0 本分类」开关（默认开），计数为 0 的分类不显示，但当前选中的分类始终显示。
   - 分类行悬停/聚焦出现：置顶（图钉）、重命名、删除。
   - 置顶存 `localStorage.kb_library_pinned_categories`（分类**名**数组，因为页面按名选分类）；置顶区排在普通区之前。
   - 重命名：弹 `Modal`，调用 `PATCH /api/collections/{id}`（后端已实现，见 [collections.py:47](../../server/app/routers/collections.py#L47)）；成功后 `invalidateQueries(['documents'])` + 使 collection 名称缓存失效。
   - 折叠：整个侧栏卡片可折叠，状态存 `localStorage.kb_library_sidebar_collapsed`；折叠后只显示图标条。
3. **数据管理区**：保留现有备份/恢复入口，不重写逻辑。
4. 分类计数来源仍是 `loadLibraryMeta()`（[dataSource.ts:191](../../src/lib/dataSource.ts#L191)）；本批不把它换成 facets，避免两套来源。

**要求**：

- 重命名与删除是**写操作**，但不写 `documents`；仍要：确认弹窗 + 失败 toast + 不做乐观更新。
- 本地模式：重命名走 IndexedDB `updateCategory`（若不存在则新增该函数，保持与 `deleteCategoryAndReset` 同级）；不静默吞掉。
- 全部按钮要有 `aria-label`；图钉状态用 `aria-pressed`。
- 空分类列表：显示「还没有分类」+「新建分类」按钮，保留现有 `addCategoryByName`。

**测试**：搜索过滤、隐藏 0 本、置顶排序、折叠恢复、重命名调用正确端点、本地模式分支。

**验收**：真实库把「历史」置顶并隐藏 0 本分类后刷新，状态保持；重命名一个分类，书架标题与侧栏同步更新，书籍不会丢失筛选。

---

### B3-07 工具栏：排序、活跃筛选 chips、清除全部

**文件**：

- 新增：[src/components/library/LibraryToolbar.tsx](../../src/components/library/LibraryToolbar.tsx)
- 新增：[src/components/library/ActiveFilterChips.tsx](../../src/components/library/ActiveFilterChips.tsx)
- 新增：[src/lib/libraryFilterChips.ts](../../src/lib/libraryFilterChips.ts)（`filters → chip 描述` 纯函数）
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 新增：[src/lib/__tests__/libraryFilterChips.test.ts](../../src/lib/__tests__/libraryFilterChips.test.ts)

**工具栏布局**（一行，移动端两行）：

```text
[结果计数 / 已加载 N]   [排序下拉]   [视图 网格|列表]   [筛选抽屉(移动端)]   [选择模式]
```

- 排序下拉：`最近更新 / 最早添加 / 书名 A→Z / 作者 A→Z / 上次阅读 / 阅读进度 / 文件大小`，值即 `sort`；用原生 `<select>`（可访问性优先，不做自绘下拉）。
- 视图切换沿用现有 `Grid3X3/List` 图标，补 `aria-pressed`。
- 「结果计数」用 `aria-live="polite"`，筛选变化时读屏播报「共 364 本，已加载 60 本」。

**活跃 chips**：

- 由 `buildActiveChips(filters)` 生成，字段包括 `label`、`onRemove`（写回 hook）。
- 覆盖 `q / shelf / category / author / quality / format / language / tag`；每个 chip 右上角 `×`，键盘可聚焦、Enter/Space 移除。
- 「清除全部」按钮只在 `isFiltered` 时出现，调 `clearFilters()`。
- chips 复用 B0 的 `FilterChip`，不新造样式。

**要求**：

- 计数与「已加载」来自现有 `totalBooks`/`books.length`，不新发请求。
- 排序/视图变化不清空已选（B3-03 只在筛选变化时清空）。
- chips 行在移动端横向滚动且不裁切（现有 chips 样式已处理，保持一致）。

**测试**：每个非默认字段恰好生成一个 chip；移除后 URL 对应参数消失；`isFiltered=false` 时不出现「清除全部」；`shelf=recent` 时不显示 sort。

**验收**：设置 4 个筛选条件，chips 显示 4 个；逐个点掉后回到全库；移动端 390px 无横向溢出。

---

### B3-08 `BookCard` + `BookCover`（封面按卡加载）

**文件**：

- 新增：[src/components/library/BookCard.tsx](../../src/components/library/BookCard.tsx)
- 新增：[src/components/library/BookCover.tsx](../../src/components/library/BookCover.tsx)
- 新增：[src/lib/coverCache.ts](../../src/lib/coverCache.ts)
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)（删除 `coverUrls` state 与 [385–416 行](../../src/app/library/page.tsx#L385) 的全局封面 effect）
- 新增：[src/lib/__tests__/coverCache.test.ts](../../src/lib/__tests__/coverCache.test.ts)
- 新增：[src/components/__tests__/BookCard.test.tsx](../../src/components/__tests__/BookCard.test.tsx)

**`coverCache.ts`**：

```ts
export interface CoverLease { url: string | null; release(): void }
export function acquireCoverLease(bookId: string): Promise<CoverLease>;
export function clearCoverCache(): void;          // 测试/登出用
export function coverCacheSize(): number;         // 测试用
```

- 内部 `Map<string, { url: string | null; refs: number }>`，`Map` 插入序即 LRU 序。
- `MAX_CACHED = 200`；超出时从最旧开始淘汰，**只有 `refs === 0` 才 `URL.revokeObjectURL`**（虚拟化下同一张封面可能同时被两个渲染位引用）。
- 同一 `bookId` 并发 acquire 只发一次请求（inflight 复用）。
- 请求失败/无封面 → 缓存 `null`，60 秒负缓存，避免每个卡片挂载都打一次 404。
- `release()` 幂等；`acquire` 的 Promise 尚未 resolve 时组件卸载，用 `cancelled` 标志立即 release，不产生引用泄漏。
- `beforeunload` 不做批量 revoke（LRU 淘汰已覆盖），但 `clearCoverCache()` 会 revoke 全部 `refs===0` 的条目。

**`BookCover` props**：

```ts
{ book: Pick<Book, 'id' | 'title' | 'coverPath'>; variant: 'grid' | 'list'; sizes?: string }
```

- `coverPath` 为空**直接**渲染占位（`BookOpen` 图标 + 书名首字），不发请求（现有页面就是这么做的，别退化）。
- 加载中：与占位同尺寸的 `bg-gray-200` 脉冲块，避免布局跳动。
- 用 `next/image` + `unoptimized`（blob URL，现状保持），`fill`（grid）/ 固定尺寸（list）。
- 错误（lease.url === null）回退占位，不显示破图。

**`BookCard` props**：

```ts
{
  book: Book;
  variant: 'grid' | 'list';
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
  onOpen?: (book: Book) => void;
  onEdit?: (book: Book) => void;
  onDelete?: (book: Book) => void;
  qualityIssues?: string[];
}
```

- 结构：`<article>`；封面区与标题都能进阅读器（`Link` 到 `/reader/{id}`）；编辑/删除/选择按钮**放在 `Link` 外面**，避免嵌套可聚焦元素。
- 网格视图保留：进度条（`已读 N%`）、格式徽标、hover 遮罩「开始阅读」。
- 列表视图保留：出版社、文件大小、上次阅读时间。
- 选择态：左上角 checkbox（`role="checkbox"` + `aria-checked`），选中时卡片描 `ring-2 ring-primary-500`。
- `qualityIssues` 非空时在卡角显示小红点 + `title` 说明（B1 的可视化，不重复造）。
- 删除 `deleteBookCompletely` 等逻辑仍在页面层，卡片只回调。

**测试**：

- `coverCache`：并发 acquire 只请求一次；refs 归零后才淘汰 revoke；LRU 超 200 淘汰最旧；负缓存 60s（用 `vi.useFakeTimers`）。
- `BookCard`：无 `coverPath` 不请求；选中/取消回调；键盘 Enter 打开阅读器链接可达；编辑/删除按钮点击不会触发打开。

**验收**：真实库书架滚动时，Network 面板中封面请求数 ≤ 可视卡片数；浏览器内存里 `blob:` URL 数量不超过 220。

---

### B3-09 无限滚动 + 虚拟化 + 预取下一页

**文件**：

- 新增：[src/components/library/BookGrid.tsx](../../src/components/library/BookGrid.tsx)
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 修改：[src/lib/dataSource.ts](../../src/lib/dataSource.ts)（如需要，暴露 `hasMore`/`total` 语义）

**虚拟化策略**：

- 只在 `books.length >= 200` 时启用；小结果集走普通渲染，避免为 20 本书引入绝对定位。
- **按「行」虚拟化**，不是按卡片：网格一行 N 列，行高一致，实现简单且不会错位。
- 用 `useWindowVirtualizer({ count: rowCount, estimateSize: () => rowHeight, overscan: 4, scrollMargin })`，`scrollMargin` 取列表容器 `offsetTop`。
- 列数由容器宽度算（与 Tailwind 断点对齐）：

  ```text
  >=1280 → 5 列    >=1024 → 4 列    >=768 → 3 列    否则 → 2 列
  ```

  用 `ResizeObserver` 监听容器（jsdom 里 mock 或跳过），移动端列表视图固定 1 列。
- 行高 = `列宽 * 4 / 3`（封面 3:4）+ 96（标题/作者/元信息/进度），`list` 视图固定 112。
- 每行渲染 `<div style={{ position:'absolute', top: row.start - scrollMargin }}>`，内部 `grid-template-columns: repeat(n, minmax(0,1fr))`。
- **必须保留可访问性**：绝对定位不改变 DOM 顺序，Tab 顺序仍等于书目顺序；给容器加 `role="list"`，每行 `role="listitem"`（网格用 `role="grid"` 反而语义错，别用）。

**无限滚动**：

- 底部哨兵 `IntersectionObserver`，`rootMargin: '600px 0px'` 触发 `fetchNextPage()`，实现「提前预取下一页」。
- 哨兵只在 `hasNextPage && !isFetchingNextPage && !fetchNextPage.isError` 时工作；出错后**停止自动重试**，改显示「加载下一页失败 · 重试」按钮（避免 500 错误时 observer 疯狂重试）。
- 保留手动「加载更多」按钮在底部（现状），键盘用户不依赖 observer。
- `PAGE_STEP` 维持 60，不改动（与后端 `LIST_DEFAULT_LIMIT=200`/`LIST_HARD_LIMIT=1000` 都兼容）。
- `getNextPageParam` 继续用 `allPages.length * pageSize`，但因为 B3-01 加了 `id` tiebreaker，跨页重复问题才算真解决；页面里已有的按 id 去重逻辑**保留**作为兜底。

**测试**：

- 行分组纯函数（`chunkRows(books, columns)`）：尾行不满、空数组、columns=1。
- 哨兵触发一次 `fetchNextPage`；出错后不再自动触发（用 `IntersectionObserver` mock）。
- 虚拟开启阈值：199 本走普通渲染、200 本走虚拟化。

**验收**（真实库，手动）：全库 1297 本加载到第 4 页时，`document.querySelectorAll('article[data-book-id]').length` 应 < 40；滚动到底能连续加载到最后一页；`prefers-reduced-motion: reduce` 下滚动不闪烁。

---

### B3-10 状态层：骨架、空态、错误、加载下一页失败

**文件**：

- 新增：[src/components/library/LibrarySkeleton.tsx](../../src/components/library/LibrarySkeleton.tsx)
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 修改：[src/components/ui/index.ts](../../src/components/ui/index.ts)（如需补导出）

**要求**：

- **首屏骨架**：按 `view` 渲染 12 个卡片骨架（grid）或 8 行骨架（list），尺寸与真实卡片一致，避免 CLS。B1-08 已提出骨架，这里在组件化后落到 `BookGrid` 层，注意不要重复两套。
- **元数据错误**（侧栏计数取不到）：侧栏显示 `StatePanel variant="error"` + 重试，主列表仍可用。
- **列表错误**（第一页就失败）：`StatePanel variant="error"`，标题「书目加载失败」，描述带错误信息，动作「重试」。
- **翻页错误**（已有数据）：不清空已加载书目，底部显示琥珀色内联条「第 N 页加载失败」+「重试」。
- **空态**（区分三种）：
  - 无任何书 → 「书架还是空的」+ 导入按钮 + 「打开导入中心」；
  - 有筛选 → 「没有符合条件的书」+「清除筛选」+ 提示「可能都藏在质量问题筛选里」；
  - 本地模式空 → 增加「连接后端」引导。
- **陈旧数据提示**：`booksQuery.isFetching && booksQuery.data` 时，工具栏右侧显示细灰色「刷新中…」，不做遮罩（避免用户正在选择时页面闪断）。
- 所有错误文案中文；错误信息只取 `Error.message` 前 200 字符，避免把整段 HTML 错误页塞进 UI。

**测试**：mock React Query 状态组合（首屏/翻页/空/筛选空），断言文案与按钮回调。

**验收**：断掉后端 → 错误态 + 重试；恢复后点重试 → 列表回来且不白屏。

---

### B3-11 响应式、导航、暗色模式、无障碍

**文件**：

- 修改：[src/components/Nav.tsx](../../src/components/Nav.tsx)
- 修改：[src/app/library/page.tsx](../../src/app/library/page.tsx)
- 新增：[src/components/library/LibrarySidebarDrawer.tsx](../../src/components/library/LibrarySidebarDrawer.tsx)（薄封装，复用 `Drawer`）
- 修改：[src/app/globals.css](../../src/app/globals.css)

**导航**：

- `LINKS`（[Nav.tsx:9](../../src/components/Nav.tsx#L9)）补 `/import`（导入中心，B2-10）与 `/quality`（质量中心，B1-09），顺序：`书架 / 导入 / 质量 / 回顾 / 笔记 / 图谱 / 检索 / 设置`。
  - 若 B1/B2 已加，本任务只做「去重 + 移动端适配」，不要出现两个同名链接。
- `<sm`：只显示图标 + `aria-label`，整行 `overflow-x-auto` 允许横向滚动；`md+` 图标 + 文字。
- 当前路由高亮逻辑保持（`pathname === href || pathname.startsWith(href + '/')`）。

**书架响应式**：

- `<lg`：侧栏收进「筛选」按钮 + `Drawer`（`Drawer` 是右侧抽屉，够用），抽屉内包含书架区 + 分类区；抽屉打开时背景滚动锁定（`useOverlayA11y` 已处理）。
- 工具栏在移动端 sticky 在内容区顶部，z-index 低于抽屉遮罩（抽屉是 `z-[90]`，工具栏最多 `z-20`）。
- B2-12 的多选工具条在移动端固定底部，且不得与「加载更多」按钮重叠（底部留 `pb-20`）。
- 网格移动端 2 列、字号不缩到 12px 以下。

**暗色模式**：

- 新组件只用现有已被 `.dark` 覆盖映射的类：`bg-white / bg-gray-50 / bg-gray-100 / bg-gray-200 / text-gray-* / border-gray-*`。
- 在 [globals.css](../../src/app/globals.css) 的暗色覆盖区补：`.dark .bg-gray-200 { background-color: #33415c; }`（骨架与占位在暗色下必须可见），以及 `.dark .ring-primary-500 { --tw-ring-color: rgba(56,189,248,.55); }`。
- **禁止**为暗色模式引入新硬编码色值或 `dark:` 前缀混用（现状是集中覆盖层，混用会让下次改主题难度翻倍）。

**无障碍**：

- 卡片：`<article aria-labelledby>`；标题是 `Link`；按钮都有 `aria-label`（图标按钮不得只有 `title`）。
- chips：每个移除按钮 `aria-label="移除筛选：<label>"`。
- 选择模式：容器 `role="toolbar" aria-label="批量操作"`，选中数播报 `aria-live="polite"`。
- 排序 `<select>` 有可见 `<label className="sr-only">排序方式</label>`。
- 骨架：`aria-hidden="true"`。
- 键盘可达顺序：筛选 → 排序 → 视图 → 卡片 → 加载更多；`Esc` 关抽屉/退出选择。
- 焦点可见：`focus-visible:ring-2`，暗色下对比度足够（用 primary-400 以上亮度）。

**测试**：Nav 链接数与去重；工具栏 z-index 不与抽屉冲突（jsdom 断言 class 不含 `z-[90]`）；`BookCard` 的 `aria-labelledby` 指向标题元素 id。

**验收**（390×844，暗色 + 键盘全流程）：打开抽屉筛选 → 关闭 → 卡片键盘进阅读器 → 返回保留筛选 → 退出选择模式焦点回到工具栏。

---

### B3-12 测试、性能记录、B3 报告

**后端**：

```bash
cd server
E:/zy/python/python.exe -m pytest tests -q
E:/zy/python/python.exe -m pytest tests/test_document_facets.py tests/test_documents_sort.py tests/test_library_indexes_migration.py -q
```

覆盖：7 种排序、非法 sort 400、`format`/`language` 筛选、facets 自排除、索引幂等与降级、分页无重复/漏项。

**前端**：

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

覆盖：`libraryFilters` 纯函数、chips、`coverCache`、`BookCard`、`useLibrarySelection`、`BookGrid` 行分组、骨架/错误态、Nav 链接。

**e2e**：

```bash
npm run test:e2e
```

新增/修改 spec（`e2e/library-filters.spec.ts`）：

1. 深链 `?quality=...` 与 `?format=pdf&sort=title_asc` 渲染正确（临时库先塞 ≥60 本，制造翻页）。
2. 侧栏搜索 + 置顶 + 重命名（重命名后计数仍在）。
3. 选择模式 → 全选筛选结果 → 批量软删除 → 撤销。
4. 移动端视口：筛选抽屉开合、无横向溢出。
5. 暗色模式截图不留白。

> e2e 用临时库，**不要**把真实库计数写进断言。真实库数字只在报告里手测。

**性能记录**（真实库，只读，手动执行）：

| 指标 | 采集方式 | 目标 |
|---|---|---|
| 默认列表 P50 耗时 | 浏览器 Network 面板 3 次取中位 | 不高于 B3 前基线 |
| 索引后 SQL 耗时 | `python -c` 走 API 或 `sqlite3` `EXPLAIN QUERY PLAN` | 记录实际值 |
| DOM 节点数（滚到第 4 页） | `document.querySelectorAll('*').length` | 比 B3 前下降 ≥50% |
| 首屏到可见卡片 | `performance.getEntriesByType('paint')` + 手写时间戳 | 不劣化 |
| 内存 blob 数 | `performance.memory.usedJSHeapSize` + `blob:` 计数 | `blob:` ≤ 220 |

未达标**不得硬凑**：在报告里写清实测数字、原因（例如 SQLite 优化器没用索引）和后续建议，并把这些写进 [progress.md](progress.md) 的「发现」区。

**报告**：`docs/roadmap/reports/B3-2026-10-26.md`，按 [00-execution-rules.md](00-execution-rules.md) 第 6 节模板，额外包含：

- 迁移 `0009` 执行耗时与前后查询耗时对比表。
- 分页正确性验证：`created_at` 相同样本下逐页取 ID，断言无重复/无遗漏（给出脚本输出）。
- facets 计数与 SQL 手算对比表（`formats`/`languages`/`uncategorized` 三行）。
- 虚拟化前后的 DOM 节点数、封面请求数。
- 暗色/移动端截图路径。
- 已知未解决项（例如 `title_asc` 对中文按二进制排序，非拼音序）。

更新 [progress.md](progress.md) 与 [README.md](README.md) 状态。

**验收**：全量测试 + e2e + build 全绿；性能表有实测数字；报告已写。

## 6. 验收清单

- [ ] `sort` 7 个值生效、非法值 400、分页无重复漏项（含同 `created_at` 样本）。
- [ ] `format`/`language` 筛选在列表与 count 一致；`language=en` 命中 `en-US/ENG`。
- [ ] facets 每维自排除；`total` 与书架计数一致；全部 SQL 聚合。
- [ ] `0009` 迁移幂等、可降级，真实库耗时与效果有实测记录。
- [ ] 筛选状态完整 URL 化，刷新/分享/后退一致；未知值回退默认不报错。
- [ ] 侧栏搜索/折叠/隐藏 0 本/置顶/重命名可用，偏好持久化。
- [ ] 工具栏排序 + 活跃 chips + 清除全部可用，计数 `aria-live`。
- [ ] `BookCard`/`BookCover` 组件化，页面不再有全局 `coverUrls` map；blob URL 有上限且被回收。
- [ ] ≥200 本时虚拟化生效，DOM 卡片数与结果集解耦；无限滚动 + 预取 + 手动兜底并存。
- [ ] 首屏/元数据/列表/翻页四类错误态与三种空态可区分、可重试。
- [ ] 移动端抽屉筛选、暗色模式、键盘与读屏全流程通过。
- [ ] Nav 含 `/import`、`/quality` 且无重复，移动端不溢出。
- [ ] B1/B2 的质量 chips、状态条、多选与批量动作在重构后仍可用（回归确认）。
- [ ] 全量测试 + e2e + build 通过；性能实测有数字；B3 报告与 progress.md 已更新。

## 7. 回滚

1. 代码：`git revert <B3 commit>`。
2. 迁移：`alembic downgrade 0008_operation_logs`（只删索引，不动数据；回滚前确认真实库没有依赖这些索引的其他迁移）。
3. 数据：本批不写业务数据，无需数据回滚。
4. 前端偏好（置顶/折叠/视图）留在 localStorage，回滚后自动失效但不会报错；如需彻底清理，`localStorage.removeItem('kb_library_pinned_categories' | 'kb_library_sidebar_collapsed' | 'kb_library_view')`。
5. URL 兼容：回滚后旧的 `?format=&sort=` 参数会被忽略（不报错），无需清理。

## 8. 边界与已知限制

- 中文标题 `title_asc` 按 Unicode 码位排序，不是拼音序；本批不引入排序规则（无依赖），在报告标注。
- facets 每维上限 100，超出只影响长尾标签/作者的候选显示，不影响筛选正确性。
- 虚拟化按固定行高估算，超长书名换行到 3 行时可能出现 ≤12px 的间距偏差；`measureElement` 可校正但会让绝对定位复杂化，本批不做，写进「发现」。
- `loadLibraryMeta()` 仍是一次性全量聚合；若 B2 之后库继续增长到万级，需要把它换成分页 facets，本批不做。

## 9. 交给 B4 的东西

- `useLibraryFilters` 是后续「队列/复习」入口的筛选基座，B4 不再碰书架 URL。
- `BookCard` 预留 `queueBadge?: ReactNode` 插槽，B4-06 用它显示「在读队列第 N 位」。
- `sort=last_read_desc`、`shelf=reading` 与 B4 的阅读队列共享同一「在读」语义（`0 < progress < 1`），不要另立定义。
- facets 的 `authors`/`tags` 输出可直接被 B4 的复习筛选复用。
