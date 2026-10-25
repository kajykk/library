# P3-2 WAL checkpoint 审计报告

日期：2026-10-26
脚本：[server/scripts/wal_checkpoint.py](../server/scripts/wal_checkpoint.py)
状态：已入库脚本；**未对真实数据库执行 `--apply`**（需单独授权）

## 概要

dry-run 以只读 URI（`file:...?mode=ro`）打开数据库，仅读取 PRAGMA 与文件状态，不执行 checkpoint。

| 项目 | 数值 |
|---|---:|
| 主库大小 | 729,640,960 bytes |
| WAL 大小 | 19,207,472 bytes |
| SHM 大小 | 32,768 bytes |
| journal_mode | wal |
| page_size | 4096 bytes |
| wal_autocheckpoint | 1000 pages |
| WAL 待合并估算 | 4,662 frames |

WAL 约占主库的 2.6%。按 1000 页自动 checkpoint 阈值计算，当前未合并帧远高于阈值；通常说明长事务或只读连接持续阻止 checkpoint 完成。

## 脚本行为

```bash
# 预览（默认 dry-run，只读）
python server/scripts/wal_checkpoint.py

# JSON 输出
python server/scripts/wal_checkpoint.py --json

# 执行 checkpoint（需单独授权；先停止 uvicorn 等写入进程）
python server/scripts/wal_checkpoint.py --apply
```

`--apply` 流程：

1. 备份 `knowledge_base.db` 及 `-wal` / `-shm`；
2. 打开读写连接，设置 `busy_timeout=5000`；
3. 执行 `PRAGMA wal_checkpoint(TRUNCATE)`；
4. 记录 `busy` / `log_frames` / `checkpointed_frames` 与 checkpoint 后文件大小；
5. 若 `busy=1`，说明仍有连接占用，WAL 可能无法完全截断，脚本提示停止写入进程后重试。

## 隔离自测

在临时目录构造 65,952 bytes WAL（16 frames）的小型数据库并执行脚本：

- dry-run 未改变主库 mtime，WAL 保持不变；
- `--apply` 返回 `busy=0`，WAL 截断为 0 bytes，主库 mtime 发生变化；
- 自动生成主库 + `-wal` + `-shm` 备份；
- 第二次 `--apply` 在空闲状态下正常完成。

真实数据库未执行 `--apply`，dry-run 前后主库与 WAL/SHM 的 size、mtime_ns 完全一致。

## 发现与未处理

- WAL 长期维持 19 MB 且帧数远超 autocheckpoint 阈值，提示可能有长连接（开发服务、脚本或只读连接）阻止 checkpoint。`--apply` 前必须停止所有写入与长读连接，否则只能得到 `busy=1`。
- 备份采用文件复制，必须在停写状态下进行；服务运行中直接复制可能得到不一致的 DB/WAL 组合。
- 本脚本不修改 `journal_mode`、不调整 autocheckpoint 配置，也不执行 `VACUUM`，避免超出本批范围。
- `.gitignore` 已新增 `server/*.db.bak.*`，备份不会被误提交。

## 回滚

停止服务后，以备份的 `.bak`、`.bak-wal`、`.bak-shm` 覆盖对应文件。checkpoint 本身只合并 WAL 内容，不改变逻辑数据。
