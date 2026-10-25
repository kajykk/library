# P3-1 语言字段归一化审计报告

日期：2026-10-26
脚本：[server/scripts/normalize_language.py](../server/scripts/normalize_language.py)
状态：已入库脚本；**未对真实数据库执行 `--apply`**（需单独授权）

## 概要

- 数据库：`server/knowledge_base.db`（只读打开）
- 文档总数：1314（未删除 1305）
- 需要归一化：202 条（全部为未删除文档）
- 未映射值：0
- 归一化目标：`zh` / `en` / `ja` / `und`

## 变更前分布

| 原值 | 数量 |
|---|---:|
| zh | 1004 |
| en | 103 |
| zh-TW | 60 |
| en-US | 56 |
| zh-CN | 56 |
| EN-US | 10 |
| eng | 6 |
| und | 4 |
| en-us | 2 |
| zh-Hans | 2 |
| zh-Hant | 2 |
| zh-cn | 2 |
| EN | 1 |
| cn | 1 |
| en-GB | 1 |
| ja | 1 |
| zh-Hans-CN | 1 |
| zh-tw | 1 |
| 简体中文 | 1 |

## 归一化映射与变更量

| 原值 → 目标 | 数量 |
|---|---:|
| zh-TW → zh | 60 |
| en-US → en | 56 |
| zh-CN → zh | 56 |
| EN-US → en | 10 |
| eng → en | 6 |
| en-us → en | 2 |
| zh-Hans → zh | 2 |
| zh-Hant → zh | 2 |
| zh-cn → zh | 2 |
| EN → en | 1 |
| cn → zh | 1 |
| en-GB → en | 1 |
| zh-Hans-CN → zh | 1 |
| zh-tw → zh | 1 |
| 简体中文 → zh | 1 |
| **合计** | **202** |

`zh` / `en` / `ja` / `und` 原值保持不变；空字符串与 NULL 映射为 `und`（本次无此类变更）。

## 变更后预览

| 语言 | 数量 |
|---|---:|
| zh | 1130 |
| en | 179 |
| und | 4 |
| ja | 1 |

## 脚本行为

```bash
# 预览（默认 dry-run，只读）
python server/scripts/normalize_language.py

# JSON 输出
python server/scripts/normalize_language.py --json

# 写库（需单独授权；先停止 uvicorn）
python server/scripts/normalize_language.py --apply
```

`--apply` 流程：

1. 完整备份 `knowledge_base.db` 及 `-wal` / `-shm` 到 `knowledge_base.db.bak.<timestamp>`；
2. 按 `WHERE id = ? AND language IS ?` 条件更新，避免覆盖 dry-run 之后的并发修改；
3. 在同一事务内写入，提交后重新只读统计实际分布；
4. 若实际分布与预览不一致，JSON 中 `after_matches_preview` 为 `false`，需人工复核。

## 隔离自测

在临时目录复制的小型数据库上验证 `--apply`：

- 5 条语言值被正确归一化；
- 生成了主库与 `-wal` / `-shm` 备份；
- 实际分布与预览一致；
- 第二次 `--apply` 无待变更记录，不创建备份、不写库。

真实数据库未执行 `--apply`。dry-run 前后主库 `size=729,640,960`、`mtime_ns=1791639713079759400`，WAL/SHM 亦未变化。

## 发现与未处理

- 语言值共出现 19 种写法，其中大小写混用（`EN` / `en-US` / `en-us`）与地区后缀（`zh-TW` / `zh-Hans-CN`）占多数；归一化后只保留 4 种。
- `zh-TW` / `zh-Hant` 与 `zh-CN` / `zh-Hans` 是否应区分为 `zh-Hant` / `zh-Hans` 属于产品口径问题；本脚本按任务要求统一为 `zh`，未做文字变体区分。
- `.gitignore` 已新增 `server/*.db.bak.*`，避免自动备份被误提交。

## 回滚

停止服务后，用对应时间戳的 `.bak` 覆盖主库及 `-wal` / `-shm`，或对 202 条记录按原值反向更新。备份文件保留在 `server/` 下，不自动删除。
