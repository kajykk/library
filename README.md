# 个人电子书 / 知识库系统

自托管个人电子书库 + 读书笔记 + 双向链接知识库。前端 Next.js 14(App Router),后端 FastAPI + SQLite(SQLAlchemy 2),实时协作基于 Yjs / pycrdt-websocket。

## 功能

- **书架**：EPUB / PDF / MOBI 上传与解析(自带 foliate-js 解析器)、封面、分类、阅读进度、增量渲染(大书库按需加载封面与列表)
- **阅读器**：分页 / 连续滚动、字号调整、书内搜索、划线与批注(带跳回)、书签
- **全文检索**：SQLite FTS5 trigram(中文 ≥3 字)+ ILIKE 兜底(<3 字查询自动回退,混合长度查询不静默漏配)
- **笔记**：Markdown 编辑器、wiki 链接与反向链接、模板、标签、版本历史、Markdown 导入导出、Yjs 多人实时协作(在线成员可见)
- **知识图谱**：文档/标签关系图、占位提及诊断、统计面板
- **运维**：后台全文索引重建(每文件 5s 预算跳过扫描版 PDF)、SHA-256 去重、备份/恢复(ZIP)、剪藏、上传/剪藏端点限流

## API 行为注记

- **列表轻量化**：`GET /api/documents` 默认 `include_content=false`，`content` 仅返回压平空白后的 **200 字摘要**(超出截断加 `…`)，全文走 `GET /api/documents/{id}` 详情接口;`include_content=true` 向后兼容返回全文。轻量模式下正文不进 ORM(只取前缀做摘要)，避免大书库列表把全文读进内存。
- **列表分页上限**：不传 `limit` 时按默认 **200** 条截断，显式传值的硬上限 **1000**(`le=1000`)。超过硬上限直接 **422**，不会静默夹取。需要"全量"语义的场景必须自己按 `offset` 翻页（前端 `listAllDocuments` 已封装）；书架走真正的分页加载，不受该上限影响。
- **书架侧栏统计**：`GET /api/documents/stats` 一次返回 `total / reading / unread / done / by_collection / uncategorized`（只统计 `type in (book, pdf)`，`deleted_at is null`）。此前前端为了显示"共 N 本"和各分类数量，先拉 `limit=1000` 的整库列表再在内存里数 —— 库超过 1000 本时统计和列表一起被截断。
- **列表过滤参数**：`types`（逗号分隔，如 `book,pdf`）、`status`（`all|reading|unread|done`）、`author`（模糊匹配）与 `q / collection_id / tag / type` 可组合；`GET /api/documents/count` 接受同一套参数，用于"共 N 本"与剩余量。
- **e2e 前置构建**：Playwright 冒烟测试用 `next start` 起前端,需先 `npm run build`;CI 的 e2e job 已内置该构建步骤。

## 目录结构

```
server/              FastAPI 后端
  app/main.py        应用入口(路由、WS、CORS、启动迁移)
  app/routers/       业务路由(documents/search/notes/annotations/graph/backup/clip...)
  app/services/      全文提取(content_extract)、重索引任务(reindex_job)、搜索索引
  app/collab.py      Yjs WebSocket 协作(数据落盘 data/collab.db)
  app/models.py      SQLAlchemy 模型(含全文搜索触发器)
  alembic/           数据库迁移
  tests/             pytest(93 用例,临时 sqlite)
src/                 Next.js 前端
  app/               页面(library/reader/notes/graph/search/settings)
  components/        阅读器(BookReader)、统计、命令面板等
  lib/               API client、解析器(foliate-js)、浏览器端存储
e2e/                 Playwright 冒烟测试(fixture 由 global-setup 生成)
```

## 快速开始

后端(默认端口 8000,真实库 `server/knowledge_base.db`):

```bash
cd server
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
alembic upgrade head                               # 已有库时执行迁移
uvicorn app.main:app --port 8000
```

前端(默认端口 3000):

```bash
npm install
npm run dev        # 开发
npm run build && npm run start   # 生产
```

浏览器打开 `http://localhost:3000`,在"设置"页填写 API 地址与 Token。

> **安全约束**：Token 只能走运行时路径(设置页写入浏览器 localStorage)。`NEXT_PUBLIC_*`
> 变量会在 `next build` 时内联进客户端 JS bundle,把 Token 放进构建参数等于公开发布 ——
> 所以 `Dockerfile` / `docker-compose.yml` 不接受 `NEXT_PUBLIC_API_TOKEN`。

## 环境变量(后端)

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KB_DATABASE_URL` | `sqlite:///./knowledge_base.db` | 数据库连接串 |
| `KB_DATA_DIR` | `./data` | 文件与协作数据目录 |
| `KB_API_TOKEN` | `change-me` | 全站 API Token(X-API-Token 头)。启动时会对弱 token 告警,生产务必替换 |
| `KB_AUTO_CREATE_TABLES` | `1` | 启动时自动建表;生产走 Alembic 时设 `0`(FTS 索引与该项无关,启动时独立初始化) |
| `KB_CORS_ORIGINS` | `http://localhost:3000` | 允许的前端来源 |
| `KB_UPLOAD_MAX_SIZE_MB` | `500` | 上传体积上限 |
| `KB_ENABLE_OCR` | `0` | 扫描版 PDF OCR(需 tesseract 二进制 + pymupdf/pytesseract/Pillow) |
| `KB_AUTO_CLASSIFY_OCR` | `0` | 导入后后台 OCR 自动分类扫描件(需 pip install rapidocr-onnxruntime pymupdf) |

> 已废弃/未实现的变量:`KB_EXTRACT_TIMEOUT_S` 此前写在表里但代码中不存在,单个文件正文提取预算目前硬编码为 5s(`content_extract.EXTRACT_TIMEOUT`)。

## 环境变量(前端)

### CSP connect-src 配置

默认 CSP 的 `connect-src` 为 `'self' http: https: ws: wss:`(宽松,兼容运行时 API 配置)。
如需收紧,构建时设置:

```bash
NEXT_PUBLIC_CSP_CONNECT_SRC="'self' https://api.example.com wss://api.example.com" npm run build
```

注意:`NEXT_PUBLIC_*` 在构建期内联进产物,运行时修改无效。

## 测试

```bash
cd server && python -m pytest tests -q      # 后端 93 用例
npm run lint && npx tsc --noEmit && npm run build   # 前端静态检查
npm run test:e2e                            # Playwright 冒烟(本机用 Edge;CI 用 chromium)
```

e2e 每次运行使用全新临时库(`server/e2e-*.db`),不污染真实数据;需先 `npm run build`(CI 已内置)。

## 真实库迁移

仓库自带 `server/knowledge_base.db`(迁移至 alembic 0006,含 sha256 去重列)。升级路径:

```bash
cd server
alembic stamp <当前版本> && alembic upgrade head
```

旧书无正文全文:设置页"全文索引维护"重建(后台任务,扫描版 PDF 按时间预算跳过)。重复文件上传返回 409 并给出已存在书籍。

## 常见坑位

- FTS trigram 只含 ≥3 字符 token:含 2 字词的查询整体回退 ILIKE(`search.py` 已处理)
- 日志消息不要含 `%` 字符(如 `%PDF`),会触发 logging 格式化异常
- y-websocket 自行拼接 `serverUrl + '/' + room`,room/token 不要内嵌在 serverUrl 里
- 前端大书库默认只渲染 60 本,滚动到底"加载更多";封面按需拉取
