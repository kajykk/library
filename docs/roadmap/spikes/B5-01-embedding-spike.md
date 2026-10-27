# B5-01 语义检索技术 spike（真实数据重评，2026-10-27）

> 任务书：[B5-semantic-search.md](../B5-semantic-search.md) 第 5 节 B5-01；硬性门槛见第 3 节。
> 本页只报**实测值**，不放宽任何一条门槛；go / no-go 的原始归属是用户，本页出稿后用户 2026-10-27 回复「你决定」，四项决策由执行方按第 3 节原文规则定下 → 结论 **NO-GO**，签核记录见 [B5-02-decision.md](B5-02-decision.md) §5。
> 本轮取代 [B5-02-gonogo.md](B5-02-gonogo.md)（旧结论建立在 44.8% 正文为空的老库上，前提已消失）。

## 0. 结论摘要

按第 3 节七条门槛逐条对照：

- **通过**：相关性、安装体积（wheel 与权重）、CI（不装 fastembed 时 pytest 全绿）、隐私（除首次模型下载无出站）。
- **不通过**：全量索引耗时（外推 2 h 15 min，线 20 min）、检索常驻内存外推（bge ≈661 MB，线 500 MB）、hybrid P95 延迟（11/15 条超 800 ms）。
- **关键归因**：hybrid 延迟超标的不是向量检索——纯语义暴力点积 P50 3.1–4.0 ms / P95 ≤6.4 ms（bge，8,866 向量），全库外推约 53–70 ms。超标全部来自**现有中文关键词通道**：查询里只要含 <3 字的词（政治、权力、文明、本能、泛化…），现状 `/api/search` 的实现就跳过 FTS 走全表 `LIKE`，实测关键词通道 694–1,312 ms；两条英文查询（词长均 ≥3）走 FTS MATCH，hybrid P95 只有 127–171 ms，**恰好是通过门槛的两条**。
- 严格执行「任一项不达标即 no-go」，本轮结果是 **no-go**（2026-10-27 已按用户「你决定」的授权定稿并记入 [B5-02-decision.md](B5-02-decision.md) §5）。三条 FAIL 里只有索引耗时/内存是嵌入本身的物理瓶颈；延迟那条属现有中文关键词通道（F-15），它的修复价值独立于 B5 成立。

## 1. 环境与可复现命令

| 项目 | 值 |
|---|---|
| 机器 CPU | 12 逻辑核，单进程 CPU，无 GPU |
| 系统解释器 | `E:/zy/python/python.exe`（3.12，无 fastembed/numpy/psutil） |
| spike 隔离环境 | `E:/code/projects/library/.spike-venv`（fastembed 0.9.1 / numpy 2.5.4 / onnxruntime 1.31.1，共 31 个包） |
| 应用环境 | `server/.venv`（**未安装任何 B5 依赖**，保持干净） |
| 数据 | `server/scripts/spike-out/spike.db` = `server/knowledge_base.db` 的字节副本（1,329,700,864 B，源文件 mtime 10-27 00:55，副本 11:34），全程以 `mode=ro&immutable=1` URI 打开 |
| 只读约束 | 未写 `knowledge_base.db`，未写 `server/data/`；仓库侧只新增 `.gitignore` 两行 + `server/scripts/spike_semantic.py` + 本报告目录 |

```bash
# 依赖安装（本批唯一允许的出站：pip 拉包 + 首次模型下载，来源与哈希见 §3，隐私判定见 §9）
E:/zy/python/python.exe -m venv E:/code/projects/library/.spike-venv
E:/code/projects/library/.spike-venv/Scripts/python.exe -m pip install --upgrade pip
E:/code/projects/library/.spike-venv/Scripts/python.exe -m pip install fastembed

# 跑 spike（离线索引 + 检索 + 延迟，产出 retrieval.json / pool.md）
cd E:/code/projects/library
HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  .spike-venv/Scripts/python.exe server/scripts/spike_semantic.py pool --threads 4 --batch-size 8

# 按 judgments.json 打分（产出 relevance.md / relevance.json）
E:/zy/python/python.exe server/scripts/spike_semantic.py score
```

`spike-out/` 内的辅助脚本（本轮新增，gitignore，不作为交付物）：`measure_speed.py`（threads×batch 吞吐网格）、`measure_fts_split.py`（关键词通道耗时分段）、`recompute_hybrid.py`（等深 RRF 重算）、`make_judgments.py`（人工判定录入）。

## 2. 与任务书的偏离（3 处，均已记录）

1. **第二候选模型换了**。任务书写的是 `intfloat/multilingual-e5-small`，fastembed 0.9.1 的 41 个受支持模型里 **e5 系列只有 `intfloat/multilingual-e5-large`**（1024 维、2.24 GB、需 query/passage 前缀），单权重就超第 3 节 500 MB 线，且 1024 维向量会把常驻内存门槛再抬高约一倍。故第二候选换成同为多语小模型的 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384 维、0.22 GB）。两模型用同一批 15 条查询并排跑，结果都在 §8。
2. **切片样本 50 本，不是任务书要求的 20 本**（epub 28 / pdf 16 / txt 6；zh 46 / en 4；正文字数 min 3,816、中位 200,000、max 200,000）。只增强代表性，不改变任何门槛。
3. **人工判定明细入了库**：`docs/roadmap/spikes/B5-01-judgments.json`（15 查询 × 118 文档判定 + 141 chunk 判定）。因为 `spike-out/` 是 gitignore 且 no-go 收尾时会被删除，判定证据必须留下，否则相关性门槛不可复核。

另有两处「按任务书条件自然不触发」的差，记在对应小节而不是本节：任务书第 5 项的 hnswlib 备选（前置条件「暴力点积外推 >800 ms」不成立，见 §4）与 CI 门槛里 build/docker 本轮未重跑（只重跑 pytest，见 §9 备注）。`docs/roadmap/progress.md` 的偏离记录把这两处也各列了一行，合计 4 行。

## 3. 问题 1 · 模型选型

| 候选 | HF 源（fastembed 实际拉取的仓库） | 维度 | token 上限 | 许可 | 权重字节 | 主 onnx SHA-256 |
|---|---|---|---|---|---|---|
| `BAAI/bge-small-zh-v1.5` | `Qdrant/bge-small-zh-v1.5` @ `46fbe35f…` | 512 | 512 | MIT | 95,221,432 B（90.8 MB） | `1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38`（model_optimized.onnx 94,781,076 B） |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | `Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` @ `faf4aa42…` | 384 | **128** | Apache-2.0 | 252,138,706 B（240.5 MB） | `634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99`（model_optimized.onnx 235,052,644 B + tokenizer.json 17,083,009 B） |
| `intfloat/multilingual-e5-large`（任务书 e5-small 的替代，**未下载**） | `Qdrant/multilingual-e5-large-onnx` | 1024 | 512 | MIT | 2.24 GB（fastembed 登记值） | 未验证 |

- 模型卡：https://huggingface.co/BAAI/bge-small-zh-v1.5 、https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 。两许可（MIT / Apache-2.0）均允许本地推理与权重缓存。
- 缓存位置：`server/scripts/spike-out/models/`（332 MB，两个仓库共 10 个文件）；生产若 go 需改为固定 `HF_HOME` 并锁定 revision。
- 首次下载耗时与来源（任务书要求逐条记录；URL 由 fastembed 注册表的 `sources.hf` + `model_file` 按 HF 规范 resolve 形式拼出，本地缓存目录 `models--Qdrant--…` 与之对应，非我另外猜测的地址）：
  - `https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/main/model_optimized.onnx`（94,781,076 B，27.5 s，10-27 11:33）+ 同仓 `config.json` / `tokenizer.json` / `tokenizer_config.json` / `special_tokens_map.json`
  - `https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/resolve/main/model_optimized.onnx`（235,052,644 B，42.3 s，10-27 11:33）+ 同仓 4 个配置文件（含 `tokenizer.json` 17,083,009 B）
  - 日志 `spike-out/model-download.log`；两文件的 SHA-256 见上表；此后所有运行都带 `HF_HUB_OFFLINE=1`。
- **截断风险（选模型时必须知道的坑）**：800 字切片对 bge 有 6,056/8,866 条（68.3%）超过 512 token（平均 579.6，最长 802），尾部被静默丢弃；multi 的上限是 128 token，**每一条 chunk 都被截到 128**（平均 127.9，最长 128），即每个切片约 84% 的字符根本没进入模型。multi 在质量上反而略高于 bge（§8），说明分数主要由切片开头决定——这对「概念问句答案在中段」的长书章节是系统性风险。

## 4. 问题 2 · 库选型

`fastembed`（onnxruntime，无 torch）可用，两个候选模型都在其注册表内，**不需要退化的 `sentence-transformers` + torch 方案**（按任务书该方案直接判不通过，故未安装、未测量）。

- 安装后 `.spike-venv/Lib/site-packages` 合计 **173 MB**，其中 pip 自身 12 MB → **新增 wheel ≈161 MB ≤ 200 MB（通过，但只剩 39 MB / 24% 余量）**。
- 占用前列：onnxruntime 47 MB、numpy 34 + numpy.libs 21 MB、Pillow 16 MB、hf_xet 9 MB、tokenizers 8 MB、huggingface_hub 8 MB、fastembed 2 MB。
- numpy / Pillow 与 `server/requirements.txt` 无重叠，即全部为新增传递依赖；`hnswlib` **未引入**：任务书第 5 项的条件是「暴力点积外推 >800 ms 才测 hnswlib」，实测全库外推 53–70 ms（§7），条件不成立。
- 依赖若批准，写入 `server/requirements-semantic.txt`，**不进 CI、不进镜像**（本轮已验证不装它们 pytest 全绿）。

## 5. 问题 3 · 切片策略

参数按任务书：`CHUNK_SIZE=800`、`OVERLAP=120`、`MAX_CHUNKS=200`、`MIN_DOC_CHARS=200`。切分函数 `spike_semantic.chunk_text()`：先 `normalize_text()`（合并空白、压缩连续换行），再按 680 字符步进取 800 字窗口，窗口尾部 200 字内若命中 `。！？；.!?;` 就在标点处收口，否则硬切；不按句分（现有正文提取已丢段落结构，句子边界不可靠）。

| 指标 | 50 本样本 | 全库（只读扫描估算） |
|---|---|---|
| 有正文文档 | 50 | 1,060（`deleted_at IS NULL` 且长度 >0；<200 字跳过 15 本） |
| 正文字符 | 7,979,801 | 140,029,467 |
| chunk 数 | 8,866 | **≥153,673（下界）** |
| 每本 chunk | 平均 177.3 / 中位 200 / 最大 200 | — |
| 触顶 200 的本数 | 37 / 50 | 633 / 1,060 |
| 单 chunk 字符 | 平均 762.9 / 最大 800 | — |
| PDF 子集 | 16 本，平均 165.1 chunk，12 本触顶 | — |
| 放大倍数 | — | chunk 17.33×、字符 17.55× |

- 估算方法：`ceil(len(content)/680)` 再以 200 封顶，只读遍历 140,029,467 字符；标点收口只会**增加** chunk 数，所以 153,673 是下界。
- **覆盖缺口（新发现，任务书未预见）**：`server/app/services/content_extract.py:21` 的 `MAX_CHARS = 200_000` 已把入库正文截到 20 万字，而 `MAX_CHUNKS=200` 的切片（199×680 + 800 = 136,120 字符）只覆盖其中 13.61 万。也就是说 633 本长书除了入库时已被砍掉的部分，切片阶段还会再丢尾部约 6.4 万字（≈32%）。语义索引对这部分**永远检索不到**，除非改 `MAX_CHUNKS` 或提高 `MAX_CHARS`（后者涉及真实库重提取，需 G1 逐次授权）。

## 6. 问题 4 · 索引成本

基准：8,866 chunks（50 本），batch=8，threads=4，无 OOM。

| 模型 | 加载 | 嵌入 | 吞吐 | 字符吞吐 | 全库外推 | 20 min 线 |
|---|---|---|---|---|---|---|
| bge-small-zh-v1.5 | 0.13 s | 504.35 s | 17.6 chunk/s | 13,412 字符/s | **8,850 s ≈ 2 h 15 min** | 超 7.4× → **FAIL** |
| paraphrase-multilingual-MiniLM | 1.12 s | 285.93 s | 31.0 chunk/s | 23,657 字符/s | **5,019 s ≈ 1 h 24 min** | 超 4.2× → **FAIL** |

threads×batch 网格（1,200 chunk 子样本＝同一批前 8 本文档，bge，`spike-out/speed-grid.json`；`峰值 RSS` 是进程级单调最大值，被前面的组合污染，只能当上界看）：

| threads | batch | 1,200 chunks 耗时 | chunk/s | 全库外推 | 该轮 RSS 增量 |
|---|---|---|---|---|---|
| 4 | 8 | 81.6 s | 14.7 | 174 min | 213.2 MB |
| 4 | 16 | 87.2 s | 13.8 | 186 min | 402.4 MB |
| 4 | 32 | 86.5 s | 13.9 | 185 min | 834.6 MB |
| 8 | 8 | 69.1 s | **17.4** | **147 min** | 208.1 MB |
| 8 | 16 | 72.2 s | 16.6 | 154 min | 401.9 MB |
| 8 | 32 | 68.9 s | **17.4** | 147 min | 835.5 MB |
| 12 | 8 | 118.3 s | 10.1 | 253 min | 208.0 MB |
| 12 | 16 | 104.3 s | 11.5 | 223 min | 403.1 MB |
| 12 | 32 | 88.4 s | 13.6 | 189 min | 835.1 MB |

- 网格最优 **17.4 chunk/s（threads=8）**，与 8,866 chunk 全程基准的 17.6 chunk/s 持平；子样本比全程略慢是模型/warmup 摊销差异，所以只读相对趋势。
- **加线程不解决问题**：12 线程（=全部逻辑核）反而掉到 10.1 chunk/s，onnxruntime 的 intra-op 线程在超线程上争用为负收益。
- **加批次不提速、只涨内存**：batch 8→32 吞吐 17.4→17.4，RSS 增量 208→836 MB。
- 结论：全库 153,673 chunks 的索引耗时**没有调参出路**，最好也就 ≈147 min（且 153,673 本身是下界），线是 20 min → FAIL 幅度稳定在 7×。

换算给决策用：**20 分钟预算 @17.6 chunk/s ≈ 21,120 chunks ≈ 120 本**（按样本 177 chunk/本）。所以「全库一次索引」与「20 分钟」不可能同时成立；要 go 就必须把第 3 节那条改写成「单次后台任务预算 ≤20 min + 分批覆盖」，或把覆盖范围缩到某个子集。

## 7. 问题 5 · 检索延迟

向量部分（numpy 暴力点积，8,866 向量常驻，top-5）：

| 模型 | P50 区间 | P95 最大 | 全库外推（×17.3） |
|---|---|---|---|
| bge | 3.06 – 4.02 ms | 6.38 ms | ≈53–70 ms |
| multi | 5.37 – 6.31 ms | 8.81 ms | ≈93–109 ms |

→ 均在 800 ms 内，**不需要 ANN**（这也是不引入 hnswlib 的依据）。

hybrid（FTS 关键词通道 top-5 + 语义 top-5 的等深 RRF，含 query 嵌入与全库 LIKE/FTS 查询）逐条实测：

| 查询 | 通道 | bge P50/P95 (ms) | multi P50/P95 (ms) | 超 800 ms |
|---|---|---|---|---|
| q01 弗洛伊德如何解释文明与本能的冲突？ | ILIKE | 983 / 1030 | 975 / 1040 | 是 |
| q02 精神分析如何理解创伤？ | ILIKE | 728 / 896 | 744 / 789 | bge 是 |
| q03 权力为什么会让人腐败？ | ILIKE | 719 / 778 | 752 / 785 | 否（接近） |
| q04 民粹主义为什么在民主国家兴起？ | ILIKE | 862 / 907 | 865 / 875 | 是 |
| q05 第一次世界大战为什么会爆发？ | ILIKE | 979 / 1039 | 1014 / 1117 | 是 |
| q06 人工智能会取代哪些工作？ | ILIKE | 955 / 1044 | 966 / 1137 | 是 |
| q07 机器学习模型如何避免过拟合？ | ILIKE | 1244 / 1403 | 1246 / 1305 | 是 |
| q08 数字时代为什么让人更孤独？ | ILIKE | 661 / 737 | 678 / 734 | 否 |
| q09 资本主义与民主之间有什么张力？ | ILIKE | 791 / 838 | 829 / 868 | bge 是 |
| q10 六爻纳甲如何起卦和断卦？ | ILIKE | 1188 / 1248 | 1227 / 1237 | 是 |
| q11 国家权力的合法性来自哪里？ | ILIKE | 810 / 855 | 833 / 888 | 是 |
| q12 大模型对齐为什么需要 RLHF？ | ILIKE | 1254 / 1319 | 1250 / 1292 | 是 |
| q13 What is the hard problem of consciousness? | **FTS MATCH** | 122 / 171 | 119 / 127 | 否 |
| q14 Why do introverts need quiet time? | **FTS MATCH** | 134 / 140 | 137 / 143 | 否 |
| q15 什么是政治哲学？ | ILIKE | 849 / 876 | 859 / 883 | 是 |

**门槛判定：bge 11/15、multi 10/15 条 P95 >800 ms → FAIL。**

归因分段实测（`spike-out/measure_fts_split.py`，同一批 15 条查询打全库 1,283 本存活文档，每行独立计时）：

| 查询 | 短词（<3 字） | 现状走哪条路 | ILIKE 全表 | FTS MATCH（强制走） | snippet | 关键词通道合计 |
|---|---|---|---|---|---|---|
| q01 | 文明、本能 | ILIKE | 1,428 | 147（有 5 行） | 33 | 993 |
| q02 | 创伤 | ILIKE | 744 | 78 | 37 | 747 |
| q03 | 权力、腐败 | ILIKE | 704 | **0.1（0 行）** | 24 | 717 |
| q04 | 民粹、民主 | ILIKE | 819 | **0.1（0 行）** | 20 | 886 |
| q05 | 战争、刺杀 | ILIKE | 1,003 | 119 | 33 | 1,014 |
| q06 | 就业 | ILIKE | 1,043 | 155 | 31 | 1,008 |
| q07 | 泛化、正则 | ILIKE | 1,225 | 24 | 34 | 1,312 |
| q08 | 孤独 | ILIKE | 674 | 98 | 24 | 694 |
| q09 | 民主 | ILIKE | 793 | 124 | 18 | 828 |
| q10 | 六爻、纳甲、起卦 | ILIKE | 1,266 | **0.1（0 行）** | 30 | 1,248 |
| q11 | 国家、权力 | ILIKE | 904 | 97 | 35 | 897 |
| q12 | 对齐 | ILIKE | 1,238 | 30 | 33 | 1,281 |
| q13 | 无 | **FTS MATCH** | 1,177（对照） | 76 | 57 | **112** |
| q14 | 无 | **FTS MATCH** | 1,187（对照） | 132 | 74 | **122** |
| q15 | 政治、哲学 | ILIKE | 845 | 48 | 24 | 873 |

- 口径说明：分段列（`FTS MATCH` / `ILIKE 全表` / `snippet`）各取该查询的**单次冷跑**，「关键词通道合计」是走真实 `fts_search()` 路径连续 5 次取的**中位数**（此时 SQLite 页缓存已热）。两者不能相加核对——q01 的 1,428 ms 冷跑 vs 993 ms 热中位数就是这个差；用「合计」列判门槛，用「分段」列判归因。
- 关键词通道耗时区间：ILIKE 路径 **694–1,312 ms**；FTS MATCH 路径 **112–122 ms**（两条英文）。语义部分 ≤8.81 ms，RRF 融合本身 <1 ms。
- 注意「FTS MATCH 只要 0.1 ms」的三种情况（q03/q04/q10）不是快，而是**一行都没命中**——trigram 索引不登记 2 字 token，所以强制走 FTS 会静默返回空集（这正是 [search.py:192-193](../../../server/app/routers/search.py#L192-L193) 注释里写的「静默漏配」，也是必须整体回退 ILIKE 的原因）。同一批词里只要含一个 ≥3 字词（如「弗洛伊德」「人工智能」），FTS 就能在 102–112 ms 内返回 5 行。
- snippet 生成 18–74 ms，不是瓶颈。
- ILIKE 路径为何这么贵：[server/app/routers/search.py:111-137](../../../server/app/routers/search.py#L111-L137) 的 `_search_ilike` 对 title/author/description/content 四列各做一次 `%term%` 全表扫描，再在 `ORDER BY CASE` 里把同样的 LIKE 表达式重算 3 轮；spike 复刻了这条 SQL（扫描对象是全库 1,283 行存活记录，其中 1,075 行有正文、平均 13.2 万字符）。

含义：**这条 FAIL 属于现有搜索的中文分词缺陷，不是嵌入引入的成本**。它独立于 B5 成立，也是 B5 延迟门槛能否达标的先决条件。

## 8. 问题 6 · 质量对照（15 条查询，人工判定）

判定口径：`docs` = 该文档是否**主题相关**（文档级，hybrid 与 FTS 通道的粒度）；`chunks` = 具体切片是否**含答案**（chunk 级，语义 top-5 的粒度）。命中 = top-5 内至少 1 个被判为相关。标注明细见 `B5-01-judgments.json`。

| 查询 | 类型 | FTS full | FTS kw | bge 语义 top5 | bge 文档级 | bge hybrid | multi 语义 top5 | multi 文档级 | multi hybrid |
|---|---|---|---|---|---|---|---|---|---|
| q01 弗洛伊德如何解释文明与本能的冲突？ | 概念+人名 | 0 | 1 | 4 | 1 | 1 | 2 | 1 | 1 |
| q02 精神分析如何理解创伤？ | 概念 | 0 | 2 | 2 | 1 | 2 | 1 | 1 | 3 |
| q03 权力为什么会让人腐败？ | 概念 | 0 | 1 | 3 | 1 | 1 | 3 | 1 | 1 |
| q04 民粹主义为什么在民主国家兴起？ | 概念 | 0 | 0 | 1 | 1 | 1 | 1 | 1 | 1 |
| q05 第一次世界大战为什么会爆发？ | 事件 | 0 | 1 | 0 | 0 | 1 | 1 | 1 | 1 |
| q06 人工智能会取代哪些工作？ | 概念 | 0 | 0 | 0 | 0 | 0 | 3 | 1 | 1 |
| q07 机器学习模型如何避免过拟合？ | 概念 | 0 | 1 | 2 | 1 | 1 | 0 | 0 | 2 |
| q08 数字时代为什么让人更孤独？ | 概念 | 0 | 1 | 2 | 1 | 2 | 2 | 2 | 2 |
| q09 资本主义与民主之间有什么张力？ | 概念 | 0 | 0 | 3 | 1 | 1 | 1 | 1 | 1 |
| q10 六爻纳甲如何起卦和断卦？ | 短词/术语 | 0 | 3 | 5 | 3 | 3 | 5 | 3 | 3 |
| q11 国家权力的合法性来自哪里？ | 概念 | 0 | 1 | 3 | 2 | 2 | 3 | 2 | 2 |
| q12 大模型对齐为什么需要 RLHF？ | 短词/术语 | 0 | 1 | 1 | 1 | 2 | 2 | 1 | 2 |
| q13 What is the hard problem of consciousness? | 英文 | 0 | 2 | 1 | 1 | 2 | 2 | 2 | 2 |
| q14 Why do introverts need quiet time? | 英文 | 1 | 1 | 0 | 0 | 1 | 1 | 1 | 1 |
| q15 什么是政治哲学？ | 短词 | 0 | 2 | 2 | 2 | 2 | 4 | 2 | 3 |

汇总：

- **现状 FTS（整句当一个短语）**：1/15 命中 —— 现有搜索对概念问句几乎无效，只有 q14 靠整句里恰好出现的词序侥幸命中。
- **FTS 关键词重写后（最好情况）**：12/15，q04/q06/q09 落空（概念词在正文里不含字面出现）。
- **语义**：bge 12/15（q05/q06/q14 落空），平均 top-5 精度 0.39；multi **14/15**（仅 q07 落空），平均精度 0.41。
- **hybrid 不劣于任一单通道**：bge 15/15、multi 15/15。
- 门槛换算：第 3 节要求「semantic top-5 至少 60% 命中」= ≥9/15 → bge 12、multi 14 → **PASS**；「hybrid 在 ≥10/15 上不劣于任一单通道」→ 15/15 → **PASS**。
- 修正记录：初版 hybrid 把语义 top-5 的**文档秩**压成 5 个文档、而 FTS 通道是 5 个文档，但 chunk 融合深度不一致，导致 q08 的 multi 出现虚假「劣于」。改为两通道等深 top-5 融合（`recompute_hybrid.py`）并判定 `4b07f2bd…《困境》` 的 :55 切片在文档级相关后，两条模型都是 15/15。此处粒度判断是唯一一处由执行方补做的标注，已写进 judgments.json 可复核。

## 9. 门槛对照表（第 3 节原文，未放宽）

| 维度 | 通过线 | 实测 | 判定 |
|---|---|---|---|
| 相关性 | semantic top-5 ≥60% 命中；hybrid ≥10/15 不劣于单通道 | bge 12/15 (80%)、multi 14/15 (93%)；hybrid 均 15/15 | **PASS** |
| 安装体积 | 新增 wheel ≤200 MB；权重 ≤500 MB | wheel ≈161 MB（余 39 MB）；bge 90.8 MB / multi 240.5 MB | **PASS**（余量偏小） |
| 索引耗时 | 真实库全量 ≤20 min（单进程 CPU） | bge 2 h 15 min / multi 1 h 24 min（threads=4 外推）；threads×batch 网格最优也只有 147 min（§6），加线程反而更慢 | **FAIL（7×，无调参出路）** |
| 查询延迟 | hybrid P95 ≤800 ms | bge 11/15、multi 10/15 超标（语义单独 ≤8.8 ms，达标） | **FAIL**，成因是关键词通道 |
| 内存 | 检索常驻额外 RSS ≤500 MB | 基线 RSS 118.2 MB；bge 样本实测增量 364.1 MB，其中向量矩阵 18.2 MB ⇒ 模型+ORT 固定开销 345.9 MB，全库向量 314.7 MB（153,673×512×4 B）⇒ **外推 ≈660.6 MB**；multi 增量 601.8 MB − 矩阵 13.6 MB = 588.2 MB，全库向量 236.0 MB ⇒ **外推 ≈824.2 MB** | **FAIL**（bge 超线 32%，multi 超 65%；multi 数值受 bge 运行后 ORT arena 未回收影响，仅作上界） |
| CI/镜像 | 不装 fastembed 时 pytest/build/docker 全绿 | pytest 248 passed（`server/.venv`，exit 0）；本轮未改任何产品代码，build/docker 沿用 R6 提交时的绿状态，本轮未重跑 | **PASS**（pytest 实测；build/docker 见备注） |
| 隐私 | 除首次模型下载外无出站；来源与哈希有记录 | `HF_HUB_OFFLINE=1`，下载发生在 10-27 11:33，两个 onnx 的 SHA-256 见 §3；`pool` 阶段无出站 | **PASS** |

## 10. 结论（本页出稿时为建议，现已成签核）

- **最终结论：NO-GO**（2026-10-27）。依据是第 3 节「任一项不达标就是 no-go」，本轮三项不达标；四项决策的逐项选择与依据在 [B5-02-decision.md](B5-02-decision.md) §5 —— 不引入任何新依赖、不创建 `server/requirements-semantic.txt`、不生成 `0011` 迁移、不接任何 LLM。决策原始归属是用户，用户回复「你决定」后由执行方按原文规则定下；推翻该结论不需要重跑 spike，只需书面改写三条成本门槛。
- 本页出稿时的默认建议同样是 no-go。以下是**若将来改判 go** 才需要的最小条件清单（门槛改写属用户的权力，执行方不自行放宽）：
  1. 先修中文关键词通道（分词或让 trigram 索引容忍 2 字词，属 B5 之外的独立前置，已登记 F-15），否则 hybrid 永远达不到 800 ms；
  2. 把「全量 ≤20 min」改写为「单次后台任务 ≤20 min + 分批覆盖」，并明确首批覆盖范围（20 min ≈ 21,120 chunks ≈ 120–143 本）；
  3. 向量存储必须降位宽或分片 mmap：bge fp16 ≈157 MB、int8 ≈79 MB（全库 153,673×512），否则常驻门槛过不去；
  4. 模型二选一，并明确接受其截断特征（bge 丢 68% 切片的尾部 token；multi 每片只用 128 token）。
- **仍然确定不做的**：微调/训练、把向量塞进 SQLite BLOB 作为默认方案、未经书面授权接远程 LLM（`KB_RAG_ALLOW_REMOTE`）。
- no-go 收尾动作已按任务书执行：不写 B5-03~B5-12 代码；`progress.md` 标 SKIPPED 并附本页实测数字；README 的 D2/D3 改为 NO-GO 结论；**`docs/roadmap/RELEASE-2026-10.md` 目前并不存在**（B6-04 当年只出了 reports 里的收口文），故本轮不新建该文件，改为在 `progress.md` 的 B6-04 行钉一条硬要求：生成 RELEASE 时必须写「已评估语义检索，因索引耗时/常驻内存/hybrid 延迟三条门槛不达标而不实现」，不留 TODO 悬空；`.spike-venv/` 与 `spike-out/`（合计约 1.84 GB，含 spike.db 副本 1.33 GB）按任务书要求**等用户单独确认后**再删，本轮保留。

## 11. 附录 A · 人工相关性标注（文档级）

| 查询 | 判定为相关的文档 |
|---|---|
| q01 | `15d2e90d` 文明及其不满 |
| q02 | `ead0e349` 精神分析治疗实践指导；`f60c2df4` 译文心理分析作品集(套装共12册)；`414207ee` And How Does That Make You Feel? |
| q03 | `5a721d4a` 什么是权力 |
| q04 | `03c89e7f` 黑暗資本 |
| q05 | `65728d7e` 理想译丛045 滔天洪水：第一次世界大战与全球秩序的重建 |
| q06 | `777fd811` Cut Through the Hype. Master the Bas |
| q07 | `66a53efe` 大模型工程师面试 算法原理、开发实践与系统部署；`b558dafe` Mastering Machine Learning |
| q08 | `918af262` 孤独的堡垒；`1938fe3f` 苦难的意义；`4b07f2bd` 困境：现代男性愤怒的根源 |
| q09 | `03c89e7f` 黑暗資本 |
| q10 | `4be404d6` 增删卜易；`1de73980` 易隐；`7c061c25` 卜筮正宗 |
| q11 | `5a721d4a` 什么是权力；`de32e581` 人天生就是政治動物 政治哲學緒論 |
| q12 | `66a53efe` 大模型工程师面试；`f8fe879f` The Craft of Post-Training；`0bba8ba2` 为机器立心 |
| q13 | `ad09e946` Philosophy of Mind A Contemp；`caac9742` The Norton Introduction to P |
| q14 | `375f62c3` Quiet: The Power of Introverts |
| q15 | `5a721d4a` 什么是权力；`de32e581` 人天生就是政治動物；`caac9742` The Norton Introduction to Philosophy |

未列出的候选文档一律判 0。chunk 级判定（141 条）见 [B5-01-judgments.json](B5-01-judgments.json)。

## 12. 附录 B · 证据文件

| 文件 | 内容 | 是否入库 |
|---|---|---|
| `server/scripts/spike_semantic.py` | 全部测量逻辑（切分/嵌入/FTS/RRF/打分） | 是（任务书允许，仅脚本） |
| `docs/roadmap/spikes/B5-01-judgments.json` | 人工相关性判定明细 | 是 |
| `server/scripts/spike-out/retrieval.json` | meta / models / chunking / full_library_estimate / stats / latency / results | 否（gitignore，随 spike-out 删除） |
| `server/scripts/spike-out/relevance.md` | §8 表的原始输出 | 否（数值已抄入本页） |
| `server/scripts/spike-out/speed-grid.json` | §6 吞吐网格 | 否 |
| `server/scripts/spike-out/fts-split.json` | §7 关键词通道耗时分段 | 否 |
| `server/scripts/spike-out/{install,model-download,run}.log` | 安装 / 下载 / 索引日志 | 否 |
| `server/scripts/spike-out/{pool,judge-brief,query-candidates}.md` | 8,866 切片池、15 查询候选与判定阅读材料 | 否 |

磁盘占用合计：`.spike-venv` 175 MB + `spike-out/models` 332 MB + `spike.db` 1.33 GB ≈ **1.84 GB**，全部可再生，删除前需用户确认。
