# 执行规则与门禁

> 本文件优先级高于任务书中的「建议」。任务书与这里冲突时，以本文件为准。

## 1. 阅读顺序

1. 本文件。
2. [README.md](README.md) 的基线、状态、待确认决策。
3. [progress.md](progress.md) 找到第一个 `TODO` 任务。
4. 对应批次的任务书。
5. 涉及接口/表时，查 [api-contracts.md](api-contracts.md) 和 [data-model.md](data-model.md)。

## 2. 硬约束

### 2.1 Git

- 禁止 `git add -A` / `git add .`；只按文件名暂存。
- 禁止 `git commit --amend`。
- 禁止 `git commit --no-verify`、`git push --force`、`git push -f`。
- 禁止修改 git config。
- 每批单独提交；不把两个批次的改动混进同一提交。
- 提交前必须看 `git status --short` 和 `git diff --stat`；发现不属于本批的文件，先停下确认。

### 2.2 依赖

- B0–B4 不新增 npm/Python 依赖。
- B5 如需新增（`fastembed`、`sqlite-vec`/`hnswlib` 等），必须先完成 spike 并由用户批准。
- 禁止借机升级 react / next / tiptap / pdfjs-dist / vitest 等无关包。
- 禁止用 npm 11 生成 lockfile；使用项目既有 npm 10 流程。
- 禁止为了让 audit 归零而改无关 overrides。

### 2.3 数据

- 所有真实数据写入必须有 **dry-run 预览 + 用户明确授权**。
- 禁止硬删除 `documents`；只允许软删除（`deleted_at`）。
- 禁止删除 `server/data/` 下的文件；孤儿文件只能移动到隔离目录。
- 批量操作最多 500 个文档；超过就要求缩小筛选。
- 每次真实写入前：
  1. `python server/scripts/incremental_backup.py --apply --keep 7`
  2. `pragma integrity_check`
  3. 记录执行前计数
  4. 执行
  5. 记录执行后计数、操作日志 id、回滚方法
- 测试必须使用临时库（`server/tests/conftest.py` 已设置），禁止连真实 `server/knowledge_base.db`。

### 2.4 代码

- 不改与本批无关的模块；发现的问题写入 [progress.md](progress.md) 的「发现」区，不在本批顺手重构。
- 不改变现有公共函数签名，除非任务书明确要求。特别是：
  - `content_extract.extract_text_from_file(fmt, path)` 保持原签名；B2 新增带 timeout 的包装函数。
  - `dataSource.resolveMode()` 保持原签名；B0 只增加状态函数并让 `resolveMode` 委托。
- 不引入新的全局状态库；继续使用 React Query + 现有 UI 组件。
- 不写无意义注释；只在「为什么」不明显时写一行。
- 新增 API 必须走 `X-API-Token` 鉴权（挂在 `/api` 下即自动继承）。
- 新增文件路径、函数名、类型名必须与任务书一致；如需偏离，先在 progress.md 记录原因。

## 3. 授权门禁

| 门禁 | 触发条件 | 需要谁确认 | 确认后记录 |
|---|---|---|---|
| G1 真实数据写入 | 导入、软删除、补封面、重提取、语言归一化、孤儿隔离 | 用户逐次确认 | 操作前后计数、备份路径、operation log id |
| G2 新增依赖 | B5 任何新包 | 用户确认 | 包名、版本、用途、audit 结果 |
| G3 迁移 | 新增 Alembic 迁移 | 用户确认 | 迁移 id、备份路径、`alembic upgrade head` 输出 |
| G4 推送 | `git push` | 用户确认 | 分支、commit、CI run |
| G5 跳过验收 | 任一任务不达标仍想进入下一批 | 用户确认 | 跳过原因、风险、补偿措施 |

未获得确认时，执行方只能做：只读查询、dry-run、临时库测试、前端静态改动。

## 4. 备份与回滚

### 4.1 迁移前

```bash
python server/scripts/incremental_backup.py --apply --keep 7
sqlite3 server/knowledge_base.db "pragma integrity_check;"
```

如果没有 `sqlite3` CLI，用：

```bash
E:/zy/python/python.exe - <<'PY'
import sqlite3
con = sqlite3.connect(r"E:\code\projects\library\server\knowledge_base.db")
print(con.execute("pragma integrity_check").fetchone())
PY
```

### 4.2 回滚层级

1. **代码回滚**：`git revert <commit>`（不要 `reset --hard`）。
2. **功能回滚**：关 feature flag（B5）或隐藏入口。
3. **数据回滚**：
   - 软删除：`POST /api/operations/{id}/undo`。
   - 批量修改：用 operation log 的 `before_state` 恢复。
   - 导入：软删除新导入的文档；源文件仍在原目录。
   - 迁移：优先降级迁移；禁止直接删表，除非有备份且用户授权。
4. **整库回滚**：用备份目录覆盖主库前必须先停后端，并保留当前库副本；需用户确认。

## 5. 验证命令

每批至少跑：

```bash
# 后端
cd server
E:/zy/python/python.exe -m pytest tests -q

# 前端
npm run typecheck
npm run lint
npm test
npm run build
```

涉及页面/交互时追加：

```bash
npm run test:e2e
```

依赖或安全相关批次追加：

```bash
npm audit --omit=dev --registry=https://registry.npmjs.org
pip-audit --desc
```

> 注意：
> - audit **必须显式指定官方 registry**。本机默认走 npmmirror 镜像，该镜像没有 audit 端点，会返回 `[NOT_IMPLEMENTED]`，得到的「0 漏洞」是假结果。
> - 不要用 npm 11 重新生成 lockfile（曾删掉 `@floating-ui/dom` 打断 CI）；安装/校验统一 `npx npm@10 ci`。
> - `npm audit fix` 可能崩（`Cannot read properties of null (reading 'edgesOut')`）；传递依赖用显式版本或定向 `overrides` 解决。

## 6. 证据模板

每个任务完成后，在 [progress.md](progress.md) 对应行填写：

```text
证据：
- 改动：<文件列表>
- 命令：<实际执行的命令>
- 结果：<通过/失败；关键输出摘要>
- 数据：<执行前后计数，仅数据任务>
- 回滚：<如何回滚>
- 备注：<偏离任务书的地方>
```

批次完成时追加一份批次报告，建议放到 `docs/roadmap/reports/Bx-<date>.md`，包含：

- 目标与范围
- 实际改动文件
- API/迁移变更
- 验证结果（命令 + 摘要）
- 数据影响（前后计数、备份路径、operation log）
- 未完成/跳过项
- 风险与回滚
- 下一批前置条件

## 7. 发现问题的处理

执行中发现任务书之外的 bug/风险时：

1. 不要直接修。
2. 在 [progress.md](progress.md) 的「发现」区新增一行：
   - 位置（文件:行）
   - 现象
   - 影响
   - 建议批次
3. 如果该问题会阻塞当前任务，登记为 `BLOCKED` 并说明原因；否则继续当前任务。

## 8. 批次切换检查

进入下一批前，确认：

- 当前批所有 `MUST` 任务为 `DONE`。
- 没有未提交的无关文件。
- 当前批报告已写。
- 数据库/前端均可启动。
- 下一批的前置条件满足（依赖、授权、决策）。
- `progress.md` 的「当前批次」已更新。
