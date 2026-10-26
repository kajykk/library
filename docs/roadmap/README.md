# 实施路线图 · 2026-10-26

> 这是下一轮执行的唯一入口。执行方先读 [00-execution-rules.md](00-execution-rules.md)，再按 **B0 → B1 → B2 → B3 → B4 → B5 → B6** 顺序执行。
>
> **进度唯一来源是 [progress.md](progress.md)**。每完成一个任务 ID，就更新该文件并填写证据；不要只在聊天里说"做完了"。

## 0. 文档状态与开工方式

| 项目 | 状态 |
|---|---|
| 任务书 B0–B6 | 已写完（7 份，共 70 个任务，每个任务都带文件清单、契约、测试与验收） |
| 契约文档 | [api-contracts.md](api-contracts.md)、[data-model.md](data-model.md) 已与任务书对齐 |
| 代码改动 | **无**。截至本文档写入时，路线图相关的功能代码一行都没动 |
| 未提交改动 | `next.config.mjs`（dev-only CSP 修复）；`.gitignore`（新增 `!docs/roadmap/`，否则本文档集无法入库）；`docs/roadmap/`（12 份 Markdown，未跟踪）→ 全部由 B0-01 分两个提交处理 |
| 开工方式 | 用户说「开始 B0」后，执行方把 [progress.md](progress.md) 的「当前批次」改为 B0/B0-01 并置 `WIP` |

执行方每轮开始时只需要做三件事：

1. 打开 [progress.md](progress.md)，从上往下找**第一个非 `DONE` 的任务 ID**——那就是当前任务；表里的 `TODO`/`WIP`/`BLOCKED`/`SKIPPED` 就是未完成清单。
2. 读该任务所在批次任务书的对应小节：里面写了要改/新增哪些文件、函数签名、测试点与验收命令，不需要再自行设计。
3. 检查该批次的「前置条件」小节是否需要授权（G1–G5）。缺授权就停下登记，然后跳到不依赖授权的任务。

每个任务完成后必须同时更新两处：任务书里承诺的证据写进 [progress.md](progress.md)，批次结束补一份 `docs/roadmap/reports/B<批次>-<日期>.md`。`docs/roadmap/reports/` 与 `docs/roadmap/spikes/` 目前不存在，由 B0-08 与 B5-01 首次创建。

## 1. 目标

把「功能增强 + 界面优化」全部落地，但不一次性铺开：

1. 先让「当前连的是哪份数据、库里缺什么」可见（B1）。
2. 再做入库治理：目录扫描导入、批量分类、补封面、重提取（B2）。
3. 再把书架从「能翻」做到「好找、好看、不卡」（B3）。
4. 然后闭合「阅读队列 → 标注 → 间隔复习」（B4）。
5. 最后做语义检索；先技术验证，通过 gate 才继续（B5）。
6. 全量回归、数据清理、文档与发布（B6）。

## 2. 当前基线（2026-10-26 只读统计）

| 项目 | 现状 |
|---|---|
| 存活文档 | 1298（book 933 / pdf 364 / note 1），书架显示 1297 |
| 软删除 | 59 |
| 数据库 | 765,173,760 B；WAL 约 10,238,232 B |
| 正文为空 | 581 本；正文 <500 字 602 本 |
| 无封面 | 515 本 |
| 未分类 | 32 本 |
| 重复 SHA 组 | 3 组（P6-6 留下的可疑组，未动） |
| 语言变体 | 仍有 en-US 17 / eng 7 / EN-US 2 / zh-TW 1 等 |
| 已知问题 PDF | 《历史与自由意识》0 字（5 秒预算不足）；《读人像法官一样识人断事》412 字（扫描件） |
| 已知坏源文件 | Downloads 中 19 个（5 个 0 字节 + 14 个 143 字节错误页），未入库 |
| 未提交改动 | `next.config.mjs` 的 dev-only CSP 修复 |
| 当前分支/HEAD | `main` / `d8dc64d`（与 origin/main 一致） |

这些基线用于每批结束后的对比；不要在没有记录的情况下改变它们。

## 3. 状态总览

> 初始状态全部为 `TODO`。执行方每批结束时更新此表，并在 [progress.md](progress.md) 写详细证据。

| 批次 | 主题 | 任务书 | 状态 | 任务数 | 已完成 |
|---|---|---|---|---|---|
| B0 | 基线与护栏 | [B0-foundation.md](B0-foundation.md) | DONE | 8 | 8 |
| B1 | 数据源可见化 + 质量中心（只读） | [B1-data-source-quality.md](B1-data-source-quality.md) | DONE | 11 | 11 |
| B2 | 目录扫描导入 + 批量治理 | [B2-import-batch.md](B2-import-batch.md) | DONE | 13 | 13 |
| B3 | 书架信息架构与性能 | [B3-library-ui.md](B3-library-ui.md) | DONE | 12 | 8 |
| B4 | 阅读队列 + 间隔复习 | [B4-reading-review.md](B4-reading-review.md) | DONE | 9 | 5 |
| B5 | 语义检索 / RAG | [B5-semantic-search.md](B5-semantic-search.md) | NO-GO | 12 | 2 |
| B6 | 收口与发布 | [B6-closeout.md](B6-closeout.md) | DONE | 5 | 5 |

状态含义：

- `TODO`：未开始。
- `WIP`：正在做；同一时间只允许一个批次处于 WIP。
- `BLOCKED`：等待用户授权、依赖或决策；必须在 progress.md 写清阻塞原因。
- `DONE`：任务书中的验收清单全部通过，且有证据。
- `SKIPPED`：用户明确同意不做；必须写原因。

## 4. 执行顺序与门禁

1. **B0 必须先做**：提交 CSP 修复、备份基线、修复数据源模式缓存；新增 `JobState` 与 `StatePanel` 基座。
2. **B1 完成前不改任何真实数据**；质量中心只读，但 `0007_document_quality` 迁移本身需要 G3。
3. **B2 的每个真实写入动作单独授权**：导入、软删除、批量改、补封面、重提取、隔离孤儿文件。
4. **B3 不动业务数据**；只加索引（G3）、只读接口与前端重构。
5. **B4 新增两张表**（`0010`，G3 + 备份）；回填只在迁移内做结构化打散，不调算法。
6. **B5 先做 spike**；go/no-go 只看 [B5-semantic-search.md](B5-semantic-search.md) 第 3 节的硬性门槛，任一项不达标就是 no-go，且 no-go 是合法交付。
7. **B6 的数据清理动作**同样逐次授权；清理脚本已存在，本批只负责按 dry-run 结果执行。

迁移必须严格按链顺序执行，一步一批：`0007`（B1）→ `0008`（B2）→ `0009`（B3）→ `0010`（B4）→ `0011`（B5，仅 go）。任何批次开始前先 `alembic current` 确认 head 与预期一致。

## 5. 待确认决策（阻塞项）

| ID | 决策 | 影响批次 | 默认建议 | 状态 |
|---|---|---|---|---|
| D1 | 是否允许 B2 在真实库执行导入/软删除/重提取 | B2 | 每一步单独预览 + 授权 | OPEN |
| D2 | 是否允许 B5 新增本地嵌入依赖（fastembed / onnxruntime 等） | B5 | **先 spike（不需要授权，装在隔离 venv）**，按 B5-02 结论再单独批准 | OPEN |
| D3 | B5 语义检索 go/no-go 门槛 | B5 | 门槛已写在 B5 任务书第 3 节（相关性 / 体积 / 耗时 / 内存 / 离线 / CI 六项），执行方只报告实测值，不自行放宽 | OPEN |
| D4 | 扫描版 PDF 是否安装 OCR（tesseract） | B2/B6 | 先标注"需 OCR"，不自动安装 | OPEN |
| D5 | 暗色模式覆盖范围 | B3 | 书架/质量/导入/回顾/设置优先；reader/notes/graph 最小适配 | OPEN |
| D6 | 每批完成后是否直接提交/推送 | 全部 | 每批停下验收；单独提交；推送需确认（G4） | OPEN |
| D7 | B3 的分面/排序是否允许为 `facets` 新增只读端点 | B3 | 允许（只读、不改现有端点），已在 [api-contracts.md](api-contracts.md) 第 2 节定契约 | OPEN |

执行方遇到未决事项时：**不要自行替用户做决定**，在 [progress.md](progress.md) 的「阻塞/授权」区登记，并继续做不依赖该决策的任务。

## 6. 文档索引

| 文件 | 用途 |
|---|---|
| [00-execution-rules.md](00-execution-rules.md) | 硬约束、授权门禁、备份/回滚、验证命令、证据模板 |
| [progress.md](progress.md) | 任务级进度、证据、阻塞项（唯一进度来源） |
| [api-contracts.md](api-contracts.md) | 新增 API 的请求/响应契约 |
| [data-model.md](data-model.md) | 迁移 0007–0011、表结构、回填与回滚 |
| [B0-foundation.md](B0-foundation.md) | 批次 0 任务书 |
| [B1-data-source-quality.md](B1-data-source-quality.md) | 批次 1 任务书 |
| [B2-import-batch.md](B2-import-batch.md) | 批次 2 任务书 |
| [B3-library-ui.md](B3-library-ui.md) | 批次 3 任务书 |
| [B4-reading-review.md](B4-reading-review.md) | 批次 4 任务书 |
| [B5-semantic-search.md](B5-semantic-search.md) | 批次 5 任务书（含 go/no-go 硬门槛） |
| [B6-closeout.md](B6-closeout.md) | 批次 6 任务书 |
| `spikes/`（待创建） | B5-01 spike 报告与 B5-02 go/no-go 决策页 |
| `reports/`（待创建） | 每批完成报告，模板见 [00-execution-rules.md](00-execution-rules.md) 第 6 节 |

## 7. 每批完成定义

一批只有同时满足以下条件，才能从 `WIP` 改为 `DONE`：

- 任务书中所有 `MUST` 任务完成。
- 新增/修改的测试通过。
- `npm run typecheck`、`npm run lint`、`npm test`、`npm run build` 通过。
- `python -m pytest tests -q` 通过。
- 涉及页面时，已在浏览器中走通黄金路径（桌面 + 移动端）。
- 数据相关批次：dry-run 结果、备份路径、执行前后计数、回滚方法都已写入报告。
- [progress.md](progress.md) 已更新，证据可复现。
- 没有把无关改动混进本批提交。
