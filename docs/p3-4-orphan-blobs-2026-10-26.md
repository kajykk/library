# P3-4 孤儿 blob 审计与隔离报告

日期：2026-10-26
脚本：[server/scripts/clean_orphan_blobs.py](../server/scripts/clean_orphan_blobs.py)
状态：已入库脚本；**未对真实数据目录执行 `--apply`**（需单独授权）

## 概要

扫描 `server/data/covers/` 与 `server/data/files/`，以 `documents.file_path` / `documents.cover_path` 的 basename 作为引用集合。

| 指标 | 数值 |
|---|---:|
| 被引用文件（按目录去重） | 2095 |
| — covers | 783 |
| — files | 1312 |
| 磁盘文件 | 2101 |
| — covers | 783 |
| — files | 1318 |
| 孤儿文件 | 6 |
| 孤儿总大小 | 62,981,321 bytes（约 60.1 MiB） |
| 损坏引用（引用但磁盘缺失） | 0 |
| 无法识别目录的引用 | 0 |

孤儿全部位于 `data/files/`，`data/covers/` 无孤儿。

## 孤儿清单

| 文件 | 大小 (bytes) | mtime |
|---|---:|---|
| `files/052d4582-b37d-4942-b3d5-8644f776e706.pdf` | 11,398,420 | 2026-09-15 20:28:06 |
| `files/20040462-d444-4358-b228-7be53aacb1c7.pdf` | 10,321,829 | 2026-09-15 20:27:27 |
| `files/2c5178e5-1b6b-450b-9915-1fecac3f6f1e.pdf` | 9,336,355 | 2026-09-15 20:25:38 |
| `files/48ba76e1-a61e-450b-8f14-183a5475808c.pdf` | 9,760,769 | 2026-09-15 20:26:50 |
| `files/d2048f74-57fd-4900-b363-997c397d4c35.pdf` | 12,606,722 | 2026-09-15 20:28:39 |
| `files/f3e0092a-e218-4467-a084-e0ce12631ac2.pdf` | 9,557,226 | 2026-09-15 20:26:14 |

6 个文件均为 PDF，时间集中在 2026-09-15 20:25–20:28，推测是同一批导入失败或记录被删除后遗留的正文文件。

## 与 P2 封面审计的差异

P2 的 `audit_covers.py` 只扫描 `data/covers/`，结论为 0 孤儿。本次把 `data/files/` 纳入扫描后才发现这 6 个 PDF，说明**仅审计封面目录会漏掉正文 blob 的孤儿**。

## 脚本行为

```bash
# 预览（默认 dry-run，只读）
python server/scripts/clean_orphan_blobs.py

# JSON 输出
python server/scripts/clean_orphan_blobs.py --json

# 隔离孤儿（需单独授权）
python server/scripts/clean_orphan_blobs.py --apply
```

`--apply` 流程：

1. 完整备份 `knowledge_base.db` 及 `-wal` / `-shm`（即使本操作不写 DB，也保留恢复点）；
2. 在 `data/_orphan_quarantine/<timestamp>/` 下按 `covers/`、`files/` 重建目录；
3. 使用 `shutil.move` 移动孤儿文件，**不删除**；目标已存在则跳过；
4. 写入 `manifest.json`（含创建时间、DB 备份路径、移动明细）；
5. 重新扫描并报告剩余孤儿数。

## 隔离自测

在临时目录构造 2 个孤儿文件后执行 `--apply`：

- 2 个文件被移动到隔离目录，原文件不再存在（移动而非删除）；
- 生成 DB 备份；
- DB 文件 mtime 未变化（本操作不写库）；
- 第二次 `--apply` 无孤儿可移动，不产生新的隔离动作。

真实 `data/files/` 未执行 `--apply`，dry-run 前后主库与 WAL/SHM 的 size、mtime_ns 完全一致。

## 发现与未处理

- 6 个孤儿 PDF 共 63 MB，若确认无用可隔离后观察一段时间再人工删除；脚本本身永不删除。
- 孤儿文件名是 UUID，无法从文件名判断对应书目。如需追溯，可比较 PDF 元数据或内容哈希与现有文档的 `sha256`。
- 软删除文档的 `file_path` 仍被 DB 引用，因此其文件不会被判为孤儿；本次统计不清理已软删文档的 blob。
- 未发现损坏引用（DB 有路径但磁盘无文件），说明当前文件与记录基本一致。

## 回滚

把隔离目录中的文件移回 `data/files/` 或 `data/covers/` 对应位置即可；`manifest.json` 记录了每个文件的原始路径。DB 备份未使用，可按需删除。
