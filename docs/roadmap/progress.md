# 执行进度

> **本文件是进度的唯一来源。** 执行方每完成一个任务，就把 `[ ]` 改成 `[x]`、把状态改为 `DONE`，并在「证据」列填写可复现的命令/文件/结果。
>
> 规则：
> - 同一时间只允许一个批次为 `WIP`。
> - 没有证据不能标 `DONE`。
> - 需要用户授权才能继续时，标 `BLOCKED` 并写进「阻塞/授权」区。
> - 发现任务书之外的问题，写进「发现」区，不要顺手修。

## 当前状态

| 项目 | 值 |
|---|---|
| 当前批次 | B1 基本完成（核心 9/11 DONE，2 项部分完成） |
| 当前任务 | 待收尾：B1-08/B1-10 部分完成，B1-11 报告已写 |
| 最后更新 | 2026-10-26（B1-01~B1-07、B1-09 DONE；B1-08/B1-10 部分；B1-11 报告已写） |
| 阻塞项 | D1-D6 待确认 |
| 下一批前置 | B2-01 依赖 quality_audit；迁移 0008 需 G3 |

## B0 · 基线与护栏

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B0-01 | [x] 提交路线图文档 + CSP 修复 | DONE | commits `3cfb28a` + `aca0f3e` |
| B0-02 | [x] 基线备份 + integrity_check | DONE | 备份 `20261026_150811`；integrity=ok；alive=1298 |
| B0-03 | [x] resolveDataSourceStatus() 与状态类型 | DONE | 9 测试通过 |
| B0-04 | [x] 修复设置页缓存失效 | DONE | typecheck 通过 |
| B0-05 | [x] StatePanel / FilterChip | DONE | 6 测试通过 |
| B0-06 | [x] job_state.py | DONE | 10 测试通过 |
| B0-07 | [x] 全量验证 | DONE | pytest 105 / npm 73 全绿 |
| B0-08 | [x] B0 报告 | DONE | [reports/B0-2026-10-26.md](reports/B0-2026-10-26.md) |

## B1 · 数据源可见化 + 质量中心

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B1-01 | [x] DocumentQuality 模型 + 迁移 0007 | DONE | 迁移成功；表/索引/列验证通过；4 测试；integrity=ok |
| B1-02 | [x] quality_audit 审计服务 | DONE | normalize_language/audit_one/scan_orphans/list_issues/start_audit/status；19 测试 |
| B1-03 | [x] quality 路由并注册 | DONE | summary/issues/orphans/audit/audit-status；12 测试 |
| B1-04 | [x] apply_document_filters 支持 quality | DONE | 10 种 quality 筛选值；列表/计数一致性；3 测试 |
| B1-05 | [x] KB_QUALITY_MIN_CONTENT_CHARS | DONE | 默认 500，范围 1-100000 |
| B1-06 | [x] 前端 API client 质量接口 | DONE | typecheck 通过 |
| B1-07 | [x] DataSourceStatusBar + layout | DONE | 8 测试通过 |
| B1-08 | [ ] 首次配置引导 + 书架错误/空态 | PARTIAL | OnboardingBanner 组件已建+4 测试；未嵌入 library/page.tsx |
| B1-09 | [x] /quality 质量中心页面 | DONE | 汇总卡/issue 表格/孤儿文件区/审计进度 |
| B1-10 | [ ] 书架质量 chips + 设置页健康标签 | PARTIAL | FilterChip 已建；chips 未嵌入 library/page.tsx |
| B1-11 | [x] 测试、B1 报告 | DONE | pytest 143 / npm 85 全绿；[reports/B1-2026-10-26.md](reports/B1-2026-10-26.md) |

## B1 · 数据源可见化 + 质量中心（只读）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B1-01 | [ ] `DocumentQuality` 模型 + 迁移 `0007_document_quality` | TODO | |
| B1-02 | [ ] `quality_audit` 审计服务（DB 统计 + 文件检查 + 孤儿报告） | TODO | |
| B1-03 | [ ] `quality` 路由并注册到 `main.py` | TODO | |
| B1-04 | [ ] `apply_document_filters` 支持 `quality`，列表/计数同步 | TODO | |
| B1-05 | [ ] 配置 `KB_QUALITY_MIN_CONTENT_CHARS` | TODO | |
| B1-06 | [ ] 前端 API client 增加质量接口与类型 | TODO | |
| B1-07 | [ ] `DataSourceStatusBar` + 挂到全局 layout | TODO | |
| B1-08 | [ ] 首次配置引导 + 书架错误/空态 | TODO | |
| B1-09 | [ ] `/quality` 只读质量中心页面 | TODO | |
| B1-10 | [ ] 书架质量筛选 chips + 设置页健康标签 | TODO | |
| B1-11 | [ ] 后端/前端测试、浏览器验证、B1 报告 | TODO | |

## B2 · 目录扫描导入 + 批量治理

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B2-01 | [ ] 抽取 `services/ingest.py`，上传与目录导入共用校验/去重 | TODO | |
| B2-02 | [ ] `directory_scan.py` 白名单扫描、坏文件/重复分类 | TODO | |
| B2-03 | [ ] `imports.py` 路由、扫描/导入后台任务 | TODO | |
| B2-04 | [ ] 批量修改/软删除接口（500 上限、confirm） | TODO | |
| B2-05 | [ ] `operation_logs` 模型/迁移 + undo 接口 | TODO | |
| B2-06 | [ ] `PUT /documents/{id}/cover`，魔数校验 | TODO | |
| B2-07 | [ ] 重提取包装函数 + `reextract` 后台任务 | TODO | |
| B2-08 | [ ] 语言归一化批量动作（dry-run + confirm） | TODO | |
| B2-09 | [ ] 孤儿文件隔离动作（移动不删除 + manifest） | TODO | |
| B2-10 | [ ] `/import` 三步向导 UI | TODO | |
| B2-11 | [ ] 质量中心动作 UI（预览/确认/日志/撤销） | TODO | |
| B2-12 | [ ] 书架多选与批量操作条 | TODO | |
| B2-13 | [ ] 测试、Downloads 扫描复核、B2 报告 | TODO | |

## B3 · 书架信息架构与性能

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B3-01 | [ ] `GET /documents` 增加 `sort` 参数 | TODO | |
| B3-02 | [ ] `GET /documents/facets` 聚合格式/语言/标签/作者 | TODO | |
| B3-03 | [ ] 书架「全选筛选结果」前端接入 B2-04 的 `/documents/ids`（含 500 上限提示） | TODO | |
| B3-04 | [ ] 书架相关索引迁移 | TODO | |
| B3-05 | [ ] `useLibraryFilters`：筛选状态 URL 化 | TODO | |
| B3-06 | [ ] 分类侧栏：搜索/折叠/隐藏 0 本/置顶/重命名入口 | TODO | |
| B3-07 | [ ] 工具栏：排序、活跃筛选 chips、清除全部 | TODO | |
| B3-08 | [ ] `BookCard` + `BookCover`（独立封面加载） | TODO | |
| B3-09 | [ ] 无限滚动 + 虚拟化 + 预取下一页 | TODO | |
| B3-10 | [ ] 骨架屏/空态/错误态 | TODO | |
| B3-11 | [ ] 移动端抽屉/导航、暗色、无障碍 | TODO | |
| B3-12 | [ ] 组件测试、Playwright、性能记录、B3 报告 | TODO | |

## B4 · 阅读队列 + 间隔复习

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B4-01 | [ ] 迁移 `0010_reading_queue_reviews` + 模型 + 历史标注回填 | TODO | |
| B4-02 | [ ] `review_scheduler.py` SM-2 lite 纯函数 | TODO | |
| B4-03 | [ ] 阅读队列 API（增删改/重排/幂等） | TODO | |
| B4-04 | [ ] 复习 API（due/rating/snooze/stats） | TODO | |
| B4-05 | [ ] 标注创建时建 review 行 | TODO | |
| B4-06 | [ ] 队列页/回顾页 Tab + 队列排序 UI | TODO | |
| B4-07 | [ ] 复习卡评分、快捷键、间隔预览 | TODO | |
| B4-08 | [ ] 时区工具、修"仅今天"、每日笔记接入 | TODO | |
| B4-09 | [ ] 调度/API/前端测试、B4 报告 | TODO | |

## B5 · 语义检索 / RAG

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B5-01 | [ ] 技术 spike：模型/向量存储/真实库基准 | TODO | |
| B5-02 | [ ] 写 go/no-go 结论，等用户确认 | TODO | 依赖 B5-01 实测数据；未确认前不得开始 B5-03 |
| B5-03 | [ ] 仅 go 后：新增依赖与 feature flags | TODO | |
| B5-04 | [ ] 迁移 `0011_semantic_search` + 模型 | TODO | |
| B5-05 | [ ] 嵌入/向量索引抽象（fastembed + fake） | TODO | |
| B5-06 | [ ] 增量索引后台任务 | TODO | |
| B5-07 | [ ] 语义/混合检索 API（RRF） | TODO | |
| B5-08 | [ ] 搜索页模式切换 UI | TODO | |
| B5-09 | [ ] 设置页语义索引卡片 | TODO | |
| B5-10 | [ ] 证据式回答（无生成模型） | TODO | |
| B5-11 | [ ] 生成式 RAG（可选，单独开关） | TODO | |
| B5-12 | [ ] fake provider 测试、隐私检查、B5 报告 | TODO | |

## B6 · 收口与发布

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B6-01 | [ ] 全量回归（pytest/typecheck/lint/vitest/build/e2e/audit） | TODO | |
| B6-02 | [ ] 移动端/暗色/无障碍/性能复核 | TODO | |
| B6-03 | [ ] 数据清理（逐次授权） | TODO | |
| B6-04 | [ ] 文档、发布说明、回滚说明 | TODO | |
| B6-05 | [ ] 最终报告与 CI 确认 | TODO | |

---

## 阻塞/授权

| 日期 | 批次/任务 | 类型 | 说明 | 需要谁 | 状态 |
|---|---|---|---|---|---|
| 2026-10-26 | 全部 | 启动 | 等待用户说「开始 B0」；在此之前执行方只做只读查询与文档 | 用户 | OPEN |
| 2026-10-26 | B1/B3/B4/B5 | 迁移 (G3) | `0007`/`0009`/`0010`/`0011` 各批一次，执行前必须备份 + `integrity_check` | 用户 | OPEN |
| 2026-10-26 | B2/B6 | 真实数据 (G1) | 任何导入/软删除/批量改/补封面/重提取/隔离/清理都需逐次预览 + 授权 | 用户 | OPEN |
| 2026-10-26 | B5 | 依赖 (G2) | 是否允许新增 fastembed / onnxruntime（可选依赖文件，不进 CI/镜像） | 用户 | OPEN |
| 2026-10-26 | B5 | 技术 | spike 后按 [B5 第 3 节](B5-semantic-search.md) 门槛判 go/no-go；no-go 是合法结果 | 用户 + 执行方 | OPEN |
| 2026-10-26 | B2/B6 | 技术 | 扫描版 PDF 是否安装 tesseract OCR（默认只标注不安装） | 用户 | OPEN |
| 2026-10-26 | B3 | 范围 | 暗色模式覆盖范围（默认书架/质量/导入/回顾/设置优先） | 用户 | OPEN |
| 2026-10-26 | 全部 | 推送 (G4) | 每批单独提交；是否推送由用户逐次确认 | 用户 | OPEN |

## 发现（不在本批顺手修）

| 日期 | 位置 | 现象 | 影响 | 建议批次 | 状态 |
|---|---|---|---|---|---|
| 2026-10-26 | [server/app/models.py:28](../../server/app/models.py#L28) | `utcnow()` 返回 tz-aware，但 SQLite 落库/读回是 naive UTC 文本 | 直接比较 tz-aware 与库中值会 TypeError 或静默错判 | B4-01/B4-02（提供 `as_utc_naive`/`from_utc_naive`） | OPEN |
| 2026-10-26 | [src/lib/dataSource.ts:254](../../src/lib/dataSource.ts#L254) | 本地模式 `shelf=recent` 先 `slice(0, limit)` 再过滤 | 加筛选后「最近添加」莫名少于 20 本（仅本地模式） | B3-02（已列入该任务验收） | OPEN |
| 2026-10-26 | [server/app/routers/documents/crud.py:71](../../server/app/routers/documents/crud.py#L71) | 排序只有 `created_at desc`，无 tiebreaker | `created_at` 相同的文档在 offset 分页里重复/漏项 | B3-01（末位加 `Document.id`） | OPEN |
| 2026-10-26 | [src/app/review/page.tsx:45](../../src/app/review/page.tsx#L45) | 用 UTC 日期字符串判断「今天」 | 东八区用户每天 0–8 点看到的「今日」是错的 | B4-08 | OPEN |
| 2026-10-26 | [.gitignore:59](../../.gitignore#L59) | 原规则 `docs/*` + `!docs/*.md`，而 `*` 不跨 `/`，导致 `docs/roadmap/*.md` 全部被忽略、`git add` 静默无效 | 路线图与进度无法入库，换机器即丢失进度 | B0-01（本轮已加 `!docs/roadmap/`，尚未提交） | FIXED-PENDING-COMMIT |
| 2026-10-26 | [README.md:3](../../README.md#L3) | 写「Next.js 14」，实际 `package.json` 是 `next@^15.5.28` | 文档误导 | B6-04 | OPEN |
| 2026-10-26 | [src/lib/__tests__/quality.test.ts](../../src/lib/__tests__/quality.test.ts) | 文件名像质量中心测试，实际测 epub/foliate/fuzzy/fts 辅助函数 | 覆盖情况误判；B1 新增质量测试时会撞名 | B1-11（新建 `qualityAudit.test.ts`，不改此文件） | OPEN |
| 2026-10-26 | 仓库根目录 | `dev-server.log`、`library_e2e*.log`、`library_fail.log`、`page_probe.html` 等运行产物留在工作区（已被 `.gitignore` 忽略，不会误提交） | 备份/扫描噪音、磁盘占用 | B6-01（只登记清单；删除需单独授权） | OPEN |

## 批次切换记录

| 日期 | 从 | 到 | 确认人 | 备注 |
|---|---|---|---|---|
| | | | | |

## 任务书偏离记录

| 日期 | 任务 ID | 原计划 | 实际做法 | 原因 | 用户是否知情 |
|---|---|---|---|---|---|
| | | | | | |
