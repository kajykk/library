# 全量代码审查报告

- **审查日期**：2026-10-04
- **范围**：`src/`（Next.js 前端 71 文件）、`server/app/`（FastAPI 后端 32 文件）、`server/alembic/`、`server/tests/`、`e2e/`、`Dockerfile`、`docker-compose.yml`、`.github/workflows/ci.yml`
- **未纳入**：`scripts/`（47 个一次性爬虫/采购脚本，与本项目无关）、`server/*.py` 根目录下的 20+ 个一次性运维脚本、`src/lib/parsers/foliate-js/`（vendored 第三方）
- **审查方式**：逐文件静态阅读 + 交叉引用验证（不接受"看起来像"的推断，每条结论附 `文件:行号`）

---

## 修复进度（第一轮，同日执行）

第一批 6 项已全部落地，后端 67 用例 / 前端 55 用例 / `tsc --noEmit` 全绿（lint 无新增 warning）。

| 编号 | 级别 | 状态 | 落地方式 |
| --- | --- | --- | --- |
| S1 | P0 | ✅ 已修复 | `Dockerfile`、`docker-compose.yml` 移除 token 构建参数；`client.ts` 不再读 `NEXT_PUBLIC_API_TOKEN` |
| S2 | P1 | ✅ 已修复 | `main.py` 启动时无条件 `ensure_fts_index(reindex=False)`，落后量转后台线程；新增 `fulltext_active` 健康检查 |
| C1 | P1 | ✅ 已修复 | `stats._to_utc()`：naive 按 UTC 标注、aware 才换算 |
| C2 | P1 | ✅ 已修复 | 默认 `limit=200` / 硬上限 1000；轻量模式 `defer(content)` + 前缀批量查询；前端全量场景显式传上限 |
| S5 | P1 | ✅ 已修复 | 下载头文件名白名单化；`DocumentCreate/Update.title` 拒绝控制字符 |
| A2 | P1 | 🟡 部分 | 孤儿 wal/shm（5 组）已删；`.gitignore` 补 `page_probe.html`。**报告原文有误，见下方更正** |

**更正（我自己上一轮写错了）**：A2 称 `.gitignore` 未覆盖 `coverage/`、`playwright-report/`、`test-results/` —— 实际三者**已在 .gitignore 第 58-60 行**，且 `git ls-files` 确认未被跟踪。我上一轮只核对了 A2 的文字描述，没有回读 .gitignore，属于该核未核。真正漏的只有 `page_probe.html` 一项（已补）。

另外两处不在原清单但顺手修正：
- `.gitignore` 的 `docs/` 改为 `docs/*` + `!docs/*.md`（写 `docs/` 会让 `!docs/*.md` 失效，审查报告自身无法入库）。
- README 的 4 处文档漂移已对齐代码（token 默认值、上传上限、auto_create_tables 默认值、`KB_EXTRACT_TIMEOUT_S` 标注为未实现），限流描述改为"上传/剪藏端点限流"。

新增回归测试：`server/tests/test_review_fixes.py`（11 条，与机器时区无关），覆盖：
FTS 与 `auto_create_tables` 解耦（含"调用不得缩进在 if 分支内"的 AST 断言）、reindex=False 不静默回填、
naive datetime 的 UTC 语义、下载头 CRLF 收敛、标题控制字符 422、limit 硬上限、
**轻量列表的摘要不得被 autoflush 回写覆盖正文**（本轮改动里最危险的一点，必须钉死）。

真实库只读探测（佐证 S2）：`documents=1197 / documents_fts=1197 / 落后 0`，三个触发器齐全，FTS5 可用——
即索引一直都在且数据是完整的，此前纯粹是"应用不去用它"。

严重程度定义：

| 级别 | 含义 |
| --- | --- |
| **P0** | 可被直接利用导致权限失守，或默认配置下即产生安全事故 |
| **P1** | 明确的正确性问题 / 生产环境必然触发的性能或功能失效 |
| **P2** | 边界情况、健壮性缺口、可维护性债，在特定输入或规模下暴露 |
| **P3** | 风格一致性、文档漂移、防御纵深 |

---

## 一、总体评价

架构分层是清楚的：前端 App Router + React Query + 统一数据源（API 优先 / IndexedDB 兜底），后端 FastAPI + SQLAlchemy 2 + Alembic + 按领域拆分的 routers/services。代码里有大量**针对具体踩过的坑写的注释**（如 Range suffix 语义、y-websocket 拼 URL、日志里的 `%` 字符、FTS trigram 短词回退），说明这个项目是被真实使用打磨过的，不是一次性产物。

主要问题集中在三处：

1. **认证模型的落地方式把密钥写进了公开产物**（P0）。
2. **"生产模式"下两条关键能力静默失效**：全文索引回退全表扫描、限流几乎不生效（P1）。
3. **内存/查询规模没有上限**：列表、Range、统计、重索引、备份/导出全都是"全量载入再处理"，在现有 571MB 库上已经是问题（P1/P2）。

---

## 二、安全问题

### S1 [P0] ✅ 已修复 · API Token 被编译进前端静态产物，任何人可从浏览器拿到全站写权限

```dockerfile
# Dockerfile:9-10
ARG NEXT_PUBLIC_API_TOKEN=change-me
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_API_TOKEN=$NEXT_PUBLIC_API_TOKEN \
```

```yaml
# docker-compose.yml（web 服务 build.args）
NEXT_PUBLIC_API_TOKEN: ${KB_API_TOKEN:-change-me}
```

```ts
// src/lib/api/client.ts:14-19
export function getApiToken(): string {
  if (typeof window !== 'undefined') {
    return window.localStorage.getItem('kb_api_token') || process.env.NEXT_PUBLIC_API_TOKEN || '';
  }
  return process.env.NEXT_PUBLIC_API_TOKEN || '';
}
```

`NEXT_PUBLIC_*` 会在 `next build` 时**内联进客户端 JS bundle**。用 compose 默认配置构建出的镜像里，真实 token 就是一段可被 `view-source` 搜到的明文字符串。拿到它等于拿到 `POST /api/backup/restore`（覆盖数据库）、`DELETE /api/documents/{id}`、剪藏（服务端出网）等全部写权限。

后端自身对 token 的处理是规范的（constant-time 比较、弱 token 启动告警），问题只出在**分发方式**。

**建议**：
- 删除 `NEXT_PUBLIC_API_TOKEN` 的构建期注入，只保留设置页写 localStorage 这一条路径（`saveApiConfig` 已存在）。
- `NEXT_PUBLIC_API_URL` 可以保留（非机密），但 token 不行。
- 短期缓解：至少在 Dockerfile 里把默认值从 `change-me` 改成空串，并在 compose 注释里写明"不要把真实 token 传进 web 构建参数"。

---

### S2 [P1] ✅ 已修复 · SQLite + `KB_AUTO_CREATE_TABLES=0` 部署下，FTS5 全文索引永久失效且无告警

```python
# server/app/main.py:38-40
if settings.auto_create_tables:
    Base.metadata.create_all(bind=engine)
    ensure_fts_index(engine)
```

```python
# server/app/services/search_index.py:67, 102-103
_available: bool | None = None
def fts_available() -> bool:
    return _available is True
```

`ensure_fts_index` 只在 `auto_create_tables=True` 时被调用。README 与 docker-compose 都要求生产环境走 Alembic（`KB_AUTO_CREATE_TABLES: "0"`），此时 `_available` 永远是 `None`，`fts_available()` 恒为 `False`，于是 `search.py:193` 与 `search.py:209` 的分支**永远走 ILIKE 全表扫描**——而 FTS 表和触发器其实是由迁移 0004 建好的，索引在那儿但没人用。

**影响范围要说准**：docker-compose 用的是 PostgreSQL，走 `search_vector` 分支，**不受影响**；受影响的是 README 推荐的「SQLite + Alembic」自托管路径，也就是 NAS 单用户部署。`/api/health` 里 `fulltext_index` 检查的是表能否查询（会显示 ok），不会暴露"应用没在用 FTS"，所以失效是静默的。

**建议**：
- `lifespan` 里无条件调用 `ensure_fts_index`（它已幂等且带 `IF NOT EXISTS`），或者在启动时探测 FTS 表与触发器是否存在来设置 `_available`。
- `_available` 的三态（未探测 / 可用 / 不可用）应显式区分，`None` 与 `False` 不能被同一个函数压成 `False`。
- 加一条断言测试：`auto_create_tables=False` + SQLite 时，`fts_available()` 返回 `True`。

---

### S3 [P1] 限流覆盖率接近于零，README 声明与实际不符

```python
# 全仓搜索 enforce_rate_limit 的调用点：
server/app/routers/documents.py:437   # 仅 upload（50/60s）
server/app/routers/clip.py:95         # 仅 clip（10/60s）
```

`server/app/middleware/rate_limit.py` 名为 middleware，但 `main.py` 从未 `add_middleware` 注册它（只注册了 CORS），它是一个需要手工调用的函数。README 把「限流」列为功能之一。

未受保护的高成本/高风险端点：`/api/search`、`/api/search/summary`、`/api/stats`、`/api/stats/health`、`/api/backup`（生成全库 zip）、`/api/backup/restore`、`/api/export/*`、`/api/migrate/*`、`/api/documents/metadata/search`（每次两个外部 HTTP）。此外 `verify_token`（auth.py:11）没有失败计数，单 token 可被暴力枚举。

**建议**：以中间件形式全局注册，按路由分级（读 300/min、写 60/min、重操作 5/min）；给 `verify_token` 加失败锁定或指数退避；顺手把 `middleware/rate_limit.py` 改名为 `rate_limit.py`（它不是中间件），或在其中导出真正的 `RateLimitMiddleware`。

---

### S4 [P1] SSRF：封面下载跟随重定向未逐跳校验，且存在 DNS rebinding 窗口

```python
# server/app/routers/documents.py:793-795
url_safety.validate_url(payload.cover_url)
data, ext = metadata_fetch.download_cover(payload.cover_url)
```

```python
# server/app/services/metadata_fetch.py:106
with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
```

`validate_url` 只校验用户提交的初始 URL，`download_cover` 却允许跟随任意跳数的重定向。攻击者控制一个公网域名返回 `302 → http://169.254.169.254/latest/meta-data` 即可绕过；`/api/documents/metadata/search` 的 `cover_url` 来自 Google Books / Open Library 的响应，同样会被 PATCH 端点直接消费。

对比之下 `clip.py:39-90` 已经实现了正确的做法（手动逐跳 + 每跳 `validate_url` + 跳数上限）——**同一份防护在两个入口有两套实现，一套做对了一套没做**。

另外 `url_safety._resolve_all_ips` 先解析校验，随后 httpx 再次解析并发起连接，中间存在 TOCTOU / DNS rebinding 窗口。`clip.py` 的逐跳校验缩小了窗口但没有消除它。

**建议**：
- 封面下载复用 `clip` 的逐跳校验逻辑，抽成 `url_safety.fetch_with_guard(url, ...)` 供两处共用。
- 根治方案是解析后把 IP 固定下来（httpx 的 `transport` 自定义 resolver / `limits` + 自定义 `connect`），让校验与连接用同一个 IP。
- 顺带把 `url_safety` 的端口白名单补上（当前只限 scheme 与 IP 段，不限端口）。

---

### S5 [P1] ✅ 已修复 · Content-Disposition 响应头注入（CRLF）

```python
# server/app/routers/documents.py:70-85
raw_name = f"{doc.title or 'document'}{'.' + ext if ext else ''}"
ascii_fallback = raw_name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "document"
headers = {
    "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(raw_name)}",
    ...
}
```

`ascii_fallback` 只剔除了双引号，**没有处理 `\r` 与 `\n`**。而 `DocumentCreate.title` / `DocumentUpdate.title`（schemas.py:47、58）除了 `min_length=1` 之外没有任何字符约束，`MigrateBook.title` 同样自由。构造 `title = 'x\r\nX-Injected: 1'` 即可尝试注入响应头。

实际后果取决于底层 h11/uvicorn 是否拒绝含控制字符的 header value——拒绝则变 500，不拒绝则是 header 注入。两种都不是可接受行为。

**建议**：`ascii_fallback` 用白名单过滤（只保留 `[A-Za-z0-9._ -]`，其余替换为 `_`），或干脆只发 `filename*=UTF-8''...`（`quote()` 已把 CRLF 编码成 `%0D%0A`，那一段是安全的）。同时给 `DocumentUpdate.title` 补上控制字符校验。

---

### S6 [P2] 协作 Token 走 query string；房间无授权校验

```python
# server/app/collab.py:105-106
query = parse_qs(scope.get("query_string", b"").decode())
token = (query.get("token") or [None])[0]
```

```ts
// src/components/notes/useYjsCollab.ts:43-46
const wsProvider = new WebsocketProvider(getCollabWsUrl(docId), `doc-${docId}`, ydoc, {
  connect: true,
  params: { token: getApiToken() },
});
```

Token 出现在 WebSocket URL 里，会被反向代理日志、浏览器历史、Referer 链路记录。`ROOM_PATTERN`（collab.py:26）只校验房间名格式，不校验"该连接是否有权访问该文档"——单用户模型下可接受，但应在文档里写明这是一个已知取舍。

另有：`_reject_unauthorized` 里 `path` 校验用的是 `scope["path"]`，而 `ROOM_PATTERN` 的可选前缀 `/ws/collab` 与挂载点一致，逻辑成立；但 `CollabStore.__init__` 先设 `self.db_path` 再调 `super().__init__()`，依赖 pycrdt 内部也使用同名属性，属于对第三方私有实现的隐式耦合，升级 pycrdt 时易碎。

**建议**：改走 `Sec-WebSocket-Protocol` 子协议或连接后第一条消息携带 token；至少在 `_reject_unauthorized` 里优先读 header（当前已支持 header，只是前端没用）。

---

### S7 [P2] 备份恢复缺少服务端侧的规模校验与解压炸弹防护

`server/app/routers/backup.py:105-174`：

- 上传体积有上限（`upload_max_size_mb`）✅
- 目录逃逸有防护（`write_relative` → `abs_path` 校验 `is_relative_to`）✅
- **但 `zf.read(entry)` 无解压后大小限制**，且 `json.loads(zf.read(META_NAME))` 的 meta.json 大小只被整体 zip 体积间接约束
- `db.merge(obj)` 会用备份里的任意 id 覆盖现有行——这是设计（upsert 合并），但服务端没有"此操作具破坏性"的确认机制，只有前端 `dialog.confirm`（settings/page.tsx:151）

**建议**：遍历 `zf.infolist()` 累加 `file_size`，超限即拒；恢复接口要求一个显式确认头或二次调用。

---

### S8 [P2] 上传文件的解析链缺少解压炸弹与整体超时兜底

- `content_extract._extract_epub`（content_extract.py:167）与 `metadata_extract._extract_epub_from_zip`（metadata_extract.py:167）都直接 `zf.read(...)`，无条目大小上限
- `content_extract._read_text`（:61）一次性 `path.read_bytes()`，500MB 的 txt 会整份进内存
- EPUB 是 zip，1MB 的压缩炸弹可展开成数 GB

`_bounded` 的 5 秒超时能防止请求挂死，但**超时不取消任务线程**（代码注释已承认），恶意文件仍会持续占用 CPU/内存直到自然结束。

**建议**：读 zip 条目前检查 `ZipInfo.file_size` 并累计总量上限；`_read_text` 改分块解码；给 `_EXTRACT_POOL` 加整体并发与队列上限。

---

### S9 [P3] DocumentOut 暴露服务端文件路径

`schemas.py:83-84` 的 `file_path` / `cover_path` 会随每个文档返回（`files/xxx.pdf`、`covers/xxx.jpg`）。属于低价值信息泄漏，但没必要暴露。建议从 `DocumentOut` 移除，前端 `apiDocToBook` 里 `filePath` 字段实际也没被使用（dataSource.ts:109 赋值后未见消费）。

### S10 [P3] 默认 token 强度不足 + 文档不一致

`config.py:13` 默认 `change-me`，README 环境变量表写的是默认 `kb-dev-token`。`warn_insecure_token`（auth.py:23-35）只在等于 `change-me` 或长度 < 32 时告警——`kb-dev-token`（12 字符）会告警，但形如 `MyLibrary2024` 这类 12 字符以上的弱 token 不会。建议加一个弱口令字典/熵检查，并把 README 默认值对齐代码。

### S11 [P3] 阅读器 DOMPurify 默认允许远程图片，构成隐私泄漏面

`BookReader.tsx:660-668` 用 `DOMPurify.sanitize()` 默认配置净化 EPUB/MOBI 内容（防 XSS 本身是**做对了的**，这一点值得肯定）。但默认配置允许 `<img src="http://...">`，恶意/带追踪像素的电子书会在用户阅读时自动出网，泄漏 IP 与在线状态。建议配置 `ALLOWED_URI_REGEXP` 只允许 `data:` / `blob:` / 同源相对路径。

---

## 三、正确性与边界情况

### C1 [P1] ✅ 已修复 · 阅读时长按系统本地时区聚合，与代码注释声称的 UTC 矛盾

```python
# server/app/routers/stats.py:50-51
if isinstance(start_time, datetime) and isinstance(end_time, datetime):
    date = start_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
```

注释写「按 UTC 日聚合阅读分钟数（跨 SQLite/PostgreSQL 一致）」，且 `annotations.py:208` 也写着「SQLite 存的是 naive UTC」。但 `datetime.astimezone()` 作用在 **naive** datetime 上时，Python 会把它当作**系统本地时间**来解释。SQLite 返回的正是 naive datetime，所以在一台 UTC+8 的机器上，每条阅读记录都被当成 UTC+8 再换算成 UTC——日期整体偏移 8 小时。热力图、`recent_days`、`total_reading_minutes` 全部受影响。

PostgreSQL 分支返回 aware datetime，不受影响——所以这是一个**只在 SQLite 部署下出现的、跨后端不一致**的问题，正好是注释声称要避免的那类。

**修复**：naive 时 `start_time.replace(tzinfo=timezone.utc)`，aware 时 `astimezone(timezone.utc)`。

### C2 [P1] ✅ 已修复 · `list_documents` 默认无 limit，全表 + 全文载入内存

```python
# server/app/routers/documents.py:234
limit: int | None = Query(default=None, ge=1, le=500),
```

`limit=None` 时不加 `.limit()`，返回**全部**文档。前端 `loadLibrary()`（dataSource.ts:148）恰恰不带 limit。README 说「列表轻量化」，但轻量化只发生在**输出**环节（`:263-266` 逐条 `summarize_content`），**读取**环节仍然把每一行的 `content`（可能数百 KB 的全文）从 SQLite 读出并构造成 ORM 对象。在现有 571MB 库上是明确的内存与延迟问题。

**修复**：给 `limit` 设默认值（100/200）并保留 500 上限；列表查询改用 `select(...).options(load_only(...))` 或 `.defer(Document.content)`，只对需要摘要的文档按需取列。

### C3 [P1] Range 请求把整个区间读入内存

```python
# server/app/routers/documents.py:604-606
with open(path, "rb") as f:
    f.seek(start)
    chunk = f.read(length)
```

`bytes=0-` 时 `end = size - 1`，`length` 等于整个文件大小——一本 500MB 的 PDF 会被一次性读进内存；两三个并发就足以打爆进程。非 Range 路径用的 `FileResponse` 是流式的，Range 路径不是，两相对比很明显。

**修复**：改用生成器/`StreamingResponse` 分块返回，或直接用 `starlette.responses.FileResponse` 的 Range 支持（它本来就实现了）。

### C4 [P1] `/stats/health` 与重索引任务全量载入文档

- `stats.py:164-170`：`select(Document)` 无 limit 无 defer，全量载入后在 Python 里对每条 `content` 跑 `MENTION_PATTERN.findall`
- `reindex_job._do_run:102-107`：同样全量载入后逐个提取

两者的内存/CPU 都与库大小线性相关，且 health 是前端统计面板会周期性拉取的。

**修复**：分页批处理；用 SQL 侧的 `LIKE` 预筛或把占位提及统计也做成独立表/物化结果。

### C5 [P2] 提取线程池 2 worker + 超时不取消 → 队头阻塞

```python
# server/app/services/content_extract.py:28-43
_EXTRACT_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, ...)
future = _EXTRACT_POOL.submit(fn)
return future.result(timeout=timeout)   # 超时后任务仍在池里跑
```

`OCR_TIMEOUT=90`（秒）的任务或两个卡死的 PDF 解析会占满两个 worker，此后**所有**提取请求排队等待。上传接口虽然 5 秒后返回空文本，但线程仍被占用。这是共享池换来的副作用，注释说明了动机（避免游离线程累积），但没有配套的隔离手段。

**修复**：按任务类型分池（快提取 / OCR），或给 OCR 单独池；改用 `multiprocessing`/可取消的 future；至少加队列深度上限与丢弃策略。

### C6 [P2] notes 页 `createDocument` 调用在 try 块外

```ts
// src/app/notes/page.tsx:222-234
try {
  const hits = await reviewAnnotations(...);
  content = buildDailyNoteContent(hits, title);
} catch (err) {
  console.warn('获取今日标注失败:', err);   // try 到此结束
}

const doc = await createDocument({ type: 'note', title, content });   // ← 未兜异常
refetchNotes();
setEditingId(doc.id);
```

`createDocument` 失败时是未捕获的 Promise rejection，用户看不到任何提示，也不会 refetch。同一个文件里的 `handleCreate`（:194-206）是有 try/catch 的，属遗漏。

### C7 [P2] 「分类已存在」的本地化提示永不触发

```ts
// src/lib/dataSource.ts:371
throw new Error((err as Error).message.includes('409') ? `分类"${name}"已存在` : (err as Error).message);
```

但 `client.ts:46` 抛的是 `new Error(message)`，`message` 只保留了后端 `detail` 文本（如 `Collection 'x' already exists`），**状态码已被丢弃**，字符串里不会出现 `409`。因此这个分支是死分支，用户看到的是英文原文。

**修复**：让 `request()` 抛一个带 `status` 的错误类型（`class ApiError extends Error { status: number }`），调用方按 `status === 409` 判断。

### C8 [P2] 批注锚点的 chapter / page 互为兜底，语义不可区分

```ts
// src/lib/dataSource.ts:502-513
chapter: anchor.chapter ?? anchor.page ?? 0,
page: anchor.page ?? anchor.chapter ?? 0,
```

两个字段互相补位，导致「这是一条 EPUB 章节锚点」还是「这是一条 PDF 页码锚点」在下游无法判断。`BookReader` 的 `chapterKey`（:261）再按格式二次切换，恢复划线时可能错位。`apiAnnToUnified` 与后端 `MigrateBookmark`（只写 `page`）的语义也不一致。

**修复**：anchor 里保留原始字段（缺失就是 `undefined`），由消费方按 `document_type` 决定用哪个；或在后端写入时统一规范化 anchor schema。

### C9 [P2] `workflowCounts` 遇到非预期 workflow 值会产生 NaN

```ts
// src/app/notes/page.tsx:121-124
const counts = { all: notes.length, inbox: 0, review: 0, done: 0 };
notes.forEach((n) => {
  const wf = ((n.meta as { workflow?: string } | undefined)?.workflow || 'inbox') as 'inbox'|'review'|'done';
  counts[wf] += 1;
});
```

`as` 断言只是编译期声明，`meta.workflow` 是用户可写的任意字符串（`DocumentUpdate.meta: dict`）。若值为 `'archived'`，`counts['archived']` 是 `undefined`，`undefined + 1 = NaN`。

**修复**：`counts[wf] = (counts[wf] ?? 0) + 1`，或对 wf 做白名单收敛。

### C10 [P2] 下载的 object URL 同步 revoke，存在竞态

```ts
// src/lib/api/client.ts:456-461
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url;
a.download = filename;
a.click();
URL.revokeObjectURL(url);   // 立即撤销
```

`library/page.tsx` 对封面 URL 做了完整的生命周期管理（:124-157），说明团队知道这个问题；但下载路径仍是同步 revoke，在部分浏览器上会取消下载。改为 `setTimeout(() => URL.revokeObjectURL(url), 0)`。

### C11 [P3] `fetch` 无超时，`resolveMode` 可能长时间挂起

`client.ts:59`（`checkHealth`）与 `request()`（:27）都没有 `AbortSignal`。后端不可达时 `resolveMode()`（dataSource.ts:91）会一直等到浏览器/网络层超时，界面停在骨架态。

### C12 [P3] `useYjsCollab` 在 cleanup 之后可能返回已销毁的 Doc

```ts
// src/components/notes/useYjsCollab.ts:74-88
return () => {
  ...
  ydoc.destroy();
  ydocRef.current = null;      // 置空
  ...
};
// ↓ 但下一次 render 直接断言非空
return { ydoc: ydocRef.current as Y.Doc, provider, status, onlineUsers };
```

`as Y.Doc` 掩盖了 `null` 的可能。React 18 StrictMode 的双载（effect → cleanup → effect）之间若发生重渲染，`NoteEditor` 拿到的就是已 `destroy()` 的 Doc。建议返回可空类型或在 effect 内重建后立即 `setState` 触发重渲染。

### C13 [P3] 上传体积上限文档与实现不一致

`config.py:15` 默认 `500`，README 环境变量表写默认 `100`。同一张表里 `KB_API_TOKEN` 默认（`kb-dev-token` vs `change-me`）与 `KB_AUTO_CREATE_TABLES` 默认（`0` vs `True`）也都对不上。

### C14 [P3] README 声明的两个环境变量实际不存在

- `KB_EXTRACT_TIMEOUT_S`：全仓搜索无此字符串。`content_extract.EXTRACT_TIMEOUT` 是硬编码的 `5`
- `KB_ENABLE_OCR`：不在 `Settings` 里，是 `content_extract` 直接 `os.getenv` 读的，绕过了统一配置

另外 `content_extract.py:21` 的注释写 `OCR_TIMEOUT = 90  # OCR 通道独立预算（分钟级）`，实际单位是秒。

---

## 四、性能

| 编号 | 级别 | 问题 | 位置 |
| --- | --- | --- | --- |
| P1 | P1 | FTS 在部分部署路径下完全不生效，搜索退化为 `ILIKE '%q%'` 全表扫描 | 见 S2 |
| P2 | P2 | `q` / `since` 等参数未转义 LIKE 通配符（`%`、`_`），用户输入可改变匹配语义并放大扫描量 | `documents.py:250`、`search.py:114`、`annotations.py:52` |
| P3 | P2 | 重命名时的引用同步用 `content.ilike('%[[旧标题]]%')` 全表扫描，命中文档全部载入后 `re.sub` | `documents.py:322-338` |
| P4 | P2 | 导出/备份/统计把全量数据载入内存后再序列化 | `export.py:38`、`backup.py:57-64`、`stats.py:82` |
| P5 | P2 | `auto_classify.classify_by_content` 是 O(文本长度 × 关键词数) 的 `str.count` 扫描，200k 字 × ~200 关键词，跑在上传的同步路径上 | `auto_classify.py:71-80` |
| P6 | P3 | 每次外部请求都新建 `httpx.Client`，无连接池复用 | `metadata_fetch.py:41,79,106` |
| P7 | P3 | 连续滚动模式下对全书每章做 `DOMPurify.sanitize`，长书首次进入卡顿 | `BookReader.tsx:667-670` |
| P8 | P3 | 本地模式下 `storeFile` 用 `file.arrayBuffer()` 整文件进内存 | `fileStorage.ts:11-14` |

---

## 五、架构、可维护性与测试

### A1 [P2] `documents.py` 800 行承载 7 类职责

CRUD、上传、文件流式下载、封面、标签、版本历史、双链同步、在线元数据、文档洞察（关键词/摘要）全在一个模块里，还混着纯函数工具（`extract_keywords`、`one_line_summary`、`_context_snippet`）。它是全仓最大的文件，也是 `search.py`、`stats.py`、`annotations.py`、`migrate.py` 共同的下游依赖（`from .documents import ...`），耦合面很宽。

**建议**：拆成 `routers/documents/`（crud / upload / files / versions / metadata / insight），纯函数抽到 `services/text_analysis.py`，`_get_or_create_tag` 抽到 `services/tags.py`（现在被 `documents`、`migrate`、`clip` 三处 import 私有函数）。

### A2 [P1] 仓库卫生（这条放在 P1 是因为它已经在影响判断成本）

`server/` 目录的现状：

- 根目录散落 20+ 个一次性脚本：`reclassify.py` / `reclassify_by_content.py` / `reclassify_fulltext.py` / `reclassify_ocr.py`、`smoke_phase2/3/4.py`、`probe_book_remaining.py`、`apply_remaining.py`、`ocr_remaining.py`、`restore_ts.py`、`migrate_sqlite_to_api.py`、`batch_import.py`
- `server/scripts/` 另有 11 个 `inspect_nocover*.py` / `verify_covers*.py` / `test_backfill_match.py`
- 残留运行时产物：`dev.db`、`probe.db`、`probe-data/`、**5 组孤儿 `e2e-*.db-wal` / `-shm`**（主库文件已被清理或 gitignore，wal/shm 留了下来）、`knowledge_base.db-wal` 19MB 未 checkpoint
- 仓库根目录：`library_e2e.log` ~ `library_e2e5.log`、`library_fail.log`、`page_probe.html`、`coverage/`（68 个 html）、`playwright-report/`、`test-results/`
- 根目录 `scripts/` 的 47 个 Python 文件是**书籍爬虫/采购脚本**（`scrape_ja.py`、`zlib_search.py`、`filter_s2_*.py`…），与本项目无关，却和项目源码平级
- `.gitignore` 已覆盖 `*.log`、`server/*.db*`、`server/e2e-*/`，但**未覆盖** `coverage/`、`playwright-report/`、`test-results/`、`page_probe.html`

**建议**：一次性脚本移入 `server/tools/` 并在 README 标注「非运行时依赖」；孤儿 wal/shm 直接删除；`.gitignore` 补上 `coverage/`、`playwright-report/`、`test-results/`、`page_probe.html`；根目录 `scripts/` 要么移出仓库，要么在 README 里一句话说明它与本项目无关。

### A3 [P2] 文档漂移汇总

| README 说法 | 实际 |
| --- | --- |
| `KB_API_TOKEN` 默认 `kb-dev-token` | `config.py:13` 默认 `change-me` |
| `KB_UPLOAD_MAX_SIZE_MB` 默认 100 | `config.py:15` 默认 500 |
| `KB_AUTO_CREATE_TABLES` 默认 0 | `config.py:17` 默认 `True` |
| `KB_EXTRACT_TIMEOUT_S` 可调 | 不存在，硬编码 5 |
| 「限流」是功能 | 仅 upload / clip 两个端点 |
| 「列表轻量化」 | 只截断输出，读取仍全量 |

### A4 [P2] 死代码

- `metadata_extract._BLOCKED_EXTS`（:23）——定义了从不使用，真正的白名单在 `documents.UPLOAD_FORMAT_WHITELIST`。这个遗留常量会误导读者以为有第二层防护
- `metadata_extract.extract_metadata`（bytes 版，:26）——无任何调用点，只有 `extract_metadata_from_file` 被用
- `src/lib/fts.ts` 的 `escapeFtsQuery`——只有 `src/lib/__tests__/quality.test.ts` 引用，生产代码零调用（前端并不自己拼 FTS 语句）
- `server/app/middleware/rate_limit.py`——命名暗示是中间件，实为需手工调用的函数（见 S3）

### A5 [P2] 分类规则硬编码且要求"手工同步"多份副本

`auto_classify.RULES`（97 行中文分类表）的注释写着「与宿主机脚本 `server/reclassify.py` 保持同步」，`classify_by_content` 又写「与 `server/reclassify_by_content.py` 的 `score_classify` 保持一致」。代码与脚本各存一份、靠人工同步，必然漂移。

**建议**：抽成 `server/app/services/taxonomy.py`（或一张 DB 表 / YAML），脚本从同一处 import。另外 `classify()` 用子串匹配，`"cv"` 会命中任何含 `cv` 的文本（如 `CVE`），建议至少对 2 字符关键词加词边界。

### A6 [P2] 测试覆盖缺口

后端 `server/tests/` 只有 4 个文件（README 称 25 用例）。按风险排序，缺的是：

- `_snapshot_version` 的 100 版裁剪与 30 秒间隔跳过（含 `force=True` 路径）
- `update_document` 重命名时的 `[[旧标题]]` 引用同步（含大小写、含正则元字符的标题）
- Range 请求的 suffix 语义（`bytes=-N`）与 416 分支
- FTS 短词回退 ILIKE 的边界（`len(t)` 恰为 2、混合长度查询）
- `stats._build_day_map` 的时区处理（C1 的直接回归测试）
- `backup.restore` 的恶意 zip（目录逃逸、超大条目）
- `url_safety` 的 IPv4-mapped IPv6、重定向绕过（S4）

前端 vitest 覆盖 `src/lib/` 与少量组件；`BookReader.tsx`（1080 行，全仓最复杂组件）只有格式测试，划线/搜索/TTS/进度这些交互路径没有覆盖。

### A7 [P2] 双数据源模式的能力边界未在 UI 上表达

`dataSource.ts` 每个方法都是 `if (mode === 'api') ... else ...` 两套实现。local 模式下 `createHighlight`（:608）与 `convertAnnotationToNote`（:642）直接抛错，但 UI 上没有"当前处于离线模式、划线不可用"的提示，用户只在点了按钮之后才看到报错。建议把模式与能力降级显式呈现。

### A8 [P3] 类型安全的薄弱环节

`meta: dict`、`position: dict | None`、`anchor: dict`（schemas.py）都是无约束 dict，前后端靠注释约定字段名（`{"page": n}`、`{"chapter": n}`、`{"workflow": ...}`、`{"totalPages": ...}`）。`tsconfig.json` 开了 `strict` 与 `noUncheckedIndexedAccess`，但 `as { page?: number }` 这类断言绕过了它。建议后端用 Pydantic 子模型、前端对应 TS 接口，配合 discriminated union 区分 anchor 类型。

### A9 [P3] CI 的两个 audit 步骤永不阻断

```yaml
- name: Audit Python dependencies (warnings only)
  run: pip-audit
  continue-on-error: true
- name: Audit production dependencies (warnings only)
  run: npm audit --omit=dev
  continue-on-error: true
```

注释说明了理由（传递依赖误报），可以接受，但建议把结果存成 artifact 或失败时贴 PR 评论，否则这两步实际上等于没跑。另外 `infra-lint` 里 `hadolint ... || true` 同样是纯装饰。

### A10 [P3] 缺少 CSP

`next.config.mjs:15-26` 已配 `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy`，但没有 `Content-Security-Policy`。而阅读器是把外部 EPUB HTML 经 DOMPurify 后用 `dangerouslySetInnerHTML` 渲染的（`BookReader.tsx:892, 901`），CSP 是这个场景下最有价值的纵深防御（能兜住 DOMPurify 未来出现的 bypass）。

---

## 六、做得好的地方（避免改坏）

审查不是为了只挑毛病，以下几处是明确做对了的，重构时请保留：

1. **SQLite PRAGMA 集中在 `database.py:22-38`**，注释准确指出「缺 `foreign_keys=ON` 时所有 cascade 不生效」。这是很多 SQLAlchemy + SQLite 项目踩的坑。
2. **上传的重活全部 `run_in_threadpool` 下放**（`documents.py:463, 482-490`），并且注释说明了「否则阻塞事件循环、冻结全站请求」。
3. **EPUB/MOBI 内容一律经 DOMPurify 再 `dangerouslySetInnerHTML`**（`BookReader.tsx:455-456, 660-661, 667-670`），且搜索用的纯文本也走净化后的 DOM，不是直接正则剥标签。
4. **`clip.py` 的 SSRF 防护**（手动逐跳、每跳校验、跳数上限、流式字节上限）——实现质量高于同仓的封面下载路径。
5. **封面 blob URL 的完整生命周期管理**（`library/page.tsx:124-157`）：镜像 ref + 卸载 revoke + 书籍消失时清理，是全仓写得最细的资源管理。
6. **文件上传的落盘顺序**：先 commit 再删文件（`documents.py:413-418`），注释明确「commit 失败时记录仍在、可重试」。
7. **`collections.py:56-71` 的父级环检测**，覆盖了"指向自身"与"指向子孙"两种情况。
8. **`db.ts:397-406` 的注释**（不能用 `new Promise(async ...)` 包裹 `await initDB()`）——说明团队真的调试过这个挂起问题。

---

## 七、建议的修复顺序（按性价比）

**第一批（本迭代，风险最高 / 成本最低）**

1. **S1** 从 Dockerfile / compose 移除 `NEXT_PUBLIC_API_TOKEN` 注入 — 改动 3 处
2. **S2** `lifespan` 无条件调用 `ensure_fts_index` + 加一条回归测试 — 改动 3 行
3. **C1** `stats.py` 时区修复 — 改动 2 行
4. **C2** `list_documents` 加默认 limit + `defer(content)` — 改动 5 行
5. **S5** `file_download_headers` 的 filename 白名单化 — 改动 3 行
6. **A2** 清理孤儿 wal/shm + `.gitignore` 补 4 项 — 纯运维

**第二批（下个迭代）**

7. **S3** 限流改为中间件并分级
8. **S4** 抽出 `url_safety.fetch_with_guard` 供 clip 与封面下载共用
9. **C3** Range 改流式
10. **C5 / C4** 提取池分级；health 与重索引改分页批处理
11. **C6 / C7 / C9 / C10** 前端四个小修复

**第三批（结构性）**

12. **A1** 拆分 `documents.py`
13. **A5** 分类规则单一来源
14. **A6** 补齐上表列出的回归测试
15. **A8** anchor / meta 的类型化

---

## 附：本次审查未覆盖的部分

- `src/lib/parsers/foliate-js/`（vendored 第三方 epubcfi / mobi 实现，未逐行审）
- `server/alembic/versions/` 6 个迁移文件的 SQL 正确性（仅确认版本号链条完整到 0006）
- 根目录 `scripts/` 的 47 个爬虫脚本（与本项目无关）
- `server/` 根目录与 `server/scripts/` 下的一次性运维脚本
- 实际运行态验证（未启动服务、未跑测试、未做压力测试）——以上结论均来自静态阅读与交叉引用
