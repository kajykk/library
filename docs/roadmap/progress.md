# 执行进度

> **本文件是进度总览；返工期间的实时状态见 [REWORK-2026-10-27.md](REWORK-2026-10-27.md) 第 6 节。**
>
> 规则：
> - 同一时间只允许一个批次为 `WIP`。
> - 没有证据不能标 `DONE`。
> - 需要用户授权才能继续时，标 `BLOCKED` 并写进「阻塞/授权」区。
> - 发现任务书之外的问题，写进「发现」区，不要顺手修。

## 当前状态

| 项目 | 值 |
|---|---|
| 当前批次 | **返工 R1–R7 全部完成并提交**（2026-10-27；R6 = `fef49c7`，R7 = `692ff1b`，B5 结论 **NO-GO**；均未推送） |
| 当前任务 | 无（R7 四问已定：1 NO-GO / 2 不选型 / 3 不批准依赖 / 4 不做生成式 RAG） |
| 最后更新 | 2026-10-27（R7 已提交） |
| 阻塞项 | D4 OCR、D5 暗色范围、G4 推送授权；另有两项**等用户一句话**：是否删除 spike 盘（≈1.84 GB）、是否开工建议批次 R8（修 F-15 中文分词） |
| 下一步 | B5-03~B5-12 依 NO-GO 结论**不得开工**；建议下一批 R8 只做 `/api/search` 的中文关键词通道（本轮实测它才是用户「搜不到/搜得慢」的真因） |

状态口径：`DONE` / `PARTIAL` / `TODO` / `SKIPPED`。证据里的 `RD-*`/`RW-*` 均指 [REWORK-2026-10-27.md](REWORK-2026-10-27.md)。

### 返工 R7 结果（2026-10-27，详见 REWORK §7.11 与 [spikes/B5-01-embedding-spike.md](spikes/B5-01-embedding-spike.md)）

| 项 | 结果 |
|---|---|
| 门槛对照 | 相关性 / 体积 / CI / 隐私 **PASS**；索引耗时 / hybrid P95 / 常驻内存 **FAIL** → 按「任一项不达标即 no-go」判 **NO-GO** |
| 四问签核 | 用户回复「你决定」→ 执行方按原文规则定：1 NO-GO、2 不选型（倾向 bge 记录在案）、3 不批准 `fastembed`/`onnxruntime`、4 不做生成式 RAG（[B5-02-decision.md](spikes/B5-02-decision.md) §5） |
| 产品代码 | **零改动**：无迁移 `0011`、无 `requirements-semantic.txt`、`server/.venv` 未装任何 B5 依赖、真实库与 `server/data/` 零写入 |
| 唯一实测收益 | 定位到用户可感知的搜索问题根因＝[search.py:192-197](../../server/app/routers/search.py#L192-L197) 的 2 字词必然退化为四列全表 `LIKE`（关键词通道 694–1,312 ms，且 FTS 对 2 字词静默返回 0 行）；语义向量本身 P95 ≤8.81 ms 完全达标 → F-15 |
| 盘 | `.spike-venv/` 175 MB + `spike-out/` ≈1.66 GB 暂保留，等用户确认再删；永久证据已入库（报告 + 决策页 + `B5-01-judgments.json` + `spike_semantic.py`） |

### 返工 R6 结果（2026-10-27，详见 REWORK §7.10）

| 任务 | 结果 |
|---|---|
| RW-601 | 后端 204→**248 passed**（+44）、前端 143→**154 passed**（+11）；新增 27 例后端连跑 3 次稳定；`tsc`/`eslint`/`build` 全绿 |
| RW-602 | 四条 CPU 密集路径 2/60s；`sm2_lite(now=…)` 注入；`filters.py` 未用导入清零；红线 8+9 例→全绿 |
| 未含 | F-5/F-8/F-9/F-11 仍在发现区；新增 F-13（无 ruff 配置/门禁）、F-14（reextract worker 无 pytest 固化） |

### 数据清理执行结果（2026-10-27，详见 [reports/B6-cleanup-2026-10-27.md](reports/B6-cleanup-2026-10-27.md)）

| 指标 | 前 | 后 | operation_id |
|---|---|---|---|
| 正文为空 | 582 | **207** | `be0fcd7f`（重提取 360 本） |
| 正文 ≥500 字 | 695 | **1052** | — |
| 正文覆盖率 | 53.6% | **82.1%** | — |
| 语言变体 | 27 | **0** | `109ed19d`（可逆） |
| 坏文件（410页/空文件） | 15 | **0**（已软删） | `a2148f21`（可逆） |
| 未分类 | 32 | 22 | `8a4cb41b`（可逆） |
| 备份 | — | `20261027_004916` | integrity=ok |

## B0 · 基线与护栏 — DONE（8/8）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B0-01 | 提交路线图文档（含 `.gitignore` 例外）+ dev-only CSP 修复 | DONE | 提交 `aca0f3e`；[.gitignore](../../.gitignore) `!docs/roadmap/`；`git ls-files docs/roadmap` 非空 |
| B0-02 | 基线备份 + `integrity_check` + 记录计数 | DONE | [reports/B0-2026-10-26.md](reports/B0-2026-10-26.md) |
| B0-03 | `resolveDataSourceStatus()` 与状态类型 | DONE | [dataSourceStatus.ts](../../src/lib/dataSourceStatus.ts) + `dataSourceStatus.test.ts` |
| B0-04 | 修复设置页保存后缓存未失效 | DONE | [reports/B0-2026-10-26.md](reports/B0-2026-10-26.md) |
| B0-05 | `StatePanel` / `FilterChip` 导出 | DONE | [src/components/ui/](../../src/components/ui/) + `statePanel.test.tsx` |
| B0-06 | `job_state.py` 后台任务状态基座 | DONE | [job_state.py](../../server/app/services/job_state.py) + [test_job_state.py](../../server/tests/test_job_state.py) |
| B0-07 | B0 单元测试与前端测试 | DONE | 上述后端/前端测试用例 |
| B0-08 | 全量验证并写 B0 报告 | DONE | [reports/B0-2026-10-26.md](reports/B0-2026-10-26.md) |

## B1 · 数据源可见化 + 质量中心 — DONE（11/11）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B1-01 | `DocumentQuality` 模型 + 迁移 `0007_document_quality` | DONE | [models.py:171](../../server/app/models.py#L171)；迁移链头 `0010_reading_queue_reviews` |
| B1-02 | `quality_audit` 审计服务 | DONE | [quality_audit.py](../../server/app/services/quality_audit.py) + [test_quality_audit.py](../../server/tests/test_quality_audit.py) |
| B1-03 | `quality` 路由并注册 | DONE | [routers/quality.py](../../server/app/routers/quality.py) |
| B1-04 | `apply_document_filters` 支持 `quality` | DONE | [documents/filters.py](../../server/app/routers/documents/filters.py) + [test_quality_api.py](../../server/tests/test_quality_api.py) |
| B1-05 | 配置 `KB_QUALITY_MIN_CONTENT_CHARS` | DONE | [config.py:26](../../server/app/config.py#L26) |
| B1-06 | 前端 API client 质量接口与类型 | DONE | [src/lib/api/](../../src/lib/api/) + [quality/page.tsx](../../src/app/quality/page.tsx) |
| B1-07 | `DataSourceStatusBar` + 全局 layout | DONE | [DataSourceStatusBar.tsx](../../src/components/DataSourceStatusBar.tsx) + 测试 |
| B1-08 | 首次配置引导 + 书架错误/空态 | DONE | [OnboardingBanner.tsx](../../src/components/OnboardingBanner.tsx)；[library/page.tsx:844-861](../../src/app/library/page.tsx#L844-L861) |
| B1-09 | `/quality` 只读质量中心页面 | DONE | [quality/page.tsx](../../src/app/quality/page.tsx) |
| B1-10 | 书架质量筛选 chips + 设置页健康标签 | DONE | [library/page.tsx:782](../../src/app/library/page.tsx#L782)；设置页健康卡 |
| B1-11 | 后端/前端测试、浏览器验证、B1 报告 | DONE | [reports/B1-2026-10-26.md](reports/B1-2026-10-26.md)；`test_quality*.py` |

## B2 · 目录扫描导入 + 批量治理 — DONE（10/13）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B2-01 | 抽取 `services/ingest.py`，上传与目录导入共用 | DONE | [ingest.py](../../server/app/services/ingest.py)；`file_storage.import_file` |
| B2-02 | `directory_scan.py` 白名单扫描 | DONE | [directory_scan.py](../../server/app/services/directory_scan.py)；RW-102 加固见 REWORK §7.2 |
| B2-03 | `imports.py` 路由、扫描/导入任务 | DONE | [imports.py:113-213](../../server/app/routers/imports.py#L113-L213)；RW-102 改为 `dir_id + rel_paths` |
| B2-04 | 批量修改/软删除（500 上限、confirm） | DONE | [documents/batch.py](../../server/app/routers/documents/batch.py)；嵌套 `patch`/`missing_ids`/tags 契约对齐见 REWORK §7.6 |
| B2-05 | `operation_logs` + undo 接口 | DONE | [operation_log.py](../../server/app/services/operation_log.py)、[operations.py](../../server/app/routers/operations.py)；状态词与 Uuid 恢复见 REWORK §7.6 |
| B2-06 | `PUT /documents/{id}/cover`，魔数校验 | DONE | [imports.py:367-420](../../server/app/routers/imports.py#L367-L420)；multipart/8 MB/JPEG·PNG·GIF·WebP 见 REWORK §7.6；撤销字节限制见 REWORK F-9 |
| B2-07 | 重提取包装函数 + `reextract` 后台任务 | DONE | [content_extract.py:322](../../server/app/services/content_extract.py#L322) 预算包装（旧签名不变）+ [reextract_job.py](../../server/app/services/reextract_job.py) + [documents/reextract.py](../../server/app/routers/documents/reextract.py)；临时库探针 42 例全过，REWORK §7.7 |
| B2-08 | 语言归一化批量动作（dry-run + confirm） | DONE | [imports.py:208-291](../../server/app/routers/imports.py#L208-L291)：dry-run/500 分批/未知值保留，REWORK §7.6 |
| B2-09 | 孤儿文件隔离（移动不删除 + manifest） | DONE | [orphan_quarantine.py](../../server/app/services/orphan_quarantine.py)；RW-103（REWORK §7.3） |
| B2-10 | `/import` 三步向导 UI | DONE | [import/page.tsx](../../src/app/import/page.tsx) |
| B2-11 | 质量中心动作 UI（预览/确认/日志/撤销） | PARTIAL | [quality/page.tsx:314](../../src/app/quality/page.tsx#L314) 仍写「隔离操作将在 B2 批次提供」；RD-B 最小版未含，留待后续迭代 |
| B2-12 | 书架多选与批量操作条 | PARTIAL | 全仓无 `useLibrarySelection`；RD-B 最小版未含，留待后续迭代 |
| B2-13 | 测试、Downloads 扫描复核、B2 报告 | DONE | 后端 195 passed（R3 后）、前端 85 passed；[reports/B2-B6-2026-10-26.md](reports/B2-B6-2026-10-26.md) |

## B3 · 书架信息架构与性能 — PARTIAL（6/12）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B3-01 | `GET /documents` 增加 `sort` | DONE | [crud.py:53](../../server/app/routers/documents/crud.py#L53)：7 键白名单、未知 400，契约对齐见 REWORK §7.6 |
| B3-02 | `GET /documents/facets` 聚合 | DONE | [facets.py](../../server/app/routers/documents/facets.py)；原崩溃 **RD-01 已由 RW-101 修复**（REWORK §7.1） |
| B3-03 | 书架「全选筛选结果」接入 `/documents/ids` | PARTIAL | 后端已就绪：[documents/ids.py](../../server/app/routers/documents/ids.py)（全筛选 + 5000/`truncated`）；书架「全选筛选结果」UI 未接（RD-B 最小版未含 `useLibrarySelection`），留待后续批次 |
| B3-04 | 书架相关索引迁移 | DONE | `0009_library_indexes`；partial index 问题见 **RD-14** |
| B3-05 | `useLibraryFilters`：筛选状态 URL 化 | DONE | [libraryFilters.ts](../../src/lib/libraryFilters.ts#L80) 解析/序列化 + [useLibraryFilters.ts](../../src/lib/useLibraryFilters.ts#L37)（`replace` + 300ms 防抖 + localStorage 视图偏好）；深链实测见 REWORK §7.9 |
| B3-06 | 分类侧栏：搜索/折叠/隐藏 0 本/置顶/重命名 | PARTIAL | RW-504 只补了 facets 侧栏（格式/语言/标签计数与选中态）；分类搜索/折叠/隐藏 0 本/置顶/重命名均未做，留给后续迭代（RD-B 最小版范围，REWORK §7.9） |
| B3-07 | 工具栏：排序、活跃筛选 chips、清除全部 | DONE | [library/page.tsx:868](../../src/app/library/page.tsx#L868) 排序、[:880](../../src/app/library/page.tsx#L880) 网格/列表、[:904](../../src/app/library/page.tsx#L904) chips + 清除全部（保留排序/视图偏好）；REWORK §7.9 |
| B3-08 | `BookCard` + `BookCover`（独立封面加载） | PARTIAL | **封面部分已完成（RW-503）**：[BookCover.tsx](../../src/components/BookCover.tsx) + [coverCache.ts](../../src/lib/coverCache.ts)（LRU 200/引用计数/inflight 去重），页面级 `coverUrls` 已删（REWORK §7.8）；按 RD-B 不拆 `BookCard`；无 60s 负缓存（F-11） |
| B3-09 | 无限滚动 + 虚拟化 + 预取 | PARTIAL | 无限滚动已有；虚拟化仅笔记页（`@tanstack/react-virtual`），书架未接；按 RD-B 最小版不做书架全量虚拟化（REWORK §7.9） |
| B3-10 | 骨架屏/空态/错误态 | DONE | [library/page.tsx:818](../../src/app/library/page.tsx#L818) 骨架；[:847-861](../../src/app/library/page.tsx#L847-L861) 空态 |
| B3-11 | 移动端抽屉/导航、暗色、无障碍 | PARTIAL | **导航入口与窄屏图标态已由 RW-501 补齐**（`aria-label/title`，REWORK §7.8）；抽屉、暗色范围（D5）仍缺 |
| B3-12 | 组件测试、Playwright、性能记录、B3 报告 | PARTIAL | `e2e/smoke.spec.ts`、`features.spec.ts` 存在；R5 新增 Nav/coverCache/datetime/queueReviewApi/dataSourceAutoAdd 单测；RW-601 再补 [useLibraryFilters.test.tsx](../../src/lib/__tests__/useLibraryFilters.test.tsx) 7 例 + 队列页组件 4 例；仍无 BookCard/书架组件测试与 B3 报告 |

## B4 · 阅读队列 + 间隔复习 — PARTIAL（6/9）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B4-01 | 迁移 `0010_reading_queue_reviews` + 模型 + 回填 | DONE | 迁移头 `0010`；[models.py:206-239](../../server/app/models.py#L206-L239) |
| B4-02 | `review_scheduler.py` SM-2 lite 纯函数 | DONE | [review_scheduler.py](../../server/app/services/review_scheduler.py)；`now` 已改为调用方注入（RW-602），9 例覆盖四档间隔与预览文案 |
| B4-03 | 阅读队列 API（增删改/重排/幂等） | DONE | [review.py:180-379](../../server/app/routers/review.py#L180-L379)：`/reading-queue`、auto-add/next、`ordered_ids`（REWORK §7.6） |
| B4-04 | 复习 API（due/rating/snooze/stats） | DONE | [review.py:384-607](../../server/app/routers/review.py#L384-L607)：四档评分、highlight/note 过滤、`preview`、5 字段 stats（REWORK §7.6） |
| B4-05 | 标注创建时建 review 行 | DONE | [review_service.py:19](../../server/app/services/review_service.py#L19) `ensure_review_row`（highlight/note、存活文档、幂等）+ [annotations.py:104](../../server/app/routers/annotations.py#L104) 创建后挂钩，失败只回滚复习行；后端 9 例（REWORK §7.9） |
| B4-06 | 队列页/回顾页 Tab + 队列排序 UI | PARTIAL | [queue/page.tsx:60](../../src/app/queue/page.tsx#L60) 两 Tab + 上移/下移/状态动作已完成（同优先级内，F-12）；拖拽排序未做 |
| B4-07 | 复习卡评分、快捷键、间隔预览 | DONE | [queue/page.tsx:464](../../src/app/queue/page.tsx#L464) ReviewTab：两步揭示、四档评分用服务端 `preview`、1–4/S/←/→/Esc、乐观隐藏与回滚（REWORK §7.9） |
| B4-08 | 时区工具、修「仅今天」、每日笔记接入 | PARTIAL | **前两项已完成（RW-502）**：[datetime.ts](../../src/lib/datetime.ts) + `since` 服务端过滤（REWORK §7.8）；`buildDailyNoteContent` 仍只有两参，「今日复习」段未接入（B4-08 剩余项，未纳入返工清单） |
| B4-09 | 调度/API/前端测试、B4 报告 | PARTIAL | 后端 `test_review_fixes.py`、`test_annotation_review_hook.py`、RW-601 新增 `test_review_scheduler.py` 9 例 + `test_reading_queue_api.py` 7 例 + `test_reviews_api.py` 5 例；前端 [queueReviewApi.test.ts](../../src/lib/__tests__/queueReviewApi.test.ts) 13 例 + [dataSourceAutoAdd.test.ts](../../src/lib/__tests__/dataSourceAutoAdd.test.ts) 6 例 + [队列页组件测试](../../src/app/queue/__tests__/page.test.tsx) 4 例；B4 报告仍缺 |

## B5 · 语义检索 / RAG — **NO-GO**（RW-701 重评已签核收口，2026-10-27；B5-01/02 完成，B5-03~B5-12 SKIPPED）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B5-01 | 技术 spike：模型/向量存储/真实库基准 | **DONE**（2026-10-27 重评） | [spikes/B5-01-embedding-spike.md](spikes/B5-01-embedding-spike.md) 六问齐备 + [server/scripts/spike_semantic.py](../../server/scripts/spike_semantic.py) + [spikes/B5-01-judgments.json](spikes/B5-01-judgments.json)。门槛对照：相关性 PASS（bge 12/15、multi 14/15，hybrid 15/15）、体积 PASS（wheel ≈161 MB / 权重 90.8–240.5 MB）、CI PASS（pytest 248 passed 未装 fastembed）、隐私 PASS（离线，SHA-256 已录）；**索引耗时 FAIL**（全库外推 bge 2 h 15 min、multi 1 h 24 min，线 20 min）、**hybrid P95 FAIL**（11/15、10/15 超 800 ms；语义单独 ≤8.8 ms 达标）、**常驻内存 FAIL**（外推 ≈661 / ≈824 MB，线 500 MB）。旧页 [spikes/B5-02-gonogo.md](spikes/B5-02-gonogo.md) 已标作废 |
| B5-02 | 写 go/no-go 结论 | **DONE** | [spikes/B5-02-decision.md](spikes/B5-02-decision.md)：四问逐条落笔（§5 签核记录，2026-10-27）→ **NO-GO**。用户回复原文「你决定」，执行方按第 3 节「任一项不达标即 no-go」定案，未放宽任何门槛；B5-03 起仍**不得开工**（除非用户书面改写三条成本门槛） |
| B5-03 | 仅 go 后：新增依赖与 feature flags | SKIPPED（B5-02 判 NO-GO） | 未新增任何依赖；`server/.venv` 保持干净，spike 依赖只存在于 `.spike-venv/`；`server/requirements-semantic.txt` **不创建**（决策 3 不批准） |
| B5-04 | 迁移 `0011_semantic_search` + 模型 | SKIPPED（B5-02 判 NO-GO） | `alembic` 仍无 `0011`，真实库零改动 |
| B5-05 | 嵌入/向量索引抽象 | SKIPPED（NO-GO） | 同上 |
| B5-06 | 增量索引后台任务 | SKIPPED（NO-GO） | 同上；实测索引耗时是主要 FAIL 项（全库 ≥153,673 chunks，网格最优仍 147 min），若将来改判 go 需先定批次预算 |
| B5-07 | 语义/混合检索 API（RRF） | SKIPPED（NO-GO） | 同上；RRF 仅在 spike 内实现（等深 top-5 融合） |
| B5-08 | 搜索页模式切换 UI | SKIPPED（NO-GO） | 同上 |
| B5-09 | 设置页语义索引卡片 | SKIPPED（NO-GO） | 同上 |
| B5-10 | 证据式回答（无生成模型） | SKIPPED（NO-GO） | 同上 |
| B5-11 | 生成式 RAG（可选开关） | SKIPPED（NO-GO） | 决策 4 选 A 不做；代码里不存在 `KB_RAG_ALLOW_REMOTE`，无任何远程 LLM 出站路径 |
| B5-12 | fake provider 测试、隐私检查、B5 报告 | DONE（按 NO-GO 口径） | 隐私检查已实测（`HF_HUB_OFFLINE=1`、两个 onnx 的 SHA-256 与下载时间记录在报告 §3）；B5 报告即 [spikes/B5-01-embedding-spike.md](spikes/B5-01-embedding-spike.md) + 决策页；fake provider 测试属 B5-03+ 实现范畴，随 NO-GO 一并取消 |

## B6 · 收口与发布 — PARTIAL（3/5）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| B6-01 | 全量回归（pytest/typecheck/lint/vitest/build/e2e/audit） | DONE | R5 收尾后：后端 **204 passed**、`npm run typecheck` 无错误、`npm test` **143 passed (22 files)**、`npm run build` 成功（REWORK §7.9） |
| B6-02 | 移动端/暗色/无障碍/性能复核 | PARTIAL | 未做全量复核；R5 只覆盖队列/书架键盘与 `aria` 的可访问名，移动端抽屉、暗色范围（D5）与性能记录仍缺 |
| B6-03 | 数据清理（逐次授权） | DONE | [reports/B6-cleanup-2026-10-27.md](reports/B6-cleanup-2026-10-27.md)：4 条 operation log + 备份 `20261027_004916` |
| B6-04 | 文档、发布说明、回滚说明 | DONE | [reports/B2-B6-2026-10-26.md](reports/B2-B6-2026-10-26.md) + 清理报告回滚节；README 版本错误另记 **RD-20**。**R7 追加硬要求**：`docs/roadmap/RELEASE-2026-10.md` 至今并未存在，将来生成时必须写一条「已评估语义检索，因全库索引耗时（2 h 15 min vs 20 min）、常驻内存（≈661 MB vs 500 MB）、hybrid P95（11/15 超 800 ms）三条门槛不达标而不实现」，实测出处指向 [spikes/B5-01-embedding-spike.md](spikes/B5-01-embedding-spike.md)，不留 TODO 悬空（B6-04 原文第 6 条的要求此前无处落地） |
| B6-05 | 最终报告与 CI 确认 | PARTIAL | 报告已有；执行轮 6 个提交仍未推送，CI 未确认；**RD-E / G4** |

---

## 阻塞/授权

| 日期 | 批次/任务 | 类型 | 说明 | 需要谁 | 状态 |
|---|---|---|---|---|---|
| 2026-10-26 | 全部 | 启动 | 等待用户启动执行轮 | 用户 | CLOSED（B0–B6 已执行） |
| 2026-10-26 | B1/B3/B4/B5 | 迁移 (G3) | `0007`/`0009`/`0010` 已应用；`0011` 因 no-go 未建 | 用户 | CLOSED |
| 2026-10-26 | B2/B6 | 真实数据 (G1) | B6-cleanup 已按逐项授权执行（4 条日志）；后续每次写入仍需新授权 | 用户 | CLOSED（本轮）；未来需重新授权 |
| 2026-10-27 | B5-02 | 决策 (D2/D3/RD-D) | spike 实测：索引耗时 2 h 15 min vs 20 min、内存外推 ≈661 MB vs 500 MB、hybrid P95 11/15 超 800 ms → 三条 FAIL。用户回复「你决定」，执行方按第 3 节原文规则定 **NO-GO**，四问记录见 [spikes/B5-02-decision.md](spikes/B5-02-decision.md) §5 | 用户→执行方 | **CLOSED（NO-GO）**；改判 go 需用户书面改写三条成本门槛 |
| 2026-10-27 | B5-03 | 依赖 (G2) | `fastembed==0.9.1` + `onnxruntime==1.31.1`（31 包 / wheel ≈161 MB，余量仅 39 MB）——**不批准**：不写 `requirements-semantic.txt`、不进 CI/镜像；hnswlib 实测不需要 | 用户 | CLOSED（拒绝，功能不落地故无必要） |
| 2026-10-27 | B5 收尾 | 磁盘 | `.spike-venv/`（175 MB）+ `server/scripts/spike-out/`（≈1.66 GB，含 1.33 GB 库副本与 332 MB 权重）是否删除 | 用户 | **OPEN（未确认前不删）**；永久证据已入库，删除不影响可复核性 |
| 2026-10-27 | 建议 R8 | 新批次 | 修 `/api/search` 的中文关键词通道（F-15）：2 字词必然退化为四列全表 `LIKE`（694–1,312 ms）且 FTS 对 2 字词静默返回 0 行。R7 实测证明这才是用户「搜不到/搜得慢」的根因，属 B5 之外的独立缺陷，故本批只登记不动代码 | 用户 | **OPEN（等指令才开工）**；无新依赖、无迁移，预计只碰 `search.py` + 测试 |
| 2026-10-26 | B2/B6 | 技术 | 扫描版 PDF 是否装 tesseract（D4） | 用户 | OPEN（维持不装） |
| 2026-10-26 | B3 | 范围 | 暗色模式覆盖范围（D5） | 用户 | OPEN |
| 2026-10-26 | 全部 | 推送 (G4) | 每批单独提交；推送需逐次确认 | 用户 | OPEN |

## 发现（不在本批顺手修）

| 日期 | 位置 | 现象 | 影响 | 建议批次 | 状态 |
|---|---|---|---|---|---|
| 2026-10-26 | [server/app/models.py:28](../../server/app/models.py#L28) | `utcnow()` 返回 tz-aware，SQLite 落库为 naive UTC 文本 | 直接比较会 TypeError 或静默错判 | B4-01/B4-02 的 `as_utc_naive`/`from_utc_naive` | FIXED |
| 2026-10-26 | [src/lib/dataSource.ts:362](../../src/lib/dataSource.ts#L362) | 本地 `shelf=recent` 先 `slice` 再过滤 | 「最近添加」莫名少于 20 本 | **RD-12 → RW-502** | FIXED（REWORK §7.8） |
| 2026-10-26 | [server/app/routers/documents/crud.py:80](../../server/app/routers/documents/crud.py#L80) | `created_at` 无稳定 tiebreaker | 分页重复/漏项 | **RD-10 → RW-304** | OPEN |
| 2026-10-26 | [src/app/review/page.tsx:45](../../src/app/review/page.tsx#L45) | 用 UTC 日期判断「今天」 | 东八区 0–8 点显示错误 | **RD-12 → RW-502** | FIXED（REWORK §7.8） |
| 2026-10-26 | [.gitignore](../../.gitignore) | `docs/*` 不跨 `/` 导致路线图被忽略 | 换机器丢进度 | B0-01 | FIXED（已提交） |
| 2026-10-26 | [README.md:3](../../README.md#L3) | 写「Next.js 14」，实际 `next@^15.5.28` | 文档误导 | **RD-20** | OPEN |
| 2026-10-26 | [src/lib/__tests__/quality.test.ts](../../src/lib/__tests__/quality.test.ts) | 文件名与内容不符（测 epub/foliate/fuzzy/fts） | 覆盖情况误判 | 后续质量测试另建文件 | OPEN |
| 2026-10-26 | 仓库根目录 | `dev-server.log`、`library_e2e*.log` 等运行产物 | 噪音/磁盘占用 | 清理需单独授权 | OPEN |
| 2026-10-27 | 清理脚本 | 真实数据变更由未入库代码完成，无法复现 | 证据链断裂 | **RD-04 → RW-201** | FIXED（`server/scripts/reextract_content.py`、`auto_classify.py`，见 REWORK §7.4） |
| 2026-10-27 | [progress.md](progress.md) | 70 行任务表被批次汇总覆盖 | 无法回答「还差哪些任务」 | **RD-19 → RW-202** | FIXED（本文件） |
| 2026-10-27 | `documents.content` | 1 本 PDF 的 content 是 UTF-16LE 残留（首字符 NUL），SQLite 长度 0、Python 视为非空 | 质量口径与 `reindex_job` 差 1 本 | REWORK F-8 | 已在 reextract 脚本内按质量口径处理；`reindex_job` 口径未纳入 R6，仍待排期 |
| 2026-10-27 | [server/app/routers/search.py:192-197](../../server/app/routers/search.py#L192-L197) | 查询含任一 <3 字词时整条退化为 `_search_ilike` 四列全表扫描；实测关键词通道 694–1,312 ms，而 2 字词强制走 FTS 会静默返回 0 行 | 中文搜索又慢又漏；B5 hybrid 延迟 FAIL 的唯一成因 | **REWORK F-15** | OPEN → **建议作为 R8 主任务**（B5 已 NO-GO，但这条与嵌入无关，是现有 `/api/search` 自身的缺陷：慢 + 静默漏配；R7 只登记未动代码） |
| 2026-10-27 | [server/app/services/content_extract.py:21](../../server/app/services/content_extract.py#L21) + 切片 `MAX_CHUNKS=200` | 入库正文上限 200,000 字符，切片只覆盖前 ≈136,120 字符 | 全库 633/1,060 本（59.7%）尾部 ≈32% 内容永远进不了任何索引 | **REWORK F-16** | OPEN（B5 判 NO-GO 后，`MAX_CHUNKS=200` 那一半不再有消费方；`MAX_CHARS=200_000` 的入库截断仍是既成事实——633/1,060 本长书的尾部 ≈32% 内容既进不了语义索引，也没有别的读取方，除非将来做全文/摘要。降低优先，不排期；若重开 B5 需与门槛改写一并决定） |

## 批次切换记录

| 日期 | 从 | 到 | 确认人 | 备注 |
|---|---|---|---|---|
| 2026-10-27 | B0–B6 执行轮 | 返工 R1 | 用户（「生成完整书面返工清单再分批修复」） | 复核发现 3 个阻断缺陷；清单见 REWORK |
| 2026-10-27 | R1 | R2 | 用户（「你决定」） | R1 提交 `0d3217d`；R2 = RW-201/202 |
| 2026-10-27 | R2 | R3 | 用户（按已批准清单继续） | R2 提交 `687799a`；R3 = RW-301–304 |
| 2026-10-27 | R3 | R4 | 用户（「真实现」：RD-A 选 A） | R3 提交 `0bf75bc`；R4 = RW-401/402 |
| 2026-10-27 | R4 | R5（最小集） | 用户（「继续」，按 REWORK 默认建议） | R4 提交 `a8cffbf`；R5 = RW-501/502/503；RW-504/505 等 RD-B |
| 2026-10-27 | R5（最小集） | R5 收尾（RW-504/505） | 用户（「补 504/505 最小版（推荐）」） | RD-B 定为最小版；RW-504/505 已完成并提交 `65d98d5`（G4 前不推送） |
| 2026-10-27 | R6 | R7（RW-701 B5 重评） | 用户（「继续」，RD-D 默认「重评」） | R6 提交 `fef49c7` + 文档标记 `da2d5d3`；R7 只跑 spike（隔离 venv + 库副本只读），**产品代码零改动** |
| 2026-10-27 | R7（签核） | 下一批（未定） | 用户（「你决定」→ 四问由执行方按第 3 节原文规则定） | **B5 判 NO-GO**，B5-03~B5-12 保持 SKIPPED；依赖 G2 拒绝、生成式 RAG 不做；R7 已提交 `692ff1b`（G4 前不推送）。待用户指令的两件事：是否开工 **R8＝修 F-15 中文关键词通道**、是否删除 spike 盘（≈1.84 GB） |

## 任务书偏离记录

| 日期 | 任务 ID | 原计划 | 实际做法 | 原因 | 用户是否知情 |
|---|---|---|---|---|---|
| 2026-10-26 | B3-06~B3-12 | 7 个前端任务逐条执行 | 合并为「部分」一行 | 执行轮收口 | 否（复核发现，RW-202 已恢复逐行） |
| 2026-10-26 | B2-07 | reextract 真提取 + 状态接口 | 只写日志的空壳端点 | 执行轮简化 | 否（RD-05；RW-401 已于 R4 真实现） |
| 2026-10-27 | 数据清理 | 入库脚本可复现 | 一次性未入库代码 | 赶进度 | 否（RD-04；RW-201 已补） |
| 2026-10-27 | B3-06/B3-08/B3-09 | 分类侧栏增强、BookCard 拆分、书架虚拟化 | 只做 facets 侧栏 + 独立 BookCover；不拆 BookCard、不接虚拟化 | RD-B 选「补 504/505 最小版」 | 是（用户选择） |
| 2026-10-27 | B4-06 | 队列排序（含拖拽） | 上移/下移 + 状态动作；拖拽未做 | RD-B 最小版 | 是（用户选择） |
| 2026-10-27 | B5-02 | 验收条款「结论页有用户明确回复记录（时间 + 选择）」 | 用户回复原文只有「你决定」，未逐条选；四问由执行方按任务书第 3 节原文规则定并把依据写进 §5 | 用户把选择权整体交出。执行方因此**只做保守方向的选择**（不放宽门槛、不批准新依赖、不接远程 LLM），并把每项的可推翻条件写明，用户随时可改判 | 是（用户指令「你决定」，选择结果已回写 [spikes/B5-02-decision.md](spikes/B5-02-decision.md) §5 等其确认） |
| 2026-10-27 | B5-01 | 候选模型 `intfloat/multilingual-e5-small` | 第二候选换成 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | fastembed 0.9.1 的 41 个受支持模型里没有 e5-small，e5 只有 large（2.24 GB，单权重即超第 3 节 500 MB 线） | 否（需用户在决策 2 确认换候选） |
| 2026-10-27 | B5-01 | 切片样本 20 本 | 50 本（epub 28 / pdf 16 / txt 6） | 增强代表性；未降低任何门槛 | 否（无害偏离，报告 §2 已记） |
| 2026-10-27 | B5-01 | 第 5 项「暴力检索 >800 ms 才测 hnswlib」 | 未安装 hnswlib | 实测语义暴力点积全库外推 53–70 ms，触发条件不成立；hybrid 超标另有成因（F-15） | 否 |
| 2026-10-27 | B5-01 | CI 门槛「pytest / build / docker 全绿」 | 本轮只重跑 pytest（248 passed, 2 warnings, 21.32s） | spike 零产品代码改动，`npm run build`/`docker build` 沿用 R6（`fef49c7`）的绿状态；已在报告 §9 注明未重跑 | 否 |
