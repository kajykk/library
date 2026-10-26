# B6 · 收口与发布

| 项目 | 值 |
|---|---|
| 批次 | B6 |
| 状态 | TODO |
| 依赖 | B0–B5 全部 DONE 或明确 SKIPPED；G4 推送授权；数据清理需逐次 G1 |
| 真实数据写入 | 仅在用户逐项授权时（去重软删除、语言归一化、孤儿隔离、WAL checkpoint） |
| 迁移 | 无新增；本批**验证迁移链完整**（head = `0011_semantic_search`，no-go 时为 `0010`） |
| 完成标志 | B6-01 ~ B6-05 全部 DONE，最终报告已写，CI tip run 绿 |

## 1. 目标

把前六批的成果变成**可交付的一次版本**：全量回归无红项、真实数据状态有明确结论、文档与回滚路径写清、推送并通过 CI。本批不新增功能。

## 2. 不做什么

- 不加任何新功能、不做「顺手优化」；发现问题就登记，不动手。
- 不硬删除 `documents` 行，不删除 `server/data/` 下的文件。
- 不升级无关依赖；不重跑 `npm install` 之外的包管理动作。
- 不用 npm 11 生成 lockfile。
- 不合并/重写历史（禁止 `reset --hard`、`rebase -i`、`--amend`）。

## 3. 前置条件

1. [progress.md](progress.md) 中 B0–B5 每批状态为 `DONE` 或有用户确认的 `SKIPPED`。
2. 所有批次报告已存在于 `docs/roadmap/reports/`。
3. 每批遗留的「发现」区条目已逐条判定：**归入下一轮 / 就地修（需授权）/ 明确接受**，并在本批报告中留痕。
4. 磁盘有足够空间放最后一次备份（当前库 765 MB）。

## 4. 任务

### B6-01 全量回归

**命令（全部实际执行，逐条记录输出摘要）**：

```bash
# 后端
cd server
E:/zy/python/python.exe -m pytest tests -q
E:/zy/python/python.exe -m alembic heads
E:/zy/python/python.exe -m alembic current

# 前端
npm run typecheck
npm run lint
npm test
npm run build

# e2e（需先 build，config 用 standalone server.js）
npm run test:e2e

# 安全
npm audit --omit=dev --registry=https://registry.npmjs.org
cd server && pip-audit --desc
```

**必须确认的点**：

1. `alembic heads` **只有一个 head**。若出现多个 head，说明某批迁移分支写错 `down_revision`，属阻断项：登记 BLOCKED，不自行改写历史迁移，先向用户报告。
2. `alembic current` 等于 [data-model.md](data-model.md) 声明的最终 head；B5 no-go 时 head 应为 `0010_reading_queue_reviews`，并在报告写明。
3. 降级演练在**临时副本**上做，不碰真实库：

   ```bash
   cp server/knowledge_base.db /tmp/kb_downgrade_probe.db   # 或 Windows 下 copy 到临时目录
   # 在副本上
   KB_DATABASE_URL=sqlite:///<副本路径> E:/zy/python/python.exe -m alembic downgrade 0006_add_sha256
   KB_DATABASE_URL=sqlite:///<副本路径> E:/zy/python/python.exe -m alembic upgrade head
   ```
   验证「0007→0011 逐级 downgrade/upgrade 不报错」，真实库只读校验 `pragma integrity_check`。副本用完删除。
4. 备份可恢复：对最近一次备份跑
   ```bash
   E:/zy/python/python.exe server/scripts/incremental_backup.py --verify <备份目录>
   E:/zy/python/python.exe server/scripts/restore_drill.py <备份目录> --verify
   ```
5. 敏感信息：跑一次 gitleaks（按 P5-2 的可运行化方式），确认新代码未落 token/绝对路径/用户藏书正文样本。
6. `git status --short` 干净或仅剩**已确认**的本批改动。仓库根目录存在历史遗留的临时文件（`dev-server.log`、`library_e2e*.log`、`page_probe.html`、`console/`、`generated-images/`、`test-results/`、`playwright-report/`、`coverage/`）：
   - 先 `git ls-files <path>` 判断是否被跟踪；
   - **未被跟踪**：只登记，不删除（可能是用户手工产物）；
   - **被跟踪**：登记到「发现」区，建议单开一批清理，不在本批混提交。

**产物**：`docs/roadmap/reports/B6-01-regression.md`（命令 + 结果 + 与上一批的差异说明）。

**验收**：以上命令全部有实测输出且通过（或明确标注豁免与授权来源）；alembic 单 head；备份可校验；无新增泄密。

---

### B6-02 移动端 / 暗色 / 无障碍 / 性能复核

> 这一项是**人工走查**，不是跑测试。测试能证明代码没崩，证明不了「好用」。

**走查矩阵**（每一项填「通过 / 问题 / 截图路径」）：

| 页面 | 390×844 | 768×1024 | 1440×900 | 暗色 | 键盘全流程 | 读屏可达 |
|---|---|---|---|---|---|---|
| `/library` | | | | | | |
| `/quality` | | | | | | |
| `/import` | | | | | | |
| `/reader/{id}` | | | | | | |
| `/notes` | | | | | | |
| `/graph` | | | | | | |
| `/search` | | | | | | |
| `/review?tab=queue` | | | | | | |
| `/settings` | | | | | | |

**检查要点**：

1. 无横向滚动条、文字不溢出、点按目标 ≥40×40 px。
2. 暗色下无「白底白字」：重点看新组件（骨架、chips、卡片占位、抽屉遮罩）。
3. 键盘：Tab 顺序符合视觉顺序；抽屉/弹窗可 `Esc` 关闭且焦点归还；复习快捷键不劫持输入框。
4. 读屏：状态条 `role=status`、错误 `role=alert`、结果计数 `aria-live`、图标按钮都有 `aria-label`。
5. 数据源降级路径：清 localStorage → 引导 + 状态条；填错 token → degraded；停后端 → 书架仍能用本地数据。

**性能复核**（真实库，只读，与 B3-12/B5-12 同口径）：

| 指标 | 目标 | 实测 |
|---|---|---|
| `/library` 首屏可交互 | ≤ B0 基线 +20% | |
| 加载 4 页后 DOM 节点数 | ≤ B0 基线的 50% | |
| 默认列表 API P50 | ≤ 迁移前基线 | |
| `/api/documents/facets` P95 | ≤ 300 ms | |
| 语义 hybrid P95（若 go） | ≤ 800 ms | |
| 封面 blob 数量 | ≤ 220 | |
| 库体积 / WAL 大小 | 记录 | |

未达标**不硬凑、不改指标定义**：写清实测值、定位到的原因、建议动作（下一轮或本批已获授权的修复）。

**产物**：`docs/roadmap/reports/B6-02-ux-perf.md` + 截图放 `docs/screenshots/roadmap-2026-10/`。

**验收**：矩阵每格有结论；截图落盘；性能表有真实数字。

---

### B6-03 数据清理（逐次授权）

> 本节**默认一个都不执行**。每个动作必须：只读审计 → 预览给用户看 → 用户明确同意 → 备份 → 执行 → 记录前后计数与 operation log id。

**动作清单**（按风险从低到高排；用户可只批准其中几项）：

| ID | 动作 | 工具 | 影响 | 撤销方式 |
|---|---|---|---|---|
| C1 | WAL checkpoint | `server/scripts/wal_checkpoint.py` | 只整理 WAL，不改数据 | 无需（可重放） |
| C2 | 语言字段归一化 | `server/scripts/normalize_language.py`（或 B2-08 API） | 改 `documents.language` | operation log / 脚本 `--apply` 前的 CSV 快照 |
| C3 | 重复 sha256 组软删除 | `server/scripts/dedup_sha256.py` | 软删 3 组中的冗余本 | `POST /api/operations/{id}/undo`（若走 API）；脚本路径需 CSV 手工恢复 |
| C4 | 孤儿 blob 隔离 | `server/scripts/clean_orphan_blobs.py` | **移动**文件到 `data/quarantine/...` | 按 manifest 移回 |
| C5 | 无正文书重提取 | B2-07 `/api/documents/reextract` | 写 `documents.content` | `undoable=false`，只能靠备份 |
| C6 | 无封面补全 | B2-06 上传 / `backfill_covers.py` | 写 `cover_path` + 文件 | operation log（上传走 API 时） |
| C7 | 软删除文档处置 | **无脚本，需用户决定** | 保留 / 长期归档 / 导出清单 | — |

**执行纪律**：

1. 每一步先跑**不带 `--apply`** 的 dry-run，把「影响条数 + 前 20 条标题」原文贴给用户。
2. 用户同意后立即：
   ```bash
   E:/zy/python/python.exe server/scripts/incremental_backup.py --apply --keep 7
   ```
   再 `pragma integrity_check`，再执行。
3. 单批 ≤500 文档（与 [00-execution-rules.md](00-execution-rules.md) 2.3 一致）；超过就分批，每批之间复核计数。
4. 执行后必须核对：
   - 存活数、软删除数、正文为空数、无封面数、未分类数、语言分布 —— 与预览值一致；
   - 不一致 → **立即停止后续动作**，登记 BLOCKED，不要「先做完再解释」。
5. 已知具体问题必须给结论，不能停留在「有待处理」：
   - 《历史与自由意识》：5 秒提取预算不足 → 用 C5 的 `reextract` 带大预算（预期 158,463 字 / 约 37 秒）；
   - 《读人像法官一样识人断事》：扫描件，282/285 页空白 → 只在 `D4=安装 OCR` 时处理，否则保持 `needs_ocr` 标记；
   - Downloads 中 19 个坏文件（5×0 字节 + 14×143 字节错误页）→ **不入库、不删除用户下载目录里的文件**，只在报告列清单。
6. 报告里每个动作都要有：授权时间、授权原话摘录、备份目录、operation log id、前后计数、回滚验证结果。

**验收**：只做了被明确授权的动作；未授权的动作停留在 dry-run 且写在报告「未执行」小节。

---

### B6-04 文档、发布说明、回滚说明

**文件**：

- 修改：[README.md](../../README.md)（项目根）
- 修改：[docs/roadmap/README.md](README.md)（状态表全部收尾）
- 新增：`docs/roadmap/RELEASE-2026-10.md`
- 修改（如接口/结构变了）：[api-contracts.md](api-contracts.md)、[data-model.md](data-model.md) 与实际代码对齐

**根 README 必须修的地方**：

1. 首行写「Next.js 14(App Router)」—— 实际是 **15.5.28**，改掉。
2. 功能列表补：质量中心 `/quality`、导入中心 `/import`、阅读队列 `/queue`、间隔复习、书架排序/分面/虚拟化、（若 go）语义检索。
3. 「API 行为注记」补新增端点摘要与参数（`sort/quality/format/language`、`/facets`、`/ids`、批量、operations/undo、imports、reextract、semantic）。
4. 「快速开始」补 `KB_QUALITY_MIN_CONTENT_CHARS`、`KB_IMPORT_DIRS`、`KB_REEXTRACT_TIMEOUT_SECONDS`、`KB_SEMANTIC_*` 等新增配置项与默认值。
5. 目录结构一节补新增模块（`services/quality_audit.py`、`services/ingest.py`、`services/directory_scan.py`、`services/operation_log.py`、`services/job_state.py`、`services/review_scheduler.py`、`services/semantic/*`、`routers/quality.py`、`routers/imports.py`、`routers/operations.py`、`routers/reading_queue.py`、`routers/reviews.py`、`documents/facets.py`）。
6. 若有 `SKIPPED`（例如 B5 no-go），在 README 明确写「已评估语义检索，因 <原因> 本轮未实现」，不留「TODO 悬空」。

**`RELEASE-2026-10.md` 结构**：

```text
1. 本轮做了什么（按 B0–B6 分节，每节 3–6 条要点）
2. 用户可见变化（截图路径 + 一句话）
3. 数据变化（前后计数总表、执行过的清理、operation log 索引）
4. 迁移链（0006 → 最终 head，每步耗时）
5. 新增配置项与默认值
6. 新增 API 一览
7. 已知限制与未做项（OCR、中文排序、本地模式无复习、chunk 定位精度…）
8. 回滚手册（代码 / 迁移 / 数据 / 整库，四段，每段给具体命令）
9. 下一轮建议（按优先级 3–5 条，来自各批「发现」区）
```

**回滚手册必须能照着敲**：写清「哪一批对应哪个 commit」「`alembic downgrade` 到哪个 revision」「备份在哪个目录」「哪些派生数据可安全删」。

**验收**：新人只读 README + RELEASE 就能启动项目、知道每个新开关的作用、能独立回滚；文档与实际代码无参数名漂移（对照 `config.py` 与路由逐条检查）。

---

### B6-05 最终报告、提交与 CI

**提交纪律**：

1. 每批一个提交（B0–B5 若已在各批提交，本批只提交收口改动）。
2. 只按文件名暂存，禁止 `git add -A` / `git add .`：
   ```bash
   git add README.md docs/roadmap/RELEASE-2026-10.md docs/roadmap/reports/...
   git status --short        # 确认没有多余文件
   git diff --cached --stat
   ```
3. 提交前确认不含：真实库文件、`server/data/`、备份目录、spike venv、日志、截图里的用户藏书标题（若敏感，截图只放本地并在报告注明）。
4. 建议的收口提交信息：
   ```text
   docs(roadmap): 路线图收口、发布说明与回归报告（B6）
   ```

**推送（G4，需用户确认后执行）**：

```bash
git push origin main
```

- 若因 GitHub 连接失败（本机 `github.com:443` 偶发不可达），用**一次性**解析覆盖，不写入任何配置：
  ```bash
  git -c http.curloptResolve=github.com:443:140.82.113.3 push origin main
  ```
  该 IP 失效时换其它 GitHub edge IP，同样只加 `-c` 到单条命令。
- 禁止 `--force`、禁止 `--no-verify`。

**CI 确认**：

```bash
gh run list --workflow CI -L 1
gh run view <run-id>
```

- tip run 必须全绿（`infra-lint` / `backend` / `frontend` / `e2e`）。
- 红的话：**修完重新提交**，不合并、不忽略；若失败源于环境（runner 抖动），重跑并在报告注明重跑次数与原因。
- `pip-audit` / `npm audit` 步骤若为 `continue-on-error`，仍要把 artifact 里的条目读出来并写进报告结论（有多少条、哪些无关本轮、哪些需下一轮）。

**最终报告**：`docs/roadmap/reports/B6-2026-10-26.md`，汇总：

- 各批状态表（DONE / SKIPPED + 一句结论）。
- 基线对比：存活、软删、正文为空、无封面、未分类、语言分布、重复组 —— 从 2026-10-26 基线到现在的数字与原因。
- 迁移链与耗时、备份清单与校验结果。
- 性能表（引 B6-02 实测）。
- 全部「发现」区条目的最终去向。
- commit 列表（`git log --oneline` 本轮区间）与 CI run 链接。
- 下一轮建议清单。

**验收**：tip 提交已推送、CI 全绿、最终报告写完、[progress.md](progress.md) 全部任务为终态且无 `TODO` 残留（或明确标注 SKIPPED 与原因）。

## 5. 验收清单

- [ ] 后端 pytest / alembic 单 head / 副本升降级演练通过。
- [ ] 前端 typecheck / lint / vitest / build / e2e 全绿。
- [ ] `npm audit --omit=dev`（官方 registry）与 `pip-audit` 结果有结论。
- [ ] 备份 `--verify` 与 `restore_drill --verify` 通过。
- [ ] gitleaks 未发现新泄密。
- [ ] 移动端/暗色/键盘/读屏走查矩阵每格有结论。
- [ ] 性能表有真实数字，未达标项有定位。
- [ ] 数据清理只执行了被逐项授权的动作；未执行项停在 dry-run。
- [ ] 根 README 与 RELEASE 文档与实际代码一致，无参数名漂移。
- [ ] 提交按批分离、无无关文件、无 `git add -A`。
- [ ] 已推送且 CI tip run 全绿。
- [ ] 最终报告完成，progress.md 无 `TODO` 残留。

## 6. 回滚

本批以文档与验证为主，回滚成本极低：

1. 文档提交：`git revert <B6 commit>`。
2. 数据动作的回滚方式在 B6-03 每个动作执行时就已写入报告，不在此重复。
3. 若推送后 CI 红且不立即修复：`git revert` 相应提交并再推送（不 force、不 reset）。

## 7. 下一轮怎么接

- B6 报告「下一轮建议」里前 3 条，作为新一轮路线图的输入。
- 新一轮继续沿用 [00-execution-rules.md](00-execution-rules.md) 与 [progress.md](progress.md) 的结构：先更新基线数字，再写任务书，不要直接改代码。
