# R8 · 中文关键词检索（修 F-15）

> 2026-10-27 用户定：**分两批**——R8a 只做查询侧（零门禁、零迁移、零依赖），R8b 是否加 bigram 索引由 R8a 期间的实测再单独请示 G3。
> R7 遗留的 spike 盘（`.spike-venv` 175 MB + `spike-out` 1.6 GB）**先留着**，等 R8 测完再删。
>
> 硬约束继承 [00-execution-rules.md](00-execution-rules.md)：禁止 `git add -A`/`--amend`/`--no-verify`；每批单独提交；测试用临时库；真实库与 `server/data/` 零写入；G1–G5 门禁不变。
> 进度写回 [progress.md](progress.md)；本文件是 R8 的任务书与证据槽。

## 1. 实测依据（2026-10-27 两轮只读探针，库副本 `mode=ro&immutable=1`）

库基线：存活 1,283（含 note）/ book+pdf 1,282、FTS 1,357 行、**正文总量 139,570,888 字符**。

> 下表数字来自已入库的测量脚本 [`server/scripts/probe_search_cost.py`](../../server/scripts/probe_search_cost.py) `--mode baseline`，
> 复现命令：`cd server && E:/zy/python/python.exe scripts/probe_search_cost.py --db scripts/spike-out/spike.db --mode baseline --runs 5 --out scripts/spike-out/r8-baseline.json`。
> （`server/scripts/spike-out/` 已被 [.gitignore:46](../../.gitignore#L46) 忽略，所以原始 JSON 是本地产物，**进 git 的证据是上面这条命令 + 本表的数字**。）每条写法取 3 次中位。
> **2026-10-27 RW-801 复核后修正**：原 E3/E5/E7 把「慢」主要归因于 `lower()` 与 `content` 列，实测归因错误——真正的主因是 `ORDER BY CASE` 取消了 `LIMIT` 提前终止（见 E4′）。

| # | 事实 | 实测 |
|---|---|---|
| E1 | **整句中文查询必然 0 命中**：无空格的句子 = 1 个 ≥3 字 term → 走 `_search_fts` 的整句短语匹配，trigram 要求逐字连续 | 11 条纯中文样本句 `hits=0`，耗时 0.1–1.2 ms（快而空）；`e1b_terms_fts_and` 同样 0 命中 |
| E2 | **现状全链路**：手工空格切成 2 字词 → 四列 `lower() LIKE` 全表 + `ORDER BY CASE` 二次求值 | 15 条查询 `e2` = **699–1,491 ms**（q03 最低 699.4，q12 最高 1,491.3） |
| E3 | `SQLAlchemy.ilike()` 在 SQLite 展开为 `lower(col) LIKE lower(?)`，对 1.4 亿字符正文逐行造小写拷贝；CJK 词改大小写敏感 `LIKE` 等价 | 同样带 `ORDER BY CASE`，`e2`→`e3` = 699→517、1,491→1,153、1,152→853 ms（**≈ −28%～−31%**）；省的是系数，不是量级 |
| E4 | **主因是 `ORDER BY CASE`**：它对每行重复求值三个 LIKE 表达式，且排序要求拿到**全部**匹配行 → `LIMIT 20` 的提前终止失效 | 同一 WHERE 去掉 ORDER BY（`e3`→`e4`）：**12/15 条从 517–1,152 ms 降到 10–86 ms（19～66 倍）**。q01 670.1→**10.2**、q11 672.2→**12.2**、q15 675.0→**14.0** |
| E4′ | 去掉 ORDER BY 后剩余成本 ≈ 全表扫描 × `limit / 总匹配数`：**罕见词天然无法提前停止** | `e4` 的 3 个残值：q07 206.1 ms（全库匹配 91 本）、q12 270.5 ms（67 本）、**q10 1,134.2 ms（11 本 < limit，必须扫完）** |
| E5 | 廉价列（title/author/description）扫描极快；`content` 只在「必须扫完」时才贵 | `e5_meta_only` = **0.8–6.7 ms**（15 条全 <7 ms）；`e5_content_only` = 10.1–1,123.5 ms ≈ `e4`（两者叠加无额外成本，说明元数据谓词不是瓶颈） |
| E6 | SQLite 的 `OR` 按谓词顺序**逐行短路**：元数据先命中的行根本不读正文 | 同样 LIMIT 20 且不排序，元数据命中密度高的 q11/q15 `e4` = 12.2/14.0 ms vs 只能靠正文命中的 q07/q10 = 206/1,134 ms |
| E7 | 混合查询用 ≥3 字词先走 FTS 取候选、短词只在候选集内精筛 | `e7_fts_prefilter` 候选集小时 **4.0（q12）/ 8.2（q13）/ 8.8（q14）ms**；候选集大时回落 434–802 ms（q08 434.1、q11 555.2、q05 621.0、q01 563.2、q07 802.2）；纯短词无候选可筛 → `null` |
| E8 | **FTS 侧 `OR` 遇到高频词会退化**（单次 MATCH 候选爆炸），不能靠一条大 OR 表达式 | 长词集合一条 OR：q01 83.4、q06 69.5、q13 79.2 ms；q13 在 R7 的整句 OR 形态曾测到 934 ms。逐词单查 + Python 合并是安全形状 |
| E9 | **`_search_fts` 用 AND、`_search_ilike` 用 OR**：同一条查询因是否含短词而走不同路径，结果集语义不一致 | [search.py:77](../../server/app/routers/search.py#L77) `" AND ".join(...)` vs [:120-127](../../server/app/routers/search.py#L120-L127) `or_(...)` |
| E10 | 正文命中远多于元数据命中：**「短词不搜正文」不是可行降级** | `e10_total`：「弗洛伊德 文明 本能」全列 521 本 / 元数据仅 17 本；「政治哲学 政治 哲学」全列 **536** / 元数据 86；「权力 腐败」全列 372 / 元数据 7 |
| E11 | `_search_ilike` 用 ORM 整行载入（含 200k 字 content）只为算 snippet | snippet 段 18–74 ms，单请求最多把 20×200k 字符搬进 Python |
| E12 | **`/api/search` 没有一条「行为级」测试**（原写「0 条」不准确，见下）：只有 4 条顺带断言，且全部用现成好打的查询 | [test_api.py:584](../../server/tests/test_api.py#L584) `test_search_and_stats`（标题命中/snippet 含词/type 过滤/空串 422）、[:618](../../server/tests/test_api.py#L618) `test_search_index_sync`（增删改后索引同步）、[:647](../../server/tests/test_api.py#L647) `test_search_two_char_query_ilike_fallback`（2 字词兜底）、[:1297](../../server/tests/test_api.py#L1297) `test_search_summary_endpoint`（形状）。**零覆盖**：整句查询、`limit` 下界、`summary` 与 `hits` 口径一致、FTS 不可用/异常回退、snippet 220 字上限 |
| E13 | `extract_keywords`（[text_analysis.py:21](../../server/app/services/text_analysis.py#L21)）是 2 字滑窗 + 频次，直接用于**查询**会产出「什么/如何/为什/伊德」噪声词 | 探针 #2 的 rewritten 列 |
| E14 | **搜索页 `Promise.all` 同时等 `/search` 与 `/search/summary`**：只快 hits 不动 summary，用户感知延迟不变 | [src/app/search/page.tsx:103-106](../../src/app/search/page.tsx#L103-L106)；`summary.total` 又直接显示为「共 N 条结果」([:250](../../src/app/search/page.tsx#L250)) |
| E15 | **FTS 排序查询里的 `JOIN` 是独立的第三个坑**：`ORDER BY bm25` 要拿到全部匹配行才排序，JOIN 就把「回表取列」摊到每一行上；改成「先只取 doc_id+score，再按主键回表取列」后，回表只剩 `limit` 行 | `e2e-r8/fts_shape.py`（5 次取中位）高频词：`consciousness` 88.5→26.2 ms、`problem` **143.5→24.2**、`hard` **217.6→22.8**、`time` 194.7→24.4、`弗洛伊德` 128.9→33.9 ms；只取 id 但仍 JOIN 的人造形状（`split_joined`）依旧 125–172 ms → **贵在 JOIN，不在投影列** |

**结论**：F-15 不是一个 bug，是四个——① 整句查询打空（E1，「搜不到」）；② `ORDER BY CASE` 取消提前终止 → 全表读正文（E4′，「慢」的主因），FTS 侧的同族问题是排序里带 `JOIN`（E15）；③ `lower()` 拷贝（E3，约 −30% 的次要因子）；④ 两条路径 AND/OR 语义不一致（E9）。①③④ 都能在查询侧解决或显著缓解，②也能解决大部分（常见词 10–86 ms）；**查询侧解决不了的是「罕见 2 字词必须扫完整表」**（E4′ 的 q10 = 1,134 ms，trigram 只登记 ≥3 字符 token，E10），这就是 R8b 的 bigram 索引要买的东西。

## 2. R8a 范围

只碰：`server/app/services/query_terms.py`（新增）、`server/app/routers/search.py`、`server/tests/test_query_terms.py`（新增）、`server/tests/test_search_api.py`（新增）、`docs/roadmap/*`、`server/scripts/probe_search_cost.py`（新增，测量脚本入库）。

不碰：`search_index.py`、`alembic/versions/*`、`models.py`、`requirements*.txt`、`src/**`（前端不改调用方式）、真实库、`server/data/`。

> `src/**` 的唯一潜在例外是 RD-J=A 时 [page.tsx:250](../../src/app/search/page.tsx#L250) 把「共」改成「约」的一行文案；未签则不动前端。

## 3. 任务

### RW-801 测量脚本入库（RD-04 纪律）

- **文件**：新增 `server/scripts/probe_search_cost.py`（合并本轮 `e2e-r8/search_cost.py` 与 `e2e-r8/query_shapes.py`，去掉 hacky 分支）。
- **要求**：默认只读（`file:<path>?mode=ro&immutable=1`），库路径由 `--db` 传入且**默认指向 `knowledge_base.db` 时必须再带 `--i-know-this-is-the-real-db` 才允许连**，否则拒绝；输出 JSON 到 stdout。`--mode after` 直接调应用层的 `/search`、`/search/summary` 端点函数（不重启用户进程），用于 RW-803 之后的同批重测。
- **验收**：`git check-ignore -v server/scripts/probe_search_cost.py` 退出 1（未被忽略）；脚本对 `spike-out/spike.db` 跑通并复现 §1 的 E1/E2/E3/E4/E5/E7/E8/E10 八项数字（允许 ±15% 抖动）。
- **回滚**：`git revert`；脚本全程只读。
- **状态（2026-10-27）**：脚本已落地，`--mode baseline` 已跑通并产出 §1 全部数字（含把 E4′ 这条新发现暴露出来的 `e4_like_no_order`）；`--mode after` 依赖 RW-802/803 的模块，届时再验。

### RW-802 查询关键词生成（纯函数，零依赖）

- **文件**：新增 `server/app/services/query_terms.py`；新增 `server/tests/test_query_terms.py`。
- **签名**（不得改名）：

  ```python
  @dataclass(frozen=True)
  class QueryPlan:
      raw: str
      terms: tuple[str, ...]      # 有序、去重、最多 6 个
      used_fallback: bool         # 是否走了「长段 3 字滑窗」降级

  def build_query_plan(q: str, max_terms: int = 6) -> QueryPlan
  ```

- **规则**（按序执行，全部可单测）：
  1. `raw = q.strip()`；全角标点与 ASCII 标点统一替换为空格（**用显式 `_SEPARATORS` 字符表**，不用 `\W`，词内 `-`/数字/`.` 要保留，如 `RLHF`、`C++`）。
  2. 空格切段；拉丁段（`^[A-Za-z0-9_.+-]+$`）长度 ≥2 直接成词（保持原大小写，由查询侧决定是否 `ILIKE`）。
  3. CJK 段长度 **2–3** → 整段成一个词。
  4. CJK 段长度 **≥4** → 按虚词边界表切分：单字虚词 `的了是在和与及或会让被把对从由于以而就也都吗呢吧啊之其这那人们它有不为很把` + 多字虚词 `为什么 什么是 如何 怎么 哪些 哪里 多少 是否 可以 而且 并且 或者 之间 来自 属于 一个 这个 那个`；保留长度 ≥2 的片段，长度 1 的丢弃。
  5. 第 4 步若无任何 ≥2 字片段 → 保留原段（宁可打空也不篡改）。
  6. 排序：出现顺序优先，长度降序作次键；去重；截断 `max_terms`。
- **硬断言（样本句必须逐条满足）**：
  - 「权力为什么会让人腐败？」→ terms 含 `权力` 与 `腐败`；
  - 「什么是政治哲学？」→ terms 含 `政治哲学`（**不得**被拆成 政治/哲学，4 字段内无虚词就保持整体，这样 trigram 能精确命中）；
  - 「机器学习模型如何避免过拟合？」→ terms 含 `机器学习`、`过拟合`；
  - 「大模型对齐为什么需要 RLHF？」→ terms 含 `RLHF`；
  - 「？」/「。」/全空格 → `terms == ()` 且 `raw` 非空也不报错。
- **回滚**：`git revert`（纯新增文件）。

### RW-803 每词路由与单次扫描（消除整句打空 + 省 30%）

- **文件**：改 `server/app/routers/search.py`；改 `server/tests/test_search_api.py`（与 RW-805 同文件）。
- **要求**：
  1. `GET /api/search` 入口把 `q` 交给 `build_query_plan`；`terms` 为空才返回 `[]`（保持现有「空白查询返回空」行为）。
  2. **分词路由**：`indexable = [t for t in terms if len(t) >= 3]`、`short = [t for t in terms if len(t) < 3]`。
     - 每个 `indexable` 词**单独**发一条 FTS 查询（`MATCH` 单词短语、`ORDER BY bm25(documents_fts, 0.0, 5.0, 1.0, 1.0, 1.0)`、`LIMIT limit`），Python 侧合并；**禁止**再拼跨词的 `AND`/`OR` 大表达式（E8/E9：AND 会打空、高频词 OR 会退化到 934 ms）。
     - 合并打分：命中词数降序 → 最好 bm25 升序 → `updated_at DESC` → `id DESC`。
     - `short` 词合并成**一条** `WHERE` 扫描，且仅在「indexable 合并结果 < limit」或「indexable 为空」时执行。
  3. **谓词选择**：CJK 词一律 `LIKE … ESCAPE '\'`（大小写敏感，E3 省 30%）；含 ASCII 字母的词仍用 `ILIKE`（保大小写不敏感）。`escape_like` 沿用 [sql_utils.py](../../server/app/services/sql_utils.py)。同一行的列谓词按 **title → author → description → content** 顺序拼（E6 的逐行短路只有在这个顺序下才省正文读）。
  4. **短词填充查询不带 SQL 侧排序**（E4′ 是这一条的依据）：`WHERE … LIMIT 剩余名额`，**不写 `ORDER BY CASE`**，让 `LIMIT` 提前终止生效；字段优先级（title 0 / author 1 / description 2 / content 3）与「命中词数」在 Python 侧对**已取回的 ≤limit 行**排序，代价与行数无关、与全表无关。`ORDER BY` 只保留给 FTS 通道内部的 `bm25`。
  5. **零结果降级**：若上述合并仍 0 条且 `raw` 含 ≥4 字 CJK 段，把这批长段按 3 字滑窗再试一轮（`used_fallback=True`），结果只作为补充、排在正式命中之后。
  6. `fts_available()` 为 False 时整条链路退化成「一次 LIKE 扫描」（保持现有安全阀），并保留 warning 日志。
- **验收（真实库只读）**：
  - `/api/search?q=权力为什么会让人腐败？` → 200 且 `len(hits) > 0`（**现状 0**，这是本任务的红→绿主线）；
  - `/api/search?q=政治哲学` → 200 且第一条标题含「政治哲学」相关主题；
  - 15 条样本句逐条记录 `hits` 与中位耗时进 §5 表。
- **回滚**：`git revert`；无数据影响。

### RW-804 `/search` 与 `/search/summary` 共用一条条件链

- **文件**：改 `server/app/routers/search.py`（`_fts_usable`、`_ilike_stats`、`search_summary`）。
- **要求**：抽出一个 `_collect(db, plan, doc_type, limit) -> list[_Candidate]`，`hits` 与 `total/by_type/titles` 必须由同一份候选集派生；`by_type` 按候选集分组，`suggestions` 逻辑不变（仍走 `extract_keywords`）。删除 `_search_ilike`/`_fts_*_SQL` 里重复实现的谓词拼装。
- **`summary.total` 口径取决于 RD-J**（未签之前本任务只做到「共用一条条件链」，不擅自把精确计数改成下界）：
  - RD-J=A → `total` = 候选集文档数（≤ 扫描预算），新增 `total_capped: bool`；预算触顶时为 `True`，前端文案改「约」。
  - RD-J=B → `total` 保持精确 COUNT（罕见短词仍 500–1,150 ms），`/search` 的优化与 summary 无关，报告须明写「感知延迟未改善」。
- **验收**：同一 `q` 下 `summary.total >= len(hits)`，且 `sum(by_type.values()) == total`；两条路径不再各自持有 SQL 字面量（grep `documents_fts MATCH` 在本文件只出现在一处）。RD-J=A 时另加一条：`total_capped` 为 `True` 的查询必须在证据表里点名。
- **回滚**：`git revert`。

### RW-805 snippet 取列瘦身

- **文件**：改 `server/app/routers/search.py`（`_highlight_snippet`、正文取数路径）。
- **要求**：SQL 侧用 `substr(content, max(1, instr(content, :term) - 60), 480)` 取窗口，不再 `select(Document)` 整行 ORM 载入（E11）；窗口取不到时用 `description`；`<mark>` 仍由 Python 拼接；snippet 字符长度上限 220（含省略号），超出截断。
- **验收**：`q=弗洛伊德` 的 top-5 snippet 均含 `<mark>`；单请求从数据库读取的 content 字符总数 ≤ 64,000（脚本打印实测）。
- **回滚**：`git revert`。

### RW-806 `/api/search` 测试从 0 到 ≥14 例（红→绿）

- **文件**：新增 `server/tests/test_search_api.py`。
- **必须覆盖**：① 整句中文查询非空（RW-803 的红线，先跑红）；② 纯 2 字词；③ 混合长短词；④ 中英混排与 `RLHF`；⑤ 大小写不敏感只对含 ASCII 的词成立；⑥ 软删除文档不返回；⑦ `type=` 过滤；⑧ `limit` clamp 与 `le=100` → 422；⑨ 空白/纯标点查询返回 `[]`；⑩ `fts_available()` 打桩 False 时仍返回正确结果；⑪ `/search/summary` 的 `total`/`by_type`/`suggestions` 形状与一致性；⑫ snippet 含 `<mark>` 且 ≤220 字符；⑬ FTS 同步异常时回退 LIKE 且**只回退一次**（不双扫）；⑭ 同一 q 的 hits 与 summary 来自同一候选集。
- **要求**：临时库（`server/tests/conftest.py`），禁止连 `knowledge_base.db`；每条修复都要有「先红后绿」记录，做不到就如实写「回归锁定」而不声称红→绿。
- **验收**：`cd server && E:/zy/python/python.exe -m pytest tests/test_search_api.py tests/test_query_terms.py -q` 全绿。

### RW-807 R8b 前置测量（bigram 索引，只读，不开 G3）

- **文件**：新增 `docs/roadmap/spikes/R8-bigram-measure.md` + `server/scripts/probe_bigram_index.py`。
- **测什么**：在库副本上（不动真实库）建 `documents_fts2`，把 CJK 正文按 **2 字滑窗空格拼接**（`持续改善` → `持续 续改 改善`，拉丁词原样）写入，`tokenize='unicode61'`；测 ① 索引体积增量 ② 全库重建耗时 ③ 2 字词查询 P50/P95 ④ 重建期间峰值内存 ⑤ 3 字以上词的短语召回正确性（滑窗必须能还原连续语义）。
- **产出**：一张对照表（现状 trigram vs bigram 副本）+ 明确的 G3 请示要点（迁移 id `0011_bigram_fts`、重建耗时、磁盘增量、回滚方式）。**本轮不创建迁移、不装依赖、不写真实库**。
- **验收**：数字可复现（脚本 + 库副本路径 + 前后 SHA-256 记录）。

## 4. 门槛（R8a 完成定义）

| 门槛 | 线 | 口径 |
|---|---|---|
| 相关性 | **≥9/15** | 15 条样本句各取 top-5，人工判定「至少 1 条切题」；基线为 R7 的 1/15（现状整句打空） |
| 非空率 | **15/15** | 15 条样本句 `/api/search` 至少返回 1 条（现状 11 条纯中文句返回 0） |
| 延迟·含长词查询 | **P95 ≤200 ms** | 含 ≥3 字 CJK 词或拉丁词的查询，库副本只读 5 次取中位后算 P95 |
| 延迟·短词填充扫描 | **常见词 ≤100 ms** | 全库匹配数 > `limit` 的短词查询：靠 E4′ 的提前终止（`e4` 基线 10–86 ms） |
| 延迟·罕见短词 | **不设线，但必须点名** | 全库匹配数 ≤ `limit` 的查询（基线 q10 1,134 ms）**结构上无法提前终止**，R8a 不解决；报告要逐条列出实测并指向 R8b。这条「未达可用」是已知事实，必须写进报告 |
| 延迟·`/search/summary` | **与 `/search` 同级（≤2×）** | E14：前端 `Promise.all` 等两者，summary 单独慢等于没优化。口径取决于 **RD-J**，未签则本行按 RD-J 的 B（保持精确计数）验收 → 明写「感知延迟未改善」 |
| 回归 | 全绿 | `pytest tests -q`（248+ 不减）、`npm run typecheck`、`npm run lint`、`npm test`（154 不减）、`npm run build` |
| 卫生 | 零越界 | 无新依赖、无迁移、真实库与 `server/data/` 零写入、`:3000`/`:8000` 用户进程不重启 |
| 测试补齐 | `/api/search` **0 → ≥14 例** | E12 |

任一项不达标 → 停下登记，不进入 R8b，也不申请 G4 推送。

## 5. 证据槽（执行时填写）

| 任务 | 状态 | 证据 |
|---|---|---|
| RW-801 | DONE | `server/scripts/probe_search_cost.py` 已落地；`git check-ignore` 退出 1；`--mode baseline --db scripts/spike-out/spike.db --runs 5` 跑通，产出 §1 的 E1/E2/E3/E4/E4′/E5/E7/E8/E10 全部数字（`e2` 699–1,491、`e3` 517–1,153、`e4` 10–1,134、`e5_meta` 0.8–6.7 ms）。`--mode after` 已在 RW-802/803 之后复跑（下表 + `scripts/spike-out/r8-after.json`，含 top-5 标题，供 §4 相关性判定复核） |
| RW-802 | DONE | `server/app/services/query_terms.py`（纯函数、零依赖）+ `tests/test_query_terms.py` **28 例全绿**。15 条样本的切词结果见下表 `R8a 词` 列；「机器学习模型」这类整词打空时由 4 字滑窗补位（`iter_recall_windows`）。API 测试反过来暴露了一个真缺陷：`...` / `--` 这类**纯标点段**曾被当成查询词（`- . +` 为保住 `C++`/`1.0` 不算分隔符），已加「至少含一个字母或数字」的规则并补测 |
| RW-803 | DONE | `server/app/routers/search.py` 重写：逐词路由（≥3 字 FTS／2 字 LIKE）、短词**一条不带 `ORDER BY` 的扫描**、FTS 排序**去 JOIN**（E15）、CJK 词不套 `lower()`、正文只取 480 字窗口。15/15 非空（基线 11 条纯中文整句 0 命中），`/search` 中位 **54.1 ms**，除 q10 外最大 **128.4 ms** |
| RW-804 | DONE（口径 = RD-J 的 **B**，未签） | `/search/summary` 与 `/search` 共用 `_route`/`_like_predicate`/滑窗触发条件；测试 `test_summary_and_hits_share_the_window_fallback` 在旧代码上跑红。**代价明写**：summary 的短词计数不带 `LIMIT`，中位 **315.0 ms**、最大 645.8 ms，是 `/search` 的 **5.8 倍**（q12 达 14.8×）→ §4 的 summary 行**未达标**，感知延迟未改善，等 RD-J |
| RW-805 | DONE | snippet 走 SQL 侧 `substr/instr` 窗口（`_content_window_expr`），不再 ORM 整行载入；`_clip` 给 220 字硬上限并补全 `<mark>`。E11 的 snippet 段 18–74 ms 并入整条查询：q01 从 `e2` 的 1,042 ms 降到 63.3 ms |
| RW-806 | DONE | `tests/test_search_api.py` **30 例全绿**（≥14 达标）。把该文件跑在 R8a 之前的 `search.py` 上：**12 红 / 18 绿**。真红（行为差异）6 条：整句查询 2 条、滑窗降级 2 条、`ORDER BY CASE` 与 FTS `JOIN` 两条 SQL 形状。红但因「旧模块没有该函数」的 6 条（`_like_predicate`×2、`_clip`×2、单次回退、`limit<1`）已在文件 docstring 里标注为 `[新约束]`，不冒充红→绿。其余 18 条是 `[回归锁定]`（软删、type、limit 上界、空查询、FTS 不可用、summary 形状…旧代码也绿）。全量 `pytest tests -q` = **306 passed**（R8a 前 248） |
| RW-807 | DONE（只测量，**未申请 G3**） | 报告 [spikes/R8-bigram-measure.md](spikes/R8-bigram-measure.md)。① 体积：bigram contentless **255.3 MiB** vs trigram contentless 668.1（−61.8%），可维护的普通 fts5 形状 755.6 vs 950.9（−20.5%），生产 `documents_fts` 真实占 **954.8 MiB**（VACUUM 前后差，= 库的 75.4%）。② 重建 **74.9 s**（展开 24.5 s）vs trigram 113.6 s。③ 2 字词 P95（`--runs 15`）全部 **≤1.53 ms**，同词 LIKE 全表扫描 4.03–**538.78 ms** → q10 的 828.8 ms 结构上消失。④ 峰值 RSS **173 MB**。⑤ 短语召回：20 个词逐词集合差 = bigram 多 7 个「词×行」（只涉及 NUL 破损的 3 篇）、**少 0 篇**，13 条中文查询并集与现状完全相等；**拉丁词从子串变词边界**（`hard` 599→328，差的 271 篇里 52 篇是 Richard）→ 记为待决 RD-K。另两条任务书外的硬结论：SQLite 3.42 无 `contentless_delete`（contentless 表不能逐行删改），SQL 触发器展开单行 0.8–2,455.8 ms 且对 NUL 行仍盲 → 维护只能落应用侧（单行 20 万字 ≈64 ms），记为待决 RD-L；顺带普查出 **11/1,283 篇含 NUL，LIKE 与 trigram 双盲**（F-17，已登记） |

15 条样本句的前后对照（`--runs 5` 取中位，库副本 `scripts/spike-out/spike.db`，1,357 篇 / 1.4 亿字）：

| qid | 查询 | R8a 词 | 现状 hits / ms | R8a hits / ms | summary total / ms | 相关性（top-5 至少 1 条切题） |
|---|---|---|---|---|---|---|
| q01 | 弗洛伊德如何解释文明与本能的冲突？ | 弗洛伊德／解释文明／本能／冲突 | 0 / 0.3（整句 e1） | 20 / 63.3 | 543 / 502.8 | ✓《文明及其不满》 |
| q02 | 精神分析如何理解创伤？ | 精神分析／理解创伤 | 0 / 1.2 | 20 / 41.1 | 134 / 47.9 | ✓《精神分析如何改变人》 |
| q03 | 权力为什么会让人腐败？ | 会让人腐败／权力／腐败 | 0 / 0.3 | 20 / 36.0 | 372 / 521.8 | ✗ top-5 无一谈权力腐败 |
| q04 | 民粹主义为什么在民主国家兴起？ | 在民主国家兴起／民主国家兴起／民粹主义 | 0 / 0.2 | 20 / 50.9 | 41 / 19.9 | ✓《危局：精英、反精英与政治解体之路》 |
| q05 | 第一次世界大战为什么会爆发？ | 第一次世界大战／会爆发／爆发 | 0 / 0.6 | 20 / 111.4 | 368 / 452.9 | ✓《滔天洪水：第一次世界大战与全球秩序的重建》 |
| q06 | 人工智能会取代哪些工作？ | 人工智能／取代／工作 | 0 / 0.3 | 20 / 52.1 | 644 / 466.0 | ✓《至高无上》《权力与预测》 |
| q07 | 机器学习模型如何避免过拟合？ | 机器学习模型／避免过拟合 | 0 / 0.4 | 12 / 15.8 | 12 / 8.6 | ✗（滑窗召回「机器学习」，但没有一本讲过拟合） |
| q08 | 数字时代为什么让人更孤独？ | 让人更孤独／数字时代／更孤独 | 0 / 0.4 | 20 / 55.0 | 43 / 25.0 | ✓《我选择独自一人》 |
| q09 | 资本主义与民主之间有什么张力？ | 资本主义／民主／张力 | 0 / 0.4 | 20 / 54.1 | 376 / 645.8 | ✓《后资本主义生活》 |
| q10 | 六爻纳甲如何起卦和断卦？ | 六爻纳甲／起卦／断卦 | 0 / 0.1 | 8 / **828.8** | 8 / 829.9 | ✓《增删卜易》《易隐》《卜筮正宗》 |
| q11 | 国家权力的合法性来自哪里？ | 国家权力／合法性 | 0 / 0.3 | 20 / 115.1 | 154 / 76.4 | △《资本与意识形态》（沾边，不算硬切题） |
| q12 | 大模型对齐为什么需要 RLHF？ | 大模型对齐／需要／RLHF | 0 / 0.1（含空格，走旧 AND 路径 e1b） | 20 / 18.6 | 666 / 274.4 | ✓《大模型工程师面试》《The Craft of Post-Training》 |
| q13 | What is the hard problem of consciousness? | hard／problem／consciousness | 0（旧路径把 `what/is/the/of/?` 一起 AND；`e1b` 用手工词表才有 11 条） | 20 / 128.4 | 657 / 344.5 | ✓《What Is Consciousness?》 |
| q14 | Why do introverts need quiet time? | introverts／need／quiet／time | 0（同上；`e1b` 手工词表 13 条） | 20 / 121.8 | 676 / 315.0 | ✓《Quiet: The Power of Introverts》 |
| q15 | 什么是政治哲学？ | 政治哲学 | 0 / 0.3 | 20 / 51.1 | 65 / 30.4 | △《自由及其背叛》（政治哲学读本，沾边） |

> 相关性口径：明确切题 13 条里扣掉两条 △ 仍剩 11 条，判 ✗ 的是 q03、q07 → **≥9/15 达标**（基线：整句路径 15/15 全 0 命中）。
> 延迟口径：含长词的 14 条最大 128.4 ms（≤200 ms 线，取最大值比 P95 更严）；q10 = 828.8 ms 属「罕见短词」行，**不设线但点名**，指向 R8b。

## 6. 需要用户决策（执行前必须签）

| ID | 决策 | 影响 | 实测依据 | 默认建议 |
|---|---|---|---|---|
| RD-G | 整句中文查询的语义：从「精确短语匹配（现 0 命中）」改为「关键词命中任一即返回 + 按命中词数与 bm25 排序」 | RW-802/803 全部 | E1（11/11 打空）、E8 | **A 采纳**。B=保留短语优先、仅 0 命中时降级重写（更保守，打空路径多一轮 FTS）；C=不改（则「搜不到」保留） |
| RD-H | 是否授权改写 `api-contracts.md` 第 683 行「`GET /api/search` 行为不变」并补写 R8 关键词语义契约 | RW-804、文档 | 该条款是 B5 时代为「语义走独立端点」写的，B5 已 NO-GO | **授权改写**，并注明出处为本任务书 §3 |
| RD-I | 短词（<3 字）是否继续搜正文 | RW-803 第 2 步 | E10：全列 521 本 vs 元数据 17 本 | **A 继续搜正文**（慢但全）。B=元数据先填满 limit 就不扫正文（15–99 ms，但正文命中随机丢失）；C=短词不搜正文（5–9 ms，丢 ≈97% 正文命中，不建议） |
| **RD-J**（新） | `summary.total` 的口径：**精确计数**（继续付 500–1,150 ms 全表计数）还是**候选集内计数 + `total_capped` 标记**（10–90 ms，但 UI「共 N 条结果」会显示为下界） | RW-804、§4 的 summary 行、E14 | E4′：精确计数 = 必须扫完；`e4` 常见词只要 10–86 ms | **A 候选集内计数 + `total_capped`，并授权把 [page.tsx:250](../../src/app/search/page.tsx#L250) 的「共」在 `total_capped` 时改为「约」**（一行文案，不动调用方式；这是 §2「不碰 `src/**`」的唯一例外，需你点头）。B=保持精确计数：`/search` 变快但用户感知不变。C=A 但不改文案 → 界面会静默报出一个偏小的精确数，**我不建议**（与数据完整性口径冲突） |
| 沿用 | D4 OCR / D5 暗色 / D6·G4 提交与推送节奏 | 不变 | — | 维持现状：不装 OCR；每批停下验收，提交前确认，G4 之前不推 |

**签核记录（2026-10-27）**：用户对 R8 的指令是「分两批：先查询侧，再评估 bigram 索引」+「开工」。按 R7 的先例， terse 指令把默认建议的决定权交给执行人，执行人取保守项并留痕可覆盖：
**RD-G = A**（关键词任一命中即返回 + 命中词数/字段优先级排序）、**RD-H = 授权改写**（已落到 [api-contracts.md §11](api-contracts.md)，含 `limit` 下界从「不设防」改为 `1–100`）、**RD-I = A**（短词继续搜正文）。
**RD-J 未签**：本轮按 **B**（`summary.total` 保持精确计数）实现，代价是 §4 的 summary 行不达标（实测 `/search/summary` 中位 315.0 ms = `/search` 的 5.8 倍，前端 `Promise.all` 下用户感知延迟不变）。这是本轮停下验收时**唯一需要你先定的事**，A 会改一行前端文案，所以我没有替你做。

## 7. 明确不做（防越界）

- 不引入任何分词/embedding 依赖（G2 仍按 R7 决议关闭，重开需你书面改写口径）。
- 不加 FTS 索引列/表、不建迁移 `0011`（那是 R8b，需 G3 + 你的单独授权）。
- 不改 `MAX_CHARS=200_000` 入库截断（F-16）——它决定「尾部内容永远搜不到」，但改它要重提取真实库（G1 逐次授权），与 R8a 无关。
- 不动前端调用方式（`searchApi(q, type)` 原样），不改 `/library` 的筛选查询。
- 不顺手清理 F-13（ruff 门禁）、F-11（封面缓存失效）等既有发现项。
