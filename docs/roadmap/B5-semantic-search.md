# B5 · 语义检索 / RAG（**带 go/no-go 门槛**）

| 项目 | 值 |
|---|---|
| 批次 | B5 |
| 状态 | TODO（B5-02 前为**评估态**，不得先写功能代码） |
| 依赖 | B4 全部 DONE；G2 依赖授权；G3 迁移授权（`0011_semantic_search`） |
| 真实数据写入 | `document_chunks` / `document_embedding_state` + `{data_dir}/semantic/` 向量文件（全部为**可重建派生数据**） |
| 迁移 | `0011_semantic_search` |
| 新增依赖 | **仅在 go 之后**，且只进 `requirements-semantic.txt`，不进 `requirements.txt` |
| 完成标志 | B5-01/B5-02 必做；no-go 时以「评估结论」收尾也算 DONE（须在 progress 记偏离）；go 时 B5-03 ~ B5-12 全 DONE |

## 1. 目标

现有检索是 FTS5 trigram + ILIKE：字面匹配很强，但「为什么人会高估自己」搜不到讲「自我服务偏差」的段落；且 trigram 只索引 ≥3 字符的 token，短词直接回退 ILIKE 扫全表。

本批评估并（在达标时）落地：

1. 本地嵌入模型（不联网查询、不上传任何正文）。
2. 片段级（chunk）向量索引 + 增量重建。
3. `semantic` / `hybrid`（RRF 融合）两种检索模式，**默认仍是现有全文检索，不改行为**。
4. 证据式回答（extractive）：只给带原文引用的段落，不编造。
5. 生成式 RAG 作为**独立可选开关**，默认关，且默认只允许本机 LLM 端点。

## 2. 不做什么

- 不把语义检索做成默认搜索路径；不替换 FTS。
- 不在 `requirements.txt` / `server/Dockerfile` / CI 里引入嵌入依赖（CI 与镜像必须能在没有 fastembed 的机器上跑通）。
- 不使用任何云 embedding API（OpenAI/Cohere/…）：正文会离开本机，本项目的数据是用户全部藏书与笔记。
- 不做微调、不做训练。
- 不把向量塞进 SQLite BLOB 作为默认方案（除非 spike 证明 numpy 暴力检索过慢，才在 B5-03 讨论 `sqlite-vec`/`hnswlib`）。
- 生成式回答不接远程 API，除非用户明确书面授权并设置 `KB_RAG_ALLOW_REMOTE=1`。

## 3. 硬性门槛（B5-02 判 go/no-go 的唯一依据）

| 维度 | 通过线 | 不达标后果 |
|---|---|---|
| 相关性 | 15 条自拟中文查询里，semantic top-5 至少 60% 的查询命中人工判定为相关的段落；hybrid 在 ≥10/15 上不劣于任一单通道 | **no-go** |
| 安装体积 | 新增 wheel 合计 ≤ 200 MB；模型权重 ≤ 500 MB | no-go 或换更小的模型 |
| 索引耗时 | 真实库全量索引 ≤ 20 分钟（单进程 CPU） | 降低覆盖范围或 no-go |
| 查询延迟 | hybrid P95 ≤ 800 ms（本机，冷 shard 全载入后） | 换向量后端；仍超则 no-go |
| 内存 | 检索常驻额外 RSS ≤ 500 MB | 换 ANN 后端；仍超则 no-go |
| CI/镜像 | 不装 fastembed 时 `pytest`/`build`/`docker build` 全绿 | 必须修到全绿才能继续 |
| 隐私 | 除首次模型下载外无任何出站请求；模型下载来源与文件哈希有记录 | 阻断 |

**no-go 是合格产出**。执行方不得为了让 B5「有交付」而降低门槛或写半成品。

## 4. 前置条件（B5-01 即可开始，不需要授权）

1. B4 全部 DONE。
2. 磁盘可用空间 ≥ 3 GB（模型 + 向量 + 缓存）。
3. 本机 Python：`E:/zy/python/python.exe`（3.12）。
4. spike 一律用**独立虚拟环境或独立 venv 目录**，不污染当前 `server` 运行环境：

   ```bash
   E:/zy/python/python.exe -m venv E:/code/projects/library/.spike-venv
   E:/code/projects/library/.spike-venv/Scripts/python.exe -m pip install --upgrade pip
   ```

   `.spike-venv/` 必须确认已被 `.gitignore` 覆盖（`/data/`、`server/data/` 已在列，venv 需单独加一行）。
5. spike 阶段**不改仓库代码**（除 `.gitignore` 与 `docs/roadmap/`），产物写 `docs/roadmap/spikes/`。

## 5. 任务

### B5-01 技术 spike（只读评估，不改产品代码）

**产物**：

- 新增：[docs/roadmap/spikes/B5-01-embedding-spike.md](../../docs/roadmap/spikes/B5-01-embedding-spike.md)
- 新增（可提交，仅脚本）：[server/scripts/spike_semantic.py](../../server/scripts/spike_semantic.py)
- 修改：[.gitignore](../../.gitignore)（`.spike-venv/`、`server/scripts/spike-out/`）

**spike 必须回答的 6 个问题**：

1. **模型选型**：候选
   - `intfloat/multilingual-e5-small`（384 维，~470 MB，中英混合表现好）
   - `BAAI/bge-small-zh-v1.5`（512 维，~95 MB，中文强、英文弱）
   - 两者都用同一批 15 条查询跑，把结果并排写进报告，由用户选。
   - 记录：模型卡链接、许可证（必须允许本地推理与再分发权重缓存）、首次下载体积、`HF_HOME` 缓存位置。
2. **库选型**：`fastembed`（onnxruntime，无 torch）优先；若不支持所选模型，记录退化方案（`sentence-transformers` + torch → 体积超标，直接判该方案不通过）。
3. **切片策略**：在真实库抽 20 本（覆盖 epub/pdf/txt，正文长度分层）：
   - 目标：`chunk_size = 800` 字，`overlap = 120` 字，单文档上限 200 chunks，正文 < 200 字的文档跳过。
   - 记录：总 chunk 数估算、平均每本文档 chunk 数、超长 PDF 的 chunk 分布。
   - 中文不按句分（现有正文提取已丢段落结构），按字符窗口 + 标点回退切；写明实际用的切分函数。
4. **索引成本**：对 50 本真实书跑一遍嵌入，线性外推全库耗时；记录 CPU 线程数、批大小、是否 OOM。
5. **检索延迟**：把 50 本向量载入 numpy，暴力点积 top-k 的 P50/P95；外推 1298 本（约 40 MB 向量）。若 > 800 ms，测试 `hnswlib` 候选并记录其安装体积。
6. **质量对照**：15 条查询（由执行方基于用户藏书主题自拟，覆盖概念查询/人名查询/短词查询/英文查询）：
   - 每条同时跑 `GET /api/search?q=`（现状 FTS）与 semantic top-5，人工标注相关性 0/1。
   - 表格化：查询 / FTS top-5 命中数 / semantic top-5 命中数 / hybrid 命中数。

**spike 约束**：

- 只读真实库；**不得写 `knowledge_base.db`**，不得写 `server/data/`。spike 输出全部落 `server/scripts/spike-out/`（gitignore）。
- 若需要真实数据但避免污染，可复制库到临时位置只读打开。
- 模型下载是本批唯一允许的出站请求，必须在报告里写明时间、URL、文件 SHA-256。

**验收**：报告含 6 个问题的明确答案 + 门槛对照表 + 结论建议（go / go-with-conditions / no-go）。

---

### B5-02 go/no-go 结论与用户确认

**产物**：

- 新增：[docs/roadmap/spikes/B5-02-decision.md](../../docs/roadmap/spikes/B5-02-decision.md)
- 修改：[README.md](README.md) 的「未决事项 D2/D3」写成结论
- 修改：[progress.md](progress.md) 的「阻塞/授权」区

**必须让用户拍板的 4 件事**（逐条列出选项，不给「我帮你决定了」）：

1. **是否继续**（对照第 3 节门槛，未达标默认建议 no-go）。
2. **模型二选一**（体积/中英文表现的权衡）。
3. **依赖授权 G2**：`fastembed` + `onnxruntime`（+ 可能 `hnswlib`）写入 `server/requirements-semantic.txt`，并确认「不进 CI/镜像」。
4. **是否要生成式 RAG**（B5-11），以及本机是否已有可用 LLM 端点。

**no-go 收尾动作**（同样算完成本批）：

- 不写 B5-03 ~ B5-12 代码；`progress.md` 把 B5-03~B5-12 标 `SKIPPED` 并在「任务书偏离记录」写明原因与门槛实测数字。
- `docs/roadmap/README.md` 的 B5 状态改为 `NO-GO`，B6 的发布说明里记录「已评估语义检索，因 X 不实现」。
- 删除 spike venv 与 `spike-out/`（用户确认后再删）。

**验收**：结论页有用户明确回复记录（时间 + 选择），否则 B5-03 起不得开工。

---

### B5-03 依赖与 feature flags（仅 go 后）

**文件**：

- 新增：`server/requirements-semantic.txt`
- 修改：[server/app/config.py](../../server/app/config.py)
- 修改：[.gitignore](../../.gitignore)（`server/data/` 已覆盖向量目录，无需新增；确认 `{data_dir}/semantic/` 不入库）
- 修改：README（部署章节加「可选语义依赖」）

**`requirements-semantic.txt`**：

```
# 仅本地语义检索使用；不装进 requirements.txt / Docker / CI
fastembed==<spike 选定版本>
onnxruntime==<对齐版本>
# 仅当 spike 证明 numpy 暴力检索不达标时才有：
# hnswlib==<版本>
```

- **版本必须 pin 精确**（与主 `requirements.txt` 的 `>=,<` 风格不同，这里要可复现）。
- 装与不装都必须能让服务启动：所有 import 走惰性函数，不在模块顶层 import。

**配置新增**（全部默认关闭）：

```python
semantic_enabled: bool = False              # KB_SEMANTIC_ENABLED
semantic_provider: str = "fastembed"        # KB_SEMANTIC_PROVIDER（fastembed|fake）
semantic_model: str = "intfloat/multilingual-e5-small"  # KB_SEMANTIC_MODEL
semantic_dim: int = 384                     # KB_SEMANTIC_DIM
semantic_chunk_size: int = 800              # KB_SEMANTIC_CHUNK_SIZE
semantic_chunk_overlap: int = 120           # KB_SEMANTIC_CHUNK_OVERLAP
semantic_max_chunks_per_doc: int = 200      # KB_SEMANTIC_MAX_CHUNKS_PER_DOC
semantic_query_timeout_seconds: float = 5.0 # KB_SEMANTIC_QUERY_TIMEOUT
rag_generative_enabled: bool = False        # KB_RAG_GENERATIVE
ollama_base_url: str = "http://127.0.0.1:11434"  # KB_OLLAMA_BASE_URL
rag_allow_remote: bool = False              # KB_RAG_ALLOW_REMOTE
```

- 校验：`semantic_enabled=true` 且 `semantic_provider=fastembed` 时，若 `import fastembed` 失败 → **启动时 warning 并自动降级 `semantic_enabled=false`**，不是 crash（用户可能只是没装可选依赖）。
- `semantic_chunk_overlap` 必须 `< semantic_chunk_size`，否则启动失败。
- `rag_generative_enabled=true` 且 `rag_allow_remote=false` 时，`ollama_base_url` 的 host 必须是 `127.0.0.1/localhost/::1`，否则启动失败并说明原因。

**验收**：不装可选依赖时，`pytest tests -q`、`npm run build`、`docker build -t kb-server server` 全部通过；`GET /api/health` 的 `checks.semantic = "disabled"`。

---

### B5-04 迁移 `0011_semantic_search` + 模型

**文件**：

- 修改：[server/app/models.py](../../server/app/models.py)
- 新增：[server/alembic/versions/0011_semantic_search.py](../../server/alembic/versions/0011_semantic_search.py)
- 新增：[server/tests/test_semantic_migration.py](../../server/tests/test_semantic_migration.py)

**表结构以 [data-model.md](data-model.md) 第 5 节为准**：`document_chunks`（`uq_document_chunks_doc_index`）与 `document_embedding_state`。

**要求**：

- `down_revision = "0010_reading_queue_reviews"`，幂等（inspector 判断）。
- 迁移**不生成任何 chunk**，不碰向量目录：建表后行数为 0 是正确状态。
- `document_chunks.text` 是派生数据，可随时 `DELETE + 重建`；不给它加全文索引（与 `documents_fts` 混淆）。
- FK 一律 `ON DELETE CASCADE`：删文档 → 删 chunk 行 → 向量 shard 由索引任务的「孤儿 shard 清理」删除（见 B5-06）。
- `downgrade()`：删 `document_embedding_state` → 删 `document_chunks`；**不删向量目录**，在迁移 docstring 里写明手动清理命令：
  ```bash
  # 仅在明确要彻底移除语义索引时执行（会删派生数据，需用户确认）
  rm -rf server/data/semantic
  ```

**测试**：表/索引/唯一约束存在；重复 upgrade 不报错；删文档级联删 chunk；downgrade 后表消失且 `documents` 不变。

---

### B5-05 嵌入 provider 与向量存储抽象

**新增**：

- [server/app/services/semantic/__init__.py](../../server/app/services/semantic/__init__.py)
- [server/app/services/semantic/chunking.py](../../server/app/services/semantic/chunking.py)
- [server/app/services/semantic/providers.py](../../server/app/services/semantic/providers.py)
- [server/app/services/semantic/vector_store.py](../../server/app/services/semantic/vector_store.py)
- 新增：[server/tests/test_semantic_chunking.py](../../server/tests/test_semantic_chunking.py)、[server/tests/test_semantic_vector_store.py](../../server/tests/test_semantic_vector_store.py)

**`chunking.py`**：

```python
def split_text(text: str, *, chunk_size: int, overlap: int, max_chunks: int) -> list[tuple[str, int, int]]:
    """返回 (片段, start_char, end_char)，end_char 不含"""
```

- 优先在 `[chunk_size*0.6, chunk_size]` 区间内的最后一个标点（`。！？；\n`，英文 `.!?`）断开；找不到就硬切。
- `overlap` 用于相邻片段的重复部分；**必须保证 `start_char/end_char` 与原文严格对齐**（阅读器高亮跳转要用）。
- 超过 `max_chunks` 截断并在索引任务里记 `truncated=true`（报告可见）。

**`providers.py`**：

```python
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dim: int
    def embed_documents(self, texts: list[str]) -> "np.ndarray": ...   # [n, dim] float32, 已 L2 归一化
    def embed_query(self, text: str) -> "np.ndarray": ...               # [dim]

def get_provider() -> EmbeddingProvider: ...   # 依 settings；fastembed 惰性 import
```

- `FakeProvider`：`hash(text) -> 确定性向量`，**测试与 CI 唯一允许的 provider**，`semantic_provider=fake` 时启用。
  - 必须做维度校验：`dim` 与 `settings.semantic_dim` 不符直接抛错（防止向量文件与模型串台）。
- e5 系模型要求 query/passage 前缀（`query: ` / `passage: `）；前缀只加在**嵌入输入**上，`document_chunks.text` 存原文，不存带前缀版本。
- 输出统一 L2 归一化 → 点积即余弦相似度，`vector_store` 不需要再算 norm。

**`vector_store.py`**：

```python
class VectorStore(Protocol):
    def put(self, document_id: str, matrix) -> None: ...       # 写该文档 shard
    def delete(self, document_id: str) -> None: ...
    def search(self, query_vec, k: int, allowed_ids: set[str] | None) -> list[tuple[str, int, float]]:
        """返回 [(document_id, chunk_index, score)]"""
    def manifest(self) -> dict: ...
    def drop_all(self) -> None: ...      # full 重建用
```

- 默认 `NumpyShardStore`，目录结构严格按 [data-model.md](data-model.md) 第 5.3 节。
- `manifest.json` 字段：`schema_version`、`model`、`dim`、`vector_format`、`created_at`、`updated_at`、`doc_count`。
  - **加载时校验**：`model` 或 `dim` 与当前 settings 不一致 → 拒绝查询，`/api/semantic/status` 返回 `vector_backend="mismatch"` 并提示「模型已变更，请全量重建」；不得静默用错误向量。
- shard 写入必须原子：写 `xxx.tmp` 再 `os.replace`。
- 载入策略：首次查询时把所有 shard 拼成一个 `float32` 大矩阵 + `doc_id/chunk_index` 索引数组，缓存在进程内单例；`document_embedding_state.indexed_at` 变化时才重载（按 max(indexed_at) 判断，不做文件监听）。
- 检索前先按 `type`/软删除过滤 `allowed_ids`（SQL 取候选 id 集合），**不要把已软删除的文档返回给用户**。
- 若 spike 选了 `hnswlib`：同样实现这四个方法，切换只在 `get_vector_store()` 里发生，其他代码不改。

**测试**（全部用 `FakeProvider` + 临时 `KB_DATA_DIR`）：

- chunking：overlap 正确、`start/end` 与 `content[a:b] == chunk.text` 逐字符断言、超长截断、空文本返回 `[]`。
- store：put/search 分数顺序、`allowed_ids` 过滤、manifest 不匹配拒绝、原子写（写中途异常不留 `.tmp`）。

---

### B5-06 增量索引后台任务

**文件**：

- 新增：[server/app/services/semantic/index_job.py](../../server/app/services/semantic/index_job.py)
- 修改：[server/app/main.py](../../server/app/main.py)（注册 `semantic` 路由，见 B5-07）
- 新增：[server/tests/test_semantic_index_job.py](../../server/tests/test_semantic_index_job.py)

**要求**：

- 复用 B0 的 [JobState](B0-foundation.md)：`JobState("semantic_index")`，单飞、可轮询、失败有 `error`。
- 处理范围：`deleted_at IS NULL AND content 非空 AND length(trim(content)) >= 200` 的文档（当前基线下 1298 本里约 717 本有正文——数字在 B5 报告里重新统计）。
- 增量判据：`document_embedding_state.content_sha256` 是 **`documents.content` 的 SHA-256**（不是文件哈希，别和 `documents.sha256` 混）。一致则跳过。
- 每文档流程：切 chunk → 删旧 chunk 行 → 插入新行 → 批量嵌入（batch 32）→ 写 shard → 写/更新 state → commit。
- **失败处理**：单文档失败只记 `failed += 1` 与 `last_error`，不中断整批；连续 20 个失败则中止任务并置 `error`（说明环境有问题，继续跑没意义）。
- 进程预算：嵌入是 CPU 密集，走独立线程并遵守 `KB_SEMANTIC_QUERY_TIMEOUT` 之外的整体时间预算；任务运行期间 `/api/semantic/index` 再启动返回 409。
- **孤儿 shard 清理**（`full=true` 之外每次增量后跑一次）：`shards/` 里存在但 `document_embedding_state` 中不存在（或文档已软删除）的 shard → 删除。**只删 `semantic/shards/` 下的文件，且必须在删除前把清单写进日志与状态**（`pruned_shards: [...]`），不得递归、不得碰 `files/` 与 `covers/`。
- 每 20 本更新一次 `current_title`，供 UI 显示。

**测试**：Fake provider 下增量幂等（跑两次第二遍 `skipped=all`）；内容变化触发重建；软删除文档的 shard 被清理且 `files/` 不受影响；单文档失败不中断；连续 20 失败中止；409 单飞。

---

### B5-07 语义/混合检索 API

**文件**：

- 新增：[server/app/routers/semantic.py](../../server/app/routers/semantic.py)
- 修改：[server/app/routers/search.py](../../server/app/routers/search.py)（只**新增** hybrid 融合函数，不改 `GET /api/search` 行为）
- 修改：[server/app/main.py](../../server/app/main.py)
- 修改：[server/app/main.py](../../server/app/main.py)（健康检查：在 `/api/health` 的 `checks` 里加 `semantic` 字段，取 `disabled/ok/missing/error`；不改现有 `status` 语义）
- 新增：[server/tests/test_semantic_api.py](../../server/tests/test_semantic_api.py)

**端点**（契约见 [api-contracts.md](api-contracts.md) 第 8 节）：

| 端点 | 说明 |
|---|---|
| `GET /api/semantic/status` | 开关、provider、模型、维度、后端、已索引/总数、chunk 数、最后索引时间 |
| `POST /api/semantic/index` | `{ids?, full?, confirm?}`；`full=true` 必须 `confirm=true`，否则 400 |
| `GET /api/semantic/index-status` | `JobState` 快照 + `current_title` + `pruned_shards` 计数 |
| `GET /api/search/semantic` | `q`、`mode=semantic|hybrid`、`type`、`limit` |

**关闭时行为（最重要的一条）**：

- `KB_SEMANTIC_ENABLED=0` → 四个端点全部 `503 {"detail":"semantic search disabled"}`，**且不 import fastembed、不加载模型、不读向量目录**。
- 判定发生在路由函数入口，不在模块 import 期，否则「默认关闭」会变成「默认启动失败」。

**`hybrid` 融合**：

- 词法通道：直接调用现有 `search()` 的函数体（抽成可复用函数 `_run_lexical(db, q, type, limit)`），**不改其 FTS/ILIKE 判定逻辑**。
- 语义通道：`VectorStore.search` top `limit*2`。
- RRF：`score = Σ 1/(60 + rank)`，同 `document_id` 合并；`channels` 标注来自哪几路。
- 片段：命中多个 chunk 时取分数最高的 chunk 文本，压缩为 ≤200 字摘要（复用 [crud.summarize_content](../../server/app/routers/documents/crud.py#L33)）。
- 语义查询向量做进程内 LRU（100 条，key = `model + q`）：翻页/重复查询不该重复嵌入。
- 超时：语义通道受 `semantic_query_timeout_seconds`；超时返回**词法结果 + `"degraded": "semantic_timeout"`**，不 500。

**测试**（Fake provider）：

- 关闭 → 503；开启但索引空 → `semantic` 返回 `index_empty=true` 空数组，`hybrid` 等同词法结果。
- `GET /api/search` 的既有测试**一条都不改也必须通过**（行为不变回归门）。
- RRF 融合顺序正确（构造词法第一/语义第一的两个文档）；软删除文档不出现在两路结果。
- 鉴权：无 token 401。

**验收**：真实库开启语义后，第 3 节 15 条查询里的概念型查询（如「高估自己」）能召回 FTS 召不到的段落，并在报告里贴出前后对比。

---

### B5-08 搜索页模式切换 UI

**文件**：

- 修改：[src/app/search/page.tsx](../../src/app/search/page.tsx)
- 新增：[src/lib/api/semantic.ts](../../src/lib/api/semantic.ts)
- 新增：[src/components/search/SemanticBadge.tsx](../../src/components/search/SemanticBadge.tsx)
- 新增：[src/components/__tests__/SearchModeToggle.test.tsx](../../src/components/__tests__/SearchModeToggle.test.tsx)

**要求**：

- 模式三选：`全文`（默认，等于现状）/ `语义` / `混合`。
  - 默认值**必须是 `全文`**，用户没开语义时界面与改造前一致。
  - 语义不可用（`/api/semantic/status` 关闭或未索引）时：另两个选项 `disabled` + 说明「语义索引未启用」，并给「去设置」链接；不隐藏，避免用户以为功能坏了。
- 结果卡显示 `channels`（`[semantic, lexical]` → 「语义+全文」小徽标）与 `score`（保留两位）。
- 片段来自后端 `snippet`，**前端不做二次高亮猜词**（现状前端如何高亮保持不动，只在语义模式下显示后端给的片段）。
- 点片段跳阅读器时带 `?position=`，用 chunk 的 `start_char` 换算不了页码——因此后端返回 `document_id` 即可，跳阅读器默认位置；这条在 UI 文案里不承诺「定位到原句」。
- URL 参数 `?mode=semantic`，与 B3 的 URL 风格一致；沿用现有 300ms 防抖，不额外加请求。
- 空索引时显示引导：「先建立语义索引（设置 → 语义检索）」而不是「没有找到结果」。
- 移动端：徽标不换行；模式切换用 `<select>`（与 B3 排序一致，可访问性优先）。

**测试**：mock status 关闭 → 选项禁用且默认全文；mode=semantic 请求走 `/api/search/semantic`；`channels` 渲染；现有全文检索测试不受影响。

---

### B5-09 设置页语义索引卡片

**文件**：

- 修改：[src/app/settings/page.tsx](../../src/app/settings/page.tsx)
- 修改：`CHECK_LABELS`（补 `semantic`，B1-10 已提过标签，这里补语义索引专卡）

**卡片内容**：

- 状态行：`enabled` / `provider` / `model` / `dim` / `vector_backend` / `documents_indexed / documents_total` / `chunks` / `last_indexed_at`。
- 操作：
  - 「增量索引」→ `POST /api/semantic/index {}`
  - 「全量重建」→ 二次确认弹窗，输入 `REBUILD` 才启用按钮（与 B2 的破坏性动作确认风格一致），发 `{full:true, confirm:true}`
  - 运行中每 2 秒轮询 `index-status`，显示 `done/total` 与 `current_title`
- 明确文案：
  - 「全部在本机完成，正文不会离开这台机器」
  - 「索引是可重建的派生数据，删除它不会影响任何书籍或笔记」
  - 「模型文件约 XXX MB，首次需联网下载一次」（数字来自 B5-01）
- 生成式 RAG 若 `KB_RAG_GENERATIVE=1` 才显示第二张卡（B5-11）。
- 关闭状态：卡片显示「未启用（需设置 `KB_SEMANTIC_ENABLED=1` 并安装可选依赖）」，附 `docs/roadmap` 里写好的安装命令，**不在 UI 里提供任何自动 pip 安装的按钮**。

**验收**：索引过程中刷新页面能恢复进度显示；全量重建在未输入 `REBUILD` 时不可提交。

---

### B5-10 证据式回答（extractive RAG，无生成模型）

**文件**：

- 新增：`server/app/services/semantic/rag.py`
- 修改：[server/app/routers/semantic.py](../../server/app/routers/semantic.py)（加 `POST /api/rag/answer`）
- 修改：[src/app/search/page.tsx](../../src/app/search/page.tsx)（「答案卡」区，仅 `mode=semantic|hybrid` 时显示）
- 新增：[server/tests/test_rag_extractive.py](../../server/tests/test_rag_extractive.py)

**答案构造（这是「证据式」的关键，不能含糊）**：

1. 取 hybrid top-`top_k`（默认 5）。
2. 每条渲染：`《书名》· 第 N 段（start_char）` + 原文引用（≤300 字，逐字来自 `document_chunks.text`）。
3. `answer` 字段是**这些引用的拼接文本**，模板固定：

   ```text
   根据库内 <n> 处原文：
   1. 《书名》：「……」
   2. 《书名》：「……」
   ```

4. **不生成任何未在原文出现的句子**，不做改写、不做总结、不做推理结论。函数名与返回 `mode="extractive"` 就是这个承诺；如果有人想「顺手加个摘要」，那属于 B5-11 且必须单独开关。
5. 无命中 → `200 {"mode":"extractive","answer":"","citations":[]}`，前端显示「库里没有找到相关原文」，**不得**由前端编一段话。
6. `citations` 必含 `document_id/title/chunk_index/quote/score`；`quote` 超过 300 字截断加省略号。
7. 限流沿用第 10 节 `/api/rag/answer：20/60s`。

**验收**：断言返回的每个 `quote` 都能在对应 `document_chunks.text` 里逐字找到（测试用 SQL 复核）。

---

### B5-11 生成式 RAG（可选，默认关，单独授权）

> 只有在 B5-02 里用户明确选择「要生成式」时才做；否则整节标 `SKIPPED`。

**文件**：

- 新增：[server/app/services/semantic/llm.py](../../server/app/services/semantic/llm.py)
- 修改：`rag.py`（`mode="generative"` 分支）
- 修改：[server/app/config.py](../../server/app/config.py)（已在 B5-03 定义好开关）

**安全要求（逐条必须实现）**：

- 只允许本地端点：`ollama_base_url` 的 host ∈ `{127.0.0.1, localhost, ::1}`；否则 `rag_allow_remote=true` 才放行。这一条**优先于任何用户配置便利**，因为请求体里含用户藏书原文。
- 请求复用现有 [httpx](../../server/requirements.txt)（不新增 LLM SDK）。
- 提示词固定模板，明确「只用给定原文回答；没有依据就回答『库里没有依据』；不得编造书名或页码」，并把 citations 传回前端与生成文本并列展示。
- 生成超时（默认 30 秒）、响应大小上限（默认 1 MB）、失败即回退 `extractive` 并在响应里标 `degraded`。
- 日志**不记录提示词正文**（含用户藏书内容），只记录长度与命中数量。
- SSRF 二次防护：即使 `rag_allow_remote=true`，也要拒绝 `file://`、云 metadata 地址（`169.254.169.254`）与非 http(s) scheme；与现有剪藏功能的 `allow_local_clip` 防护思路一致。

**验收**：默认配置下 `KB_RAG_GENERATIVE=0`，端点返回 `mode="extractive"`；配一个假本地端点（测试用 `respx`/monkeypatch）验证模板、超时回退与 remote 拒绝。

---

### B5-12 测试、隐私检查、B5 报告

**后端**：

```bash
cd server
E:/zy/python/python.exe -m pytest tests -q                              # 不装可选依赖，必须全绿
E:/zy/python/python.exe -m pytest tests -q -k semantic                  # Fake provider 路径
SEMANTIC=1 E:/zy/python/python.exe -m pytest tests -q                   # 若 CI 环境仍无 fastembed，跳过并注明
```

**必须验证「不装也能跑」**：

```bash
E:/zy/python/python.exe -c "import app.main"   # server 目录下，venv 不含 fastembed
```

**前端**：`npm run typecheck && npm run lint && npm test && npm run build`

**隐私与外联检查**（B5 独有，必须做）：

1. `pip-audit` 对 `requirements-semantic.txt` 单独跑一次（不进 CI 门，但结果进报告）。
2. 抓一次全量索引期间的网络连接：确认除模型下载外无出站请求（写明验证方式与结论）。
3. 报告记录：模型名、来源 URL、权重 SHA-256、磁盘占用、首次下载耗时。
4. 检查 `server/data/semantic/` 未被 git 跟踪：
   ```bash
   git check-ignore -v server/data/semantic/manifest.json
   git status --porcelain | grep -i semantic    # 必须为空
   ```

**性能实测**（真实库，手动）：

| 指标 | 门槛 | 实测 |
|---|---|---|
| 全量索引耗时 | ≤ 20 min | |
| 索引峰值 RSS | ≤ 1 GB | |
| hybrid 查询 P50 / P95 | ≤ 800 ms（P95） | |
| 向量目录大小 | 记录 | |
| 覆盖文档数 / chunk 数 | 记录 | |

**报告**：`docs/roadmap/reports/B5-2026-10-26.md`，除 [00-execution-rules.md](00-execution-rules.md) 第 6 节模板外必须包含：

- B5-02 决策页链接 + 用户确认原文。
- 15 条查询的 FTS / semantic / hybrid 命中对照表（这是本批唯一能证明「值不值」的证据）。
- 门槛逐项达标/未达标结论。
- 隐私检查结论。
- no-go 时：结论页 + 全部 `SKIPPED` 说明 + 清理动作（删 venv、删 spike-out）。

更新 [progress.md](progress.md)、[README.md](README.md)。

## 6. 验收清单（go 路径）

- [ ] spike 报告含 6 问答案与门槛对照；决策页有用户确认。
- [ ] 不装可选依赖时，后端测试、前端构建、镜像构建全绿。
- [ ] `KB_SEMANTIC_ENABLED=0` 时四个端点 503 且不加载模型。
- [ ] `0011` 迁移幂等、可降级；建表后不产生任何 chunk。
- [ ] chunk 的 `start_char/end_char` 与原文逐字符对齐（有断言测试）。
- [ ] 向量 manifest 的 `model/dim` 与 settings 不符时拒绝查询并提示重建。
- [ ] 增量索引幂等、单飞、可中断；孤儿 shard 清理只碰 `semantic/shards/`。
- [ ] hybrid 用 RRF，同文档合并且标注 `channels`；超时降级不 500。
- [ ] `GET /api/search` 行为与改造前完全一致（既有测试一条不改）。
- [ ] extractive 答案的每一句都可在 `document_chunks.text` 逐字定位；无命中返回空答案。
- [ ] 生成式默认关；本地端点强校验；远程需显式开关；日志不落提示词。
- [ ] 搜索页默认落在「全文」；语义不可用时给出明确引导而非空结果。
- [ ] 向量目录与模型缓存不进 git；隐私出站请求检查有结论。
- [ ] 性能门槛实测数字写入报告（未达标不得谎称通过）。

## 7. 回滚

1. 代码：`git revert <B5 commit>`；可选依赖从 `requirements-semantic.txt` 移除（主 `requirements.txt` 本就没动，Docker/CI 无需回滚）。
2. 迁移：`alembic downgrade 0010_reading_queue_reviews`（删两张派生表）。
3. 向量文件：数据库降级**不会**删除 `{data_dir}/semantic/`；需用户确认后手动删除，命令写进报告。
4. 模型缓存（`HF_HOME` / `~/.cache`）：可保留，删除与否由用户决定，报告写明路径与大小。
5. 回滚后语义端点自然 503，`GET /api/search` 不受影响——这正是把语义放在独立端点的原因。

## 8. 交给 B6 的东西

- 语义索引是否上线、性能数字、已知限制（模型变更需全量重建、chunk 位置不能精确定位到阅读器页码）。
- `docs/roadmap/spikes/` 两份文档需要在 B6 的发布说明里链接出来。
