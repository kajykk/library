# R8b 前置测量 · bigram 索引（2026-10-27，只读，未申请 G3）

> 任务书：[R8-chinese-search.md](../R8-chinese-search.md) 第 3 节 RW-807。本轮**不创建迁移、不装依赖、不写真实库**，也不申请 G3；本页只出数字与请示要点。
> 五个测量项按任务书原序：① 索引体积增量 ② 全库重建耗时 ③ 2 字词查询 P50/P95 ④ 重建期间峰值内存 ⑤ ≥3 字词的短语召回正确性。此外补测了任务书没写、但决定 R8b 能不能上线的**索引维护路径**（§7）和一条**数据完整性缺陷 F-17**（§8）。

## 0. 结论摘要

| 测量项 | 现状（trigram） | bigram | 判定 |
|---|---|---|---|
| ① 体积·同形状可比 | contentless 副本 668.1 MiB | contentless 副本 **255.3 MiB** | **−61.8%** |
| ① 体积·可维护形状 | 普通 fts5 副本 950.9 MiB | 普通 fts5 **755.6 MiB** | **−20.5%** |
| ① 体积·生产真实口径 | 生产 `documents_fts` = **954.8 MiB**（占库 75.4%） | 同上 755.6 MiB | −199 MiB |
| ② 全库重建 | 113.6 s | **74.9 s**（Python 展开 24.5 s） | −34% |
| ③ 2 字词 P95（14 词） | LIKE 4.03–**538.78** ms | **≤1.53 ms** | 罕见短词那堵墙拆掉 |
| ④ 重建峰值内存 | 171 MB | **173 MB** | 同级，不是瓶颈 |
| ⑤ ≥3 词短语召回 | — | CJK 6 词里 5 词与真值完全相等，第 6 词「多 1 篇」是 F-17 盲区所致 | 滑窗**没有错召** |
| ⑤ 拉丁词召回 | 子串语义（`hard` 命中 Richard） | **词边界语义**（不再命中 Richard） | q13 单批 −179 篇 → **行为改变，需 RD-K** |

三条必须先讲清楚的坏消息：

1. **任务书设想的 contentless 形状在当前 SQLite 上不能维护。** 本机与 `server/.venv` 都是 SQLite **3.42.0**，`contentless_delete=1` 需要 ≥3.43，实测建表报 `unrecognized option: "contentless_delete"`，普通 `DELETE` 报 `cannot DELETE from contentless fts5 table`。所以 255.3 MiB 那一行只在「每次改动都全库重建」的假设下成立；真正可逐行维护的是普通 fts5 表（正文再存一份）→ 755.6 MiB。
2. **SQL 触发器写不出这个分词器。** 用递归 CTE 在触发器里做 2 字滑窗，单行 INSERT 实测 **0.8–2,455.8 ms**（20 万字级 ≈ 4–7 s），上传路径不可用；应用侧 Python 维护同一行只要 **≈64 ms**（§7）。R8b 的工程量重心从「加一张表」移到「索引维护改由应用负责」。
3. **bigram 会把英文搜索从子串改成整词**（§6.3）。这不是性能问题，是召回语义变更，得用户决定。

## 1. 环境与可复现命令

| 项目 | 值 |
|---|---|
| 解释器 | `E:/zy/python/python.exe`（3.12.0，sqlite3.sqlite_version = **3.42.0**）；`server/.venv` 同为 3.42.0 |
| 源库（只读） | `server/scripts/spike-out/spike.db`，1,329,700,864 B，SHA-256 前缀 `77f41abb7fb12724`，探针记录的前后哈希相同（`source_untouched: true`） |
| 语料 | documents 1,357 行 / 未软删 **1,283** 行；正文总字符 `length()` 合计 **140,537,774** |
| 真实库 | `server/knowledge_base.db` **未被打开过**；`server/data/` 零写入 |
| 产物（gitignore） | `server/scripts/spike-out/bigram/{bigram,trigram_ref,bigram_full,trigram_ref_full}.db` + `measure.json` `measure_r15.json` `measure_full.json` |

```bash
cd E:/code/projects/library/server

# contentless 形状（默认，体积口径）：建两张索引 + 全库重建 + 查询延迟
E:/zy/python/python.exe scripts/probe_bigram_index.py \
    --db scripts/spike-out/spike.db --work-dir scripts/spike-out/bigram --runs 5 \
    --out scripts/spike-out/bigram/measure.json

# 同一批索引复用、把 runs 提到 15（延迟以这份为准，理由见 §5）
E:/zy/python/python.exe scripts/probe_bigram_index.py \
    --db scripts/spike-out/spike.db --work-dir scripts/spike-out/bigram --runs 15 --skip-build \
    --out scripts/spike-out/bigram/measure_r15.json

# 普通 fts5 形状（可逐行删改，§3 的可维护口径）：输出文件加 _full 后缀，不覆盖上一轮
E:/zy/python/python.exe scripts/probe_bigram_index.py \
    --db scripts/spike-out/spike.db --work-dir scripts/spike-out/bigram --runs 5 --contentless 0 \
    --out scripts/spike-out/bigram/measure_full.json
```

生产 `documents_fts` 真实字节（§3 那行 954.8 MiB）来自 `e2e-r8/fts_footprint.py`：`spike.db` 复制到 `e2e-r8/size_probe.db` → `VACUUM` 记 1,327,435,776 B → `DROP TABLE documents_fts` + `VACUUM` 记 326,221,824 B → 差 **1,001,213,952 B**。辅助脚本（`e2e-r8/`，gitignore，不作交付物）：`recall_diff.py`（召回差逐行定位）、`three_way.py`（生产 trigram / LIKE / bigram 三方集合）、`damaged_rows.py`（NUL 行普查）、`nul_blindness.py`、`nul_probe_overlap.py`（15 条探针查询与 NUL 盲区的交集）、`recall_delta.py`（按 `_route` 口径的整句召回差）、`write_amplification*.py`（§7 两条维护路径）。测量用的大副本（`size_probe.db` 326 MB、`ui.db` 1.33 GB、`wa*.db`、`mini.db`、`bigram-mini/`）已在签核前删除，`e2e-r8/` 现存 **167 KB** 全部是脚本与日志，上述数字用本文档 §1 的命令可重跑复现。

## 2. 索引形状与命名偏离

任务书写的是 `documents_fts2`，实测建的是 `documents_bigram_fts`（bigram 侧）与 `documents_trigram_fts`（同形状 trigram 对照，用来证明「不是 contentless 帮 bigram 说话」）。表名不影响测量，但会影响迁移文案，**登记为待决**：G3 若放行，用任务书的 `documents_fts2` 还是沿用探针名。

```sql
CREATE VIRTUAL TABLE documents_bigram_fts USING fts5(
    title, author, description, content,
    tokenize = 'unicode61',
    content=''            -- 或去掉这一行 = 可逐行删改的普通 fts5 表
);
```

- 写入前在应用侧展开：**CJK 连续段 → 2 字滑窗、空格拼接；非 CJK 段整段作一个 token**（`持续改善` → `持续 续改 改善`；`RLHF对齐` → `RLHF 对齐 齐`）。展开后字符量 524,216,199 B（原文四列 295,784,055 B），膨胀 1.77×。
- 查询侧短语 = 相邻二分词：`机器学习模型` → `"机器 器学 学习 习模 模型"`，只有正文里真的连续出现该串的行才命中；这与 R8a 的 `_route`（≥3 字走 FTS、2 字走 LIKE）互斥，R8b 若落地则整条查询都走索引。
- 排序仍用 `bm25(...)`，测量里不加 `LIMIT`（要的是全量召回的耗时上界）。

## 3. ① 索引体积

| 形状 | 行数 | 文件字节 | MiB | 相对现状 |
|---|---|---|---|---|
| 生产 `documents_fts`（trigram，普通表，含软删行） | 1,357 | 1,001,213,952 | **954.8** | 1.00× |
| trigram 副本（普通表，只索引未软删） | 1,283 | 997,044,224 | 950.9 | 0.996× |
| trigram 副本（contentless） | 1,283 | 700,579,840 | 668.1 | 0.70× |
| **bigram 副本（普通表，可逐行删改）** | 1,283 | **792,260,608** | **755.6** | **0.79×** |
| bigram 副本（contentless，3.42 下无法增量维护） | 1,283 | 267,657,216 | 255.3 | 0.27× |

- 同一形状横比才公平：普通表口径 **755.6 vs 950.9 MiB（−195 MiB，−20.5%）**；contentless 口径 **255.3 vs 668.1 MiB（−61.8%）**。
- 若 R8b 保留 trigram 只为兜英文子串（RD-K 的 B 案），磁盘是 **950.9 + 755.6 ≈ 1,706 MiB**（现库 1,266 MiB 的 1.35 倍），不是净省。
- 库文件里 trigram 占 75.4%，说明「索引体积」在这台机器上就是「库体积」，新增索引前必须写清磁盘口径。

## 4. ② 重建耗时与 ④ 峰值内存

| 索引 | 全库重建 | 其中 Python 展开 | 峰值 RSS（单进程，50 ms 采样） |
|---|---|---|---|
| bigram（contentless） | **73.8 s** | 25.1 s | 171 MB |
| bigram（普通表） | 74.9 s | 24.5 s | 173 MB |
| trigram 副本（contentless / 普通表） | 112.6 s / 113.6 s | 0（SQL 直接灌原文） | 164 MB / 171 MB |

- 外推口径：1,283 行 / 1.4 亿字，平均 58 ms/篇；`MAX_CHARS=200_000` 封顶后，最坏单篇全量重建 ≈64 ms（§7 实测）。
- 内存不是门槛：整轮重建 RSS 峰值 173 MB，与 B5 那条 500 MB 线不在一个量级（对比 R7 的 FAIL 项）。
- 重建期间是否要停写、是否需要 `OPTIMIZE`，属 R8b 工程细节，本轮未测（在只读副本上测不到并发写入）。

## 5. ③ 2 字词延迟（R8b 要买的那堵墙）

`runs=15`，同一进程内连续 15 次取分位（`runs=5` 时 P95 恰等于 max，会被冷页缓存首触污染：起卦那次 P95=62.61 ms 而 P50=0.09 ms；15 次后 P95=0.13 ms。两个数都记在这里，验收以 15 次为准）。

| 词 | bigram P50 | bigram P95 | 现状 LIKE P50 | 现状 LIKE P95 | 全库命中 |
|---|---|---|---|---|---|
| 起卦 | 0.05 | 0.13 | 507.45 | **538.78** | 5 |
| 断卦 | 0.05 | 0.09 | 505.06 | **526.89** | 6 |
| 权力 | 0.57 | 0.63 | 20.77 | 21.24 | 364 |
| 腐败 | 0.23 | 0.35 | 80.40 | 83.31 | 155 |
| 本能 | 0.56 | 0.61 | 17.77 | 22.52 | 431 |
| 冲突 | 0.60 | 0.68 | 11.84 | 12.53 | 436 |
| 民主 | 0.48 | 0.78 | 18.92 | 20.52 | 309 |
| 张力 | 0.20 | 0.23 | 108.47 | 115.44 | 139 |
| 取代 | 0.61 | 0.70 | 15.45 | 16.27 | 449 |
| 工作 | 1.28 | 1.48 | 3.36 | 3.88 | 639 |
| 孤独 | 0.49 | 0.53 | 17.38 | 18.46 | 370 |
| 创伤 | 0.37 | 0.55 | 60.24 | 73.14 | 281 |
| 需要 | 1.31 | **1.53** | 3.44 | 4.03 | 658 |
| 哲学 | 0.76 | 1.24 | 10.93 | 11.17 | 405 |

- 14 词全部 ≤1.53 ms；trigram 索引对同一批词恒 **0 命中**（结构性，E10），所以这一列不是「快但空」的对照。
- 对应到 R8a 的实测端点：q10「六爻纳甲／起卦／断卦」`/search` **828.8 ms**、`/search/summary` **829.9 ms**，是 R8a 唯一没打下的延迟线（任务书第 4 节「罕见短词不设线，但必须点名」）。bigram 侧同样两个词的索引查询是 0.13 / 0.09 ms → **该条查询的成本上限从「扫 1.4 亿字正文」变成「两次索引查找」**。
- 短词命中数 > `limit` 的那一类（权力/腐败/本能…，R8a 靠提前终止压到 ≤100 ms）也在同一张表里变快，但收益远没有罕见词大。

## 6. ⑤ 短语召回正确性

### 6.1 ≥3 字 CJK：与 LIKE 真值逐词对照

| 词 | LIKE 真值 | bigram | trigram 副本 | bigram P50/P95 | trigram P50/P95 |
|---|---|---|---|---|---|
| 政治哲学 | 65 | 65 ✓ | 65 ✓ | 0.36/0.42 | 0.18/0.25 |
| 机器学习 | 63 | 63 ✓ | 63 ✓ | 0.35/0.48 | 0.18/0.21 |
| 弗洛伊德 | 177 | 177 ✓ | 177 ✓ | 0.63/0.91 | 0.51/0.59 |
| 精神分析 | 134 | 134 ✓ | 134 ✓ | 1.03/1.14 | 0.51/0.62 |
| 人工智能 | 151 | 151 ✓ | 151 ✓ | 0.78/0.90 | 0.57/0.63 |
| 第一次世界大战 | 120 | **121** | 120 | 1.27/1.42 | 0.52/0.73 |

6 词里 5 词三方完全相等 → **滑窗短语没有引入错召**（相邻性约束足够）。第 6 词多的那 1 篇是 `rowid=2`（F-17 的 NUL 行），不是 bigram 的错，见 §6.2。

### 6.2 与 LIKE 的差集 100% 落在破损行

20 个词（14 短 + 6 长）逐词做**集合**差（不只比条数）：bigram 多出的条目合计 **7 个「词×行」**，涉及且仅涉及 3 篇（`rowid` 2 / 536 / 1302）；**bigram 少 0 篇，20 个词的 `missing` 全部为空**。

| 词 | bigram | LIKE | 多出的 rowid | 该篇为何 LIKE 不中 |
|---|---|---|---|---|
| 冲突 | 437 | 436 | 1302 | 正文含 NUL，`length()`=28,571 而真实 200,000 字 |
| 取代 | 451 | 449 | 2、1302 | 同上（`rowid=2` `length()`=71,256 / 真实 100,113） |
| 工作 | 640 | 639 | 536 | 含 NUL，`length()`=75 / 真实 2,388 |
| 需要 | 660 | 658 | 536、1302 | 同上 |
| 第一次世界大战 | 121 | 120 | 2 | 同上 |

其余 15 个词三方集合完全相同。三方核对（`three_way.py`，以 `doc_id` 回 `documents` 表 join 并过滤未软删）：生产 trigram = LIKE = **120 篇**、逐行相同；bigram = 121 篇，唯一多出的 `rowid=2` 里 `第一次世界大战` 确实出现 1 次（`instr(content, term)=76290`，位置在 NUL 之后）。→ **是 LIKE 与 trigram 双盲，不是 bigram 过召**。

### 6.3 拉丁词：子串 → 词边界（行为变更）

`unicode61` 把 `hard` 当成一个整词，`trigram` 把它当 3 字子串序列。同一批文档：

| 查询 | 现状（trigram 子串） | bigram（整词） | 差 |
|---|---|---|---|
| `hard` | 599 | 328 | **−271** |
| q13 三词并集（hard/problem/consciousness） | 657 | 478 | −179 |
| q14 四词并集（introverts/need/quiet/time） | 676 | 574 | −102 |

−271 那批里最高频的词形是 `richard`(52)、`richardson`(8)、`hardt`(6)、`hardy`(4)、`hardin`(3)、`hardwired`(2)、`reinhard`(2)…——**今天的英文搜索把「搜 hard 出 Richard」当成召回**。bigram 之后这条没了（精度更好，但用户输入 `introverts` 也搜不到只写 `introvert` 的书）。

15 条探针查询按 R8a 的 `_route` 口径（≥3 字走 trigram FTS、2 字走 LIKE）取并集，与 bigram 并集对照（`recall_delta.py`）：

| 查询 | 现状召回 | bigram 召回 | 只 bigram 有 | 只现状有 |
|---|---|---|---|---|
| q01 弗洛伊德…冲突 | 543 | 544 | 1 | 0 |
| q02 精神分析…创伤 | 134 | 134 | 0 | 0 |
| q03 权力…腐败 | 372 | 372 | 0 | 0 |
| q04 民粹主义…民主 | 41 | 41 | 0 | 0 |
| q05 第一次世界大战…爆发 | 368 | 368 | 0 | 0 |
| q06 人工智能…取代…工作 | 644 | 645 | 1 | 0 |
| q07 机器学习…过拟合 | 12 | 12 | 0 | 0 |
| q08 数字时代…孤独 | 43 | 44 | 1 | 0 |
| q09 资本主义…张力 | 376 | 376 | 0 | 0 |
| q10 六爻纳甲／起卦／断卦 | 8 | 8 | 0 | 0 |
| q11 国家权力／合法性 | 154 | 154 | 0 | 0 |
| q12 大模型对齐／需要／RLHF | 666 | 668 | 2 | 0 |
| q13 hard/problem/consciousness | 657 | **478** | 0 | **179** |
| q14 introverts/need/quiet/time | 676 | **574** | 0 | **102** |
| q15 政治哲学 | 65 | 65 | 0 | 0 |
| **合计** | | | **5**（全是 NUL 破损行） | **281**（全来自两条英文查询） |

13 条中文查询的召回集合与现状**逐条相等**（q10 也相等：8 篇，只是那 828.8 ms 变成 ≤0.13 ms）。

## 7. 索引维护路径（任务书没写，但决定 R8b 可行性）

| 路径 | 单行成本（5 篇样本） | 能否覆盖 NUL 之后的文本 | 结论 |
|---|---|---|---|
| SQL 触发器 + 递归 CTE 滑窗展开 | rowid 1302（SQL 可见 28,571 字）**556.6 ms**；rowid 2（71,256 字）**2,455.8 ms**；rowid 3（SQL 可见 3 字）1.3 ms；514 0.8 ms → **≈20–35 ms/1k 字** | **不能**：SQL 的 `length()` 在 NUL 处截断，`MATCH '需要'` 只回 `rowid=2`，不含 536 | 20 万字书 ≈4–7 s，上传/重提取路径不可用；且复刻 F-17 盲区 |
| 应用侧维护（Python 展开 + 普通 fts5 逐行 DELETE+INSERT） | 1302 全行重建 **41.8 ms**（另一次拆分口径 25.6 展开 + 37.9 写索引 = 63.5 ms，取保守 **≈64 ms**）；rowid 2 35.6 ms；3 6.1 ms；536 3.1 ms；514 0.9 ms | **能**：`MATCH '需要'` 回 `[2, 536, 1302]` | 与一次 SHA-256 计算同量级，可放进上传/保存事务之后 |
| contentless 表 + 全库重建 | 74.9 s / 次 | 能 | 每次增删改都重建，只在「批量重提取」窗口里说得通 |

前置事实：本机与 `server/.venv` 的 SQLite 都是 **3.42.0**，`contentless_delete=1` 直接报 `unrecognized option`，contentless 表 `DELETE` 报 `cannot DELETE from contentless fts5 table`。要在 3.42 上逐行维护，索引只能是**普通 fts5 表**（也就是 §3 那行 755.6 MiB 而非 255.3 MiB）；想用 contentless 的体积优势就得先升 SQLite（新依赖，超出 R8b「不装依赖」的范围）。

## 8. 顺带查出的数据完整性缺陷（登记为 F-17）

`damaged_rows.py` 全库普查：**1,283 篇未软删文档里 11 篇（0.86%）的 title/author/description/content 含 NUL 字节**（rowid 2、3、109、229、257、301、514、536、720、744、1302），无一例非法 UTF-8（严格解码全通过；rowid 1302 里的 `U+FFFD` 是入库时就写进去的替换符，不是编码坏）。

SQLite 对这种行的行为不一致，实测三点：`length(content)` 在 NUL 处截断（`rowid=3` 报 3、真实 50,860）、**`LIKE` 静默不匹配 NUL 之后的内容**、`instr()` 与 Python 读到的驱动侧字符串都是全文。FTS5 trigram（C 侧按声明长度分词）**同样盲**——生产 `documents_fts` 与 LIKE 的命中集合逐行相同（§6.2）。

影响：R8a 的 2 字词 LIKE 通道、RD-J 的精确计数、以及 ≥3 字的 trigram 通道，对这 11 篇都是漏的；`_content_window_expr` 用 `instr` 定位窗口，所以「已经返回的行」里 snippet 不受影响。`nul_probe_overlap.py` 用真实 `build_query_plan` 词表比对：15 条探针查询里 **10 条**在 NUL 之后的文本中至少有一个查询词命中（q01/q02/q03/q05/q06/q08/q09/q12/q13/q14），是否进 top-5 另说，但「搜不到」的成因里除了 F-15 还有这一条。这是 F-8 的扩展（F-8 只记了《很杂很杂的杂学知识》那 1 本，即本处的 `rowid=229`）。修复位置在入库归一化（写入前剥 NUL），不在搜索层。

## 9. G3 请示要点（本轮不申请，等 RD-K/RD-L 有答案再提）

1. **迁移**：`0011_bigram_fts`（Alembic head 现为 `0010_reading_queue_reviews`）。内容 = 建 `documents_bigram_fts`（普通 fts5 表，四列，`tokenize='unicode61'`）+ 应用侧维护入口；**不建触发器**（§7）。
2. **耗时**：全库回填 74.9 s（1,283 篇 / 1.4 亿字，含 24.5 s 分词展开）；单篇增量 ≈64 ms。
3. **磁盘**：bigram 替换 trigram 并删旧表 → **−199 MiB**（755.6 换 954.8）；保留 trigram 兜英文子串（RD-K 的 B 案）→ **+755.6 MiB**，库体积 1.35 倍。申请时必须二选一写清楚。
4. **回滚**：`DROP TABLE documents_bigram_fts` + 删除应用侧写索引的调用点；`documents` 表零改动，回滚后再按 `search_index.py` 的重建路径回填 trigram（113.6 s）即可复原。R8a 的查询侧改动不依赖 bigram，回滚后搜索仍可用（罕见 2 字词退回 828 ms 那条线）。
5. **验收线建议（数字来自本页，等用户定）**：2 字词索引查询 P95 ≤5 ms；q10 端点 `/search` ≤200 ms；13 条中文查询召回集合与现状逐条相等；英文查询按 RD-K 的口径另立一条。
6. **先决**：RD-K（拉丁词边界 vs 子串）、RD-L（索引维护路径：应用侧普通表 / contentless + 全库重建 / 升 SQLite）、F-17 是否同批处理。三者未决 → 不提交 G3 申请。

## 10. 偏离与局限

1. 表名与任务书不同（§2）。
2. 任务书只要求测 contentless 式索引，本轮加了**普通 fts5**与**同形状 trigram 对照**两个变体，因为 §7 的发现否定了默认形状。
3. 延迟数字取自只读副本、单进程、无并发写入，且不通过 FastAPI 端点（是纯 SQL 索引查找的耗时上界）；R8a 端点侧的 P95 见 [../R8-chinese-search.md](../R8-chinese-search.md) §5。
4. 相关性门槛没在 bigram 上重判：13/15 条查询召回集合与现状相等（§6.3），top-5 排序会变（bm25 词频定义随分词改变），属 R8b 开工后的验收项。
5. 未测 `OPTIMIZE`、未测并发写入下的重建、未测 PostgreSQL 分支（`search_index.py` 在非 SQLite 方言直接返回不可用）。
