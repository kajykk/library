# B0 · 基线与护栏

| 项目 | 值 |
|---|---|
| 批次 | B0 |
| 状态 | TODO |
| 依赖 | 无 |
| 真实数据写入 | 否（只做备份） |
| 预计改动 | 9 个文件（4 改 5 新） |
| 完成标志 | B0-01 ~ B0-08 全部 DONE，B0 报告已写 |

## 1. 目标

在写任何新功能前，先把三件事固定下来：

1. 当前唯一未提交的 CSP 修复单独入库，不和新功能混在一起。
2. 数据源状态有统一契约，后续所有页面不再各自猜 `api/local`。
3. 修掉「设置页保存了后端配置，页面却还停在本地模式」的缓存 bug。
4. 准备共享 UI 状态组件和后台任务状态基座，避免 B1/B2/B5 重复造轮子。

## 2. 不做什么

- 不新增依赖。
- 不改数据库、不跑迁移。
- 不改书架布局、不改质量逻辑。
- 不顺手修 `next.config.mjs` 之外的配置。
- 不执行任何 `--apply` 数据脚本。

## 3. 前置检查

```bash
git status --short
# 预期只有： M .gitignore / M next.config.mjs / ?? docs/roadmap/
git log --oneline -3
# 预期 HEAD 为 d8dc64d
```

如果出现其他未提交文件，先停下，在 [progress.md](progress.md) 的「发现」区登记，不要把它们混进 B0 提交。

> `?? docs/roadmap/` 是本轮计划的全部依据，必须入库；否则下一轮换机器/换目录就找不到进度。

## 4. 任务

### B0-01 提交路线图文档与 CSP 修复

**文件**：[.gitignore](../../.gitignore)、[docs/roadmap/](../../docs/roadmap/)、[next.config.mjs](../../next.config.mjs)

**第 1 步 · 路线图文档入库（先做，后面的批次都依赖它能被跟踪）**

已验证的事实：`.gitignore` 原规则是 `docs/*` + `!docs/*.md`，而 gitignore 的 `*` **不跨越 `/`**，所以 `docs/roadmap/*.md` 全部被忽略（`git add docs/roadmap/README.md` 静默无效）。本轮已在 `.gitignore` 增加一行 `!docs/roadmap/` 放行该目录；`docs/screenshots/` 等二进制继续被忽略。

1. 确认忽略规则生效，且截图目录仍然忽略：

   ```bash
   git check-ignore -v docs/roadmap/README.md docs/screenshots/x.png; echo "exit=$?"
   # 预期：README.md 无输出（未忽略），docs/* 命中 docs/screenshots/x.png
   ```

2. 确认待提交内容只有 12 份 Markdown，无向量、无备份、无 `.db`：

   ```bash
   git status --short docs/roadmap/
   ls docs/roadmap/
   ```

3. 逐个文件暂存（禁止 `git add -A` / `git add .`）：

   ```bash
   git add .gitignore docs/roadmap/README.md docs/roadmap/00-execution-rules.md \
     docs/roadmap/progress.md docs/roadmap/api-contracts.md docs/roadmap/data-model.md \
     docs/roadmap/B0-foundation.md docs/roadmap/B1-data-source-quality.md \
     docs/roadmap/B2-import-batch.md docs/roadmap/B3-library-ui.md \
     docs/roadmap/B4-reading-review.md docs/roadmap/B5-semantic-search.md \
     docs/roadmap/B6-closeout.md
   git diff --cached --stat
   git commit -m "docs: 加入 B0–B6 执行路线图与 API/数据契约（B0）"
   ```

**第 2 步 · CSP 修复**

1. 确认 diff 只包含 dev-only `'unsafe-eval'` 分支：

   ```bash
   git diff -- next.config.mjs
   ```

2. 确认生产分支仍为 `script-src 'self' 'unsafe-inline'`，没有把 `'unsafe-eval'` 带进生产。
3. 运行前端验证：

   ```bash
   npm run typecheck
   npm run lint
   npm test
   npm run build
   ```

4. 只暂存该文件并提交：

   ```bash
   git add next.config.mjs
   git commit -m "fix(web): 仅在开发环境放行 CSP unsafe-eval（B0）"
   ```

**不要**：`git add -A`、`--amend`、`--no-verify`。

**证据**：两个 commit hash、`git show --stat`、`git check-ignore` 输出、验证命令结果。

---

### B0-02 基线备份与完整性检查

**前置**：B0-01 已提交，工作区干净。

**命令**：

```bash
python server/scripts/incremental_backup.py --apply --keep 7
```

然后用 Python 检查：

```bash
E:/zy/python/python.exe - <<'PY'
import sqlite3, os
p = r"E:\code\projects\library\server\knowledge_base.db"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("integrity:", con.execute("pragma integrity_check").fetchone())
print("alive:", con.execute("select count(*) from documents where deleted_at is null").fetchone())
print("soft_deleted:", con.execute("select count(*) from documents where deleted_at is not null").fetchone())
print("db_bytes:", os.path.getsize(p))
con.close()
PY
```

**记录**：

- 备份目录路径
- `integrity_check` 结果
- alive / soft_deleted / db_bytes
- WAL 大小（如存在）

**验收**：`integrity_check` 为 `ok`；备份目录存在且包含主库与 manifest。

---

### B0-03 数据源状态契约

**文件**：

- 修改：[src/lib/dataSource.ts](../../src/lib/dataSource.ts)
- 新增：[src/lib/useDataSourceStatus.ts](../../src/lib/useDataSourceStatus.ts)
- 新增：[src/lib/__tests__/dataSourceStatus.test.ts](../../src/lib/__tests__/dataSourceStatus.test.ts)

**为什么**：当前 `resolveMode()` 只返回 `'api' | 'local'`（[dataSource.ts:92](../../src/lib/dataSource.ts#L92)），页面无法区分「没配 token」「配了但后端挂了」「正常」，于是只能静默回退。B1 的状态条需要原因。

**类型契约**（放在 `dataSource.ts`）：

```ts
export type DataSourceStatusMode = 'api' | 'local' | 'degraded' | 'unconfigured';
export type DataSourceStatusReason = 'api_ok' | 'no_token' | 'health_failed' | 'checking';

export interface DataSourceStatus {
  mode: DataSourceStatusMode;
  reason: DataSourceStatusReason;
  baseUrl: string;
  tokenConfigured: boolean;
  health: SystemHealth | null;
}

export async function resolveDataSourceStatus(force = false): Promise<DataSourceStatus>;
```

**行为**：

| 条件 | `mode` | `reason` |
|---|---|---|
| 有 token 且 `/api/health` 成功 | `api` | `api_ok` |
| 没有 token | `local` | `no_token` |
| 有 token 但健康检查失败/超时 | `degraded` | `health_failed` |
| 正在请求 | `local`（或上一次缓存） | `checking` |

**实现要求**：

1. 30 秒缓存 + 单飞 Promise，避免每个页面重复打 `/api/health`。
2. `resolveMode()` 保持原签名，内部委托 `resolveDataSourceStatus()`：
   - `api` → `'api'`
   - `local` / `degraded` / `unconfigured` → `'local'`
3. `resetModeCache()` 同时清空状态缓存和单飞 Promise。
4. 不返回 token；`baseUrl` 可以返回。
5. 服务端渲染（`window === undefined`）时返回 `unconfigured`，不要访问 `localStorage`。

**Hook**（新文件）：

```ts
export function useDataSourceStatus(): {
  status: DataSourceStatus | null;
  isResolving: boolean;
  refresh: () => void;
};
```

使用 React Query key `['dataSourceStatus']`，`staleTime` 与 `resolveDataSourceStatus` 的缓存一致（30s）。`refresh()` 调 `resolveDataSourceStatus(true)` 并更新缓存。

**测试**：

- 无 token → `local/no_token`。
- 有 token + health ok → `api/api_ok`。
- 有 token + health fail → `degraded/health_failed`。
- `resolveMode()` 在 degraded 时仍返回 `local`（数据逻辑不变）。
- 两次并发调用只发一次 `fetch`。

---

### B0-04 修复设置页保存后的模式缓存

**文件**：[src/app/settings/page.tsx](../../src/app/settings/page.tsx)

**问题**：

- 保存配置时只调用了 `resetModeCache()`（[settings/page.tsx:111](../../src/app/settings/page.tsx#L111)）。
- `useDataSourceMode` 的 React Query 缓存是 `staleTime: Infinity`（[useDataSourceMode.ts:22](../../src/lib/useDataSourceMode.ts#L22)），所以搜索页等使用该 Hook 的页面不会重新探测。
- 书架自身的 `LIBRARY_QUERY_KEY` 也不会自动失效，用户会看到「保存成功但书还是 0 本」。

**做法**：

1. 在设置页引入 `useQueryClient` 和 `useDataSourceModeInvalidator()`。
2. `handleSaveAndTest()` 保存后：
   ```ts
   saveApiConfig(apiBase, apiToken);
   resetModeCache();
   invalidateMode(); // useDataSourceModeInvalidator
   await queryClient.invalidateQueries({ queryKey: ['dataSourceStatus'] });
   await queryClient.invalidateQueries({ queryKey: ['documents'] });
   ```
3. 如果健康检查失败，保留 `connectionStatus='fail'`，但状态条应显示 `degraded`，不要谎报成功。
4. 设置页加载时如果已有 token，自动跑一次健康检查（可选；不阻塞页面）。

**验收**：

- 在浏览器 localStorage 为空时进入书架 → 显示本地/未连接。
- 在设置页填入正确 base + token，保存 → 不刷新页面，书架自动重新加载并显示 API 数据。
- 填入错误 token，保存 → 状态条显示「后端不可达/令牌错误」，书架仍可用本地数据，不白屏。

---

### B0-05 共享 UI 状态组件

**新增**：

- [src/components/ui/StatePanel.tsx](../../src/components/ui/StatePanel.tsx)
- [src/components/ui/FilterChip.tsx](../../src/components/ui/FilterChip.tsx)

**修改**：

- [src/components/ui/index.ts](../../src/components/ui/index.ts) 导出两个组件。

**`StatePanel` 契约**：

```ts
type StatePanelVariant = 'loading' | 'empty' | 'error';

interface StatePanelProps {
  variant: StatePanelVariant;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}
```

要求：

- 使用现有 Tailwind 语义色，不引入新主题库。
- `error` 必须有重试入口。
- `loading` 用骨架/旋转图标，不用裸文字「加载中...」。
- 支持 `role="status"` / `role="alert"`。

**`FilterChip` 契约**：

```ts
interface FilterChipProps {
  label: string;
  count?: number;
  active?: boolean;
  onClear?: () => void;
  onClick?: () => void;
}
```

**验收**：组件可独立渲染；有基础 vitest（可用 `@testing-library/react`）。

---

### B0-06 后台任务状态基座

**新增**：[server/app/services/job_state.py](../../server/app/services/job_state.py)

**背景**：现有 [reindex_job.py](../../server/app/services/reindex_job.py) 自己在模块级维护状态。B1 质量审计、B2 目录扫描/导入、B5 语义索引都需要同样的「单飞、进度、错误」状态，复制四套会漂移。

**契约**：

```python
class JobState:
    def __init__(self, name: str): ...
    def start(self, total: int = 0) -> bool: ...
    def update(self, **fields) -> None: ...
    def finish(self) -> None: ...
    def fail(self, error: str) -> None: ...
    def snapshot(self) -> dict: ...
```

字段至少包含：

```python
{
  "name": str,
  "running": bool,
  "total": int,
  "done": int,
  "error": str | None,
  "started_at": str | None,
  "finished_at": str | None,
}
```

要求：

- `threading.Lock` 保护。
- `start()` 在已运行时返回 `False`，不覆盖现有进度。
- `finish()/fail()` 只在 running 时生效。
- 时间使用 UTC ISO 字符串。
- 不依赖 FastAPI/SQLAlchemy，方便单测。

**测试**：[server/tests/test_job_state.py](../../server/tests/test_job_state.py)

- 首次 start 返回 True；第二次返回 False。
- update 合并字段。
- finish 后 running=False、finished_at 非空。
- fail 记录 error。

**注意**：B0 只新增基座，不改造 `reindex_job`。是否迁移 `reindex_job` 由 B6 决定，不在本批顺手改。

---

### B0-07 补测试并跑全量验证

**测试文件**：

- `src/lib/__tests__/dataSourceStatus.test.ts`
- `src/components/__tests__/StatePanel.test.tsx`（可选，若时间允许）
- `server/tests/test_job_state.py`

**命令**：

```bash
cd server
E:/zy/python/python.exe -m pytest tests -q

cd ..
npm run typecheck
npm run lint
npm test
npm run build
```

**验收**：全部通过；新增测试确实覆盖失败分支，不是只跑 happy path。

---

### B0-08 写 B0 报告

**新增**：`docs/roadmap/reports/B0-2026-10-26.md`（目录不存在则创建）

内容按 [00-execution-rules.md](00-execution-rules.md) 第 6 节模板：

- 目标与范围
- 实际改动文件
- 验证命令与结果
- 数据影响（仅备份）
- 未完成/跳过
- 风险与回滚
- 下一批前置条件

同时更新：

- [progress.md](progress.md) 的 B0 行、当前状态、批次切换记录。
- [README.md](README.md) 的状态总览。

## 5. 验收清单

- [ ] CSP 修复单独提交，生产分支无 `'unsafe-eval'`。
- [ ] 备份目录存在，`integrity_check=ok`。
- [ ] 四种数据源状态都有测试覆盖。
- [ ] 设置页保存后无需刷新即可切换模式。
- [ ] `StatePanel`/`FilterChip` 已导出并在至少一个页面使用（可在 B1 使用）。
- [ ] `job_state.py` 单测通过。
- [ ] 全量验证通过。
- [ ] B0 报告已写，progress.md 已更新。

## 6. 回滚

- 代码：`git revert <B0 commit>`。
- 备份：B0-02 的备份目录保留，不要删除。
- 无数据库变更，无数据回滚。

## 7. 交给 B1 的东西

- `resolveDataSourceStatus()` + `useDataSourceStatus()` 可直接被状态条使用。
- `StatePanel`/`FilterChip` 可直接用于质量页和书架。
- `JobState` 可直接被质量审计任务使用。
- B0 报告中记录基线计数，B1 完成后对比。
