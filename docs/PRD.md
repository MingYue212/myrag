# 个人 RAG 知识库 · PRD

> 这份文档是**从现有代码倒推**出来的，不是先写需求再实现。
> 作用是：把零散的调参经验、踩过的坑、待定的技术决策沉淀成一份可以逐步修改的活文档。
>
> **使用约定**
> - 每次改完，更新顶部的「最后修订」
> - 尚未验证的推断一律标 🔍，别当成结论
> - 决策记录（DR-xxx）一旦选定就不再改史实，新的推翻旧的请另开一条

**最后修订**：2026-10-12
**当前版本对应**：LightRAG 1.5.7 · commit `6d36230` · 21 篇文档

---

## 1. 产品定位

| 项 | 内容 |
|---|---|
| 一句话 | 面向个人资料（技术文档、PDF、笔记、代码）的**私有问答系统**，不依赖不可替代的公网服务 |
| 使用场景 | 攒一批资料后反复提问；资料会持续增长到 100+ 篇 |
| 质量优先级 | 检索质量 > 响应速度 > 成本。单次查询 5~20 秒可接受，答案不完整不可接受 |
| 非目标 | 不做多人共享、不做生产级高可用、不做移动端、不追求毫秒级延迟 |

### 规模假设（写预算时要明确）

> 这一节是**估算前提**，实际情况变了要同步改下面的数字。

- 文档量：21 篇 → **100+ 篇**（约数十万 ~ 百万级 token 规模）
- 查询频次：个人自用，**每天 20~50 次**
- 并发：**1**，偶发 2~3

---

## 2. 现状盘点

### 2.1 部署形态（混合）

| 环节 | 位置 | 模型 / 服务 | 状态 |
|---|---|---|---|
| Embedding | 本机 ollama | bge-m3，1024 维 | ✅ 正常 |
| LLM | 云端 OpenAI 兼容接口 | claude-sonnet-5 | ✅ 正常 |
| Rerank | 云端阿里云 DashScope | qwen3-rerank | ❌ 已关闭（详见 §5） |
| 向量库 / KV / 图 | 本机 JSON 文件 | `data/knowledge/` | ✅ 正常 |

⚠️ 当前是纯 JSON 文件存储。到 100+ 篇规模，**JSON 全量读写会成为明显瓶颈**，这是规模化的第一个硬约束。

### 2.2 资产

- 文档：21 篇，**全部 processed**
- 图谱：5935 节点 / 2398 边
- 磁盘：`data` 154M · `input` 89M
- 本机模型：`qwen3:4b`、`bge-m3`、`deepseek-v3.1:671b-cloud`（**没有任何 reranker**）

### 2.3 数据到底存在哪、什么格式

一句话：**全部是 `data/knowledge/` 下的明文 JSON + 一个 GraphML**，没有任何数据库。

| 文件 | 大小 | 装的是什么 | 丢了怎么办 |
|---|---|---|---|
| `vdb_relationships.json` | **68M** | 关系向量，1024 维 | 要重新 embed（免费）+ 重新抽实体（花钱） |
| `vdb_entities.json` | **49M** | 实体向量，1024 维 | 同上 |
| `kv_store_llm_response_cache.json` | 18M | **LLM 实体抽取的结果缓存**（885 条） | 重新入库要**重付一遍 LLM 费用** |
| `graph_chunk_entity_relation.graphml` | 6.9M | 知识图谱本体（标准 XML，可用 Gephi 打开） | 同上 |
| `vdb_chunks.json` | 4.4M | 切片向量，1024 维 | 重新 embed（本地免费，很快） |
| `kv_store_relation_chunks.json` | 2.1M | 关系 → chunk 反向索引 | 重建 |
| `kv_store_text_chunks.json` | 1.8M | 切片原文 | 重建 |
| `kv_store_full_docs.json` | 1.5M | 文档全文 | 重建 |
| `kv_store_entity_chunks.json` | 1.4M | 实体 → chunk 反向索引 | 重建 |
| `kv_store_full_relations.json` | 592K | 关系的完整描述 | 重建 |
| `kv_store_full_entities.json` | 164K | 实体的完整描述 | 重建 |
| `kv_store_doc_status.json` | 52K | 21 篇文档的处理状态 | 重建（会导致重复计费） |

附带两份**不在 `data/` 下**的东西：

- `input/`（89M）：21 个原始 md/docx，**都已解析**，产物放在 `input/__parsed__/`
- `data/knowledge/scan_spool/`：入库时的临时中转目录，**可以安全删除**（正常流程会自己清理）

> 💡 **最重要的一个数字：117M 里只有 4.4M 是切片向量，其余 96% 是实体/关系向量。**
> 这是 GraphRAG 的固有开销——每个文档都要抽出实体和关系并各自向量化。
> 意味着 100+ 篇时这块会涨到 **500M~1G 量级**，而且每次写入都要全量读写 JSON。这是 §4 里 Q4 的真实来源。

> 🔒 **备份建议**：不能只备份 `input/`。真正贵的是 `data/knowledge/`——尤其是 `kv_store_llm_response_cache.json`（它是你花出去的钱）。整体 154M，随手 tar 一份即可。

---

## 3. 诊断快照：2026-10-12

本次排查的结论，附证据，便于日后回看。

### 3.1 已执行的动作

| 动作 | 结果 |
|---|---|
| 停止 lightrag-server（9621） | ✅ 已停；Ollama 保留运行（embedding 依赖） |
| `RERANK_BY_DEFAULT` → `false` | ✅ 已改，原文备份在 `.env.bak-before-rerankoff` |
| 备份 `.env` | ✅ `.env.bak-before-rerankoff` |

### 3.2 重排链路彻底失效（根因：账号欠费，不是代码）

实弹调用证据：

```json
HTTP 400 {"code":"Arrearage","message":"Access denied, please make sure your account is in good standing"}
```

- 起于 2026-10-11 23:33，日志中累计 42 次
- 失败极快（0.08s），说明请求已正确到达并被识别——**说明之前打的 `patch_rerank.py` 协议补丁是成功的**，问题纯粹在账号状态
- 因为 `RERANK_BY_DEFAULT=True`，每次查询都要重试 3 次（`wait_exponential(min=4)`），**白等 8~12 秒**后才降级为原始顺序

> 关闭重排后：少等 10 秒，**检索质量零损失**——因为本来就没重排成功过。

### 3.3 发现：`.env` 的部分参数对查询不起作用

**现象**：`.env` 写了 `TOP_K=60`、`CHUNK_TOP_K=15`，但日志里跑出来的是 40 和 20。

**原因**：

1. 服务端构造查询参数是 `QueryParam(**request_data)`——**完全由 HTTP 请求体决定**
2. 前端不传某个参数时，落到 `QueryParam` 的默认值：`top_k=40`、`chunk_top_k=20`、`max_total_tokens=30000`
3. `QueryParam` 的这些默认值在 **import 时**通过 `get_env_value("TOP_K", ...)` 求值；而 `config.py` 里 `load_dotenv(".env")` 的位置**晚于**部分模块的 import
4. 结果：`LightRAG` 类读到了 60/15，`QueryParam` 却是默认的 40/20，**查询走的是后者**

### 3.3.1 三条生效路径（任选其一）

| 路径 | 怎么做 | 何时生效 | 适合 |
|---|---|---|---|
| **① 官方管理台界面** | `/webui/` 的检索参数面板 | 立即 | **日常调参，最推荐** |
| **② 直接发 API** | 请求体里带 `"top_k": 60` | 立即 | 脚本、评测、A/B 测试 |
| **③ `start.sh` 里 export** | `export TOP_K=60` 等 | 重启后 | 想让默认值全局固定 |

**为什么界面调一定生效**：服务端是 `QueryParam(**request_data)`，请求体里有的字段**直接覆盖**一切默认值，优先级最高。

**为什么界面没有就等于白配**（2026-10-12 实测）：

- 官方管理台 `/webui/`：前端产物里确实含 `top_k` / `chunk_top_k`，README 也写了它有「检索参数」面板 ✅ **可用**
- 自建界面 `web/index.html`：只有 `mix / local / global / naive` 四个模式按钮，**没有任何数值参数控件** ❌
  → 所以走自建界面时，`top_k` 永远等于默认的 40

**想让自建界面也能调**，在 `web/index.html` 的请求体里加两个字段即可，服务端不用改：

```js
// 现在只有 mode，补上这两个
query_mode: mode,
top_k: 60,              // ← 新增
chunk_top_k: 15,        // ← 新增
```

> 更好的做法是加两个输入框，把值做成可调——这样评测时不用每次改代码重启。

**最后的坑**：改完 `.env` 或 `start.sh`，**必须重启才生效**（README 也这么说）。

### 3.4 多轮历史 ≠ 查询改写

LightRAG 源码里的原话（API 文档字符串）：

> `conversation_history` parameter is sent to LLM only, **does not affect retrieval results**.

意思是：历史对话**只**在生成阶段塞给 LLM 让模型理解指代，**检索阶段用的永远是当前这一句原始 query**。

日志里有旁证——短/含糊的查询会触发关键词抽取失败：

```
WARNING - low_level_keywords is empty
WARNING - high_level_keywords is empty
WARNING - Forced low_level_keywords to origin query: 最终验证：普通回车
```

后果：第二轮问「它有什么副作用？」时，系统拿这句去向量召回，「它」没有指代对象，召回质量崩。**这是目前最伤实际体验的一个缺口。**

### 3.5 无查询改写

在 `operate.py` 全量检索 `rewrite` 关键字，唯一命中是一处无关 docstring（`rebuild_policy`）。**LightRAG 1.5.7 没有内置任何 query rewrite / query expansion。**

---

## 4. 问题清单

优先级：**P0 = 影响结果正确性** · P1 = 影响质量/体验 · P2 = 整洁与规模化准备

| ID | 优先级 | 问题 | 现状 | 处理 |
|---|---|---|---|---|
| Q1 | P0 | 重排失效 + 每次白等 10s | 已关闭 | ✅ 已止血，见 §5 选长期方案 |
| Q2 | P0 | 多轮无改写，第二轮检索崩 | 缺口 | ☐ 见 §8 |
| Q3 | P1 | `.env` 参数不生效 | 已定位 | ☐ §3.3 的 export 修法 |
| Q4 | P1 | JSON 存储扛不住 100+ 篇 | 隐患 | ☐ 规模 50 篇前评估换 PG/Qdrant |
| Q5 | P1 | 无检索质量评测 | 从未跑过 | ☐ 见 §6.3，工具现成 |
| Q6 | P2 | 切片偏大（1200 token） | 需评测佐证 | ☐ 见 §7 |
| Q7 | P2 | README 说「现有 3 篇」 | 已过时 | ☐ 改为 21 篇并补 TS |
| Q8 | P2 | 无费用/用量观测 | 缺失 | ☐ 随手记账，或查阿里云账单 |

---

## 5. 决策 DR-1：重排方案怎么选

### 5.1 三个选项

| | A. 关闭重排 | B. 云端 API | C. 本地服务 |
|---|---|---|---|
| 月成本（估） | 0 | ¥20~35（见 §5.2） | 0（电费忽略） |
| 单次延迟 | 0 | 0.3~1s | 0.6B 约 0.3~1s，4B 约 1~3s（Apple Silicon） |
| 检索质量 | 基线 | 明显优于基线 | 同 B（模型同量级） |
| 隐私 | 最好 | 原文片段要出网 | 最好 |
| 运维 | 无 | 无 | **要多起一个常驻服务** |
| 欠费风险 | 无 | **有（刚踩过）** | 无 |

### 5.2 成本测算（量级估算，以 §1 假设为前提）

阿里云 qwen3-rerank 官价约 **0.5~0.9 元 / 百万 tokens**（不同产品线口径略有差异）。

- 单次查询输入：40 个候选 × ~1000 token ≈ **4 万 token**
- 单次成本：4万 / 100万 × 0.5~0.9 ≈ **¥0.02~0.036**
- 每月（30 次/天 × 30 天 = 900 次）：**约 ¥18~33**

> 💡 关键判断：**重排的成本相对 LLM 可以忽略**。
> 同样一次查询，送 claude 的 final context 约 12k token，成本是重排的 10~20 倍。
> 所以这不是省钱的问题——是**欠费把功能搞挂**的问题。要么备一个第二家供应商做兜底（切换成本很低），要么干脆本地化。

### 5.3 推荐路径（分阶段，不一次押注）

1. **现在**：关闭重排（已做）。用这段时间补上检索评测（Q5），先把基线测出来。
2. **50 篇之前**：充值 / 换一个还能用的云端供应商。改动极小——`ali_rerank` 会把 `base_url` 直接透传给 `generic_rerank_api`，只要新服务走 OpenAI 兼容 `/reranks`、返回 `{"results":[{"index":N,"relevance_score":X}]}`，改 `RERANK_BINDING_HOST` 就行，**不用动代码**。
3. **100 篇之后 / 或对隐私有硬要求**：本地化。

> 🔍 **注意**：LightRAG 的 rerank 只有 `ali / cohere / jina` 三个 binding，**没有 ollama**，而 Ollama 本身也没有原生 rerank 端点（`dengcao/` 那批模型只能借 `/api/embed` 间接做，不是真正的 cross-encoder）。
> 想本机真跑重排，得额外起一个服务：**llama.cpp server / vLLM / Infinity / TEI** 都可以。

### 5.4 本地部署资源参考

| 模型 | 参数 | 磁盘 | 特点 |
|---|---|---|---|
| bge-reranker-v2-m3 | 0.57B | ~600MB (Q4_K_M) | Apache 2.0，中英文混合场景最稳，**首选** |
| Qwen3-Reranker-0.6B | 0.6B | ~400MB~1.2GB | 最轻，极低资源场景 |
| Qwen3-Reranker-4B | 4B | ~2.5GB | 32k 上下文，精度甜点，但对常驻内存有要求 |

**决策待办**
- ☐ 选定模型（建议从 bge-reranker-v2-m3 起步）
- ☐ 选定服务框架
- ☐ 实测：本机 40 候选批处理的真实延迟

### 5.5 本机专属评估（M4 / 16GB）

2026-10-12 实测硬件：**Apple M4 · 16GB 统一内存 · 10 核 · 磁盘可用 53GB**。

#### 内存账怎么算

| 占用方 | 估算 |
|---|---|
| macOS + 日常 App | 约 4~6 GB |
| ollama `bge-m3`（已常驻，566M 参数 F16） | 约 1.2~1.5 GB |
| LightRAG server | 约 0.3~0.5 GB |
| **剩余可支配** | **约 8~10 GB** |

> 注意 ollama 已经跑着了，这是存量占用，别漏算。

#### 候选模型在本机上的账

| 模型 | 磁盘 | 常驻内存 | 40 候选延迟（🔍 待实测） | 建议 |
|---|---|---|---|---|
| `bge-reranker-v2-m3`（0.57B，Q4_K_M） | ~600 MB | ~0.7 GB | 0.2~0.5 s | ⭐ **首选**，中英文混合最稳 |
| `Qwen3-Reranker-0.6B` | ~400 MB~1.2 GB | ~0.6 GB | 0.15~0.4 s | 内存最省 |
| `Qwen3-Reranker-4B`（Q4/Q5） | ~2.5 GB | ~2.8 GB | 1~3 s | 预算够就拿它，精度甜点 |
| `Qwen3-Reranker-8B` | ~5 GB | ~5.5 GB | 3~8 s | ❌ 16GB 上不划算 |

#### 一个反直觉的点：本地可能比调 API 还快

云端 API 报的 0.3~1 s 里，**大部分是网络往返**，不是计算。M4 有 Metal 加速，本地跑一个 0.6B 的 cross-encoder 做批处理，很可能 **200~500ms 就出结果**。

负载也完全不成问题：每天 20~50 次查询，实际计算时间**一天加起来不超过 3 分钟**。真正要权衡的不是性能，是下面这条。

#### 真正该权衡的：要不要常驻

| | 常驻服务 | 按需启动 |
|---|---|---|
| 内存 | 一直占 0.7~2.8 GB | 不用时不占 |
| 首次查询 | 直接可用 | 多几秒冷加载 |
| 运维 | 简单 | 要写个启停脚本 |

**建议**：每天就几十次查询，别让一个 2.8GB 的进程一天 24 小时挂着。选小模型（bge-reranker-v2-m3）常驻最省心，占到约 0.7GB 尚可接受；选 4B 的话就该做成按需启动。

#### 服务框架怎么选

| 框架 | 特点 | 基座开销 |
|---|---|---|
| **llama.cpp server** | C++ 写的最轻，Metal 原生支持最好 | 最小 |
| **Infinity** | `pip install infinity-emb` 一条命令，最容易起 | Python，约 200~400MB |
| vLLM | 生态全但对个人项目偏重 | 大 |

本机优先 **llama.cpp server**（省资源）或 **Infinity**（省事），别上 vLLM。

---

## 6. 检索效果：别人测过什么，自己该怎么测

### 6.1 官方 benchmark 成绩（论文 / 官方文档）

评测方式：LLM-as-Judge（GPT-4o-mini 做裁判），四个数据集（农业 / 计算机 / 法律 / 混合），每个 60 万~500 万 token。下表为 **LightRAG 的胜率**：

| 基线 | Agriculture | CS | Legal | Mix |
|---|---|---|---|---|
| vs NaiveRAG | 67.6% | 61.2% | 84.8% | 60.0% |
| vs RQ-RAG | 67.6% | 62.0% | 85.6% | 60.0% |
| vs HyDE | 75.2% | 58.4% | 73.6% | 57.6% |
| vs GraphRAG | 54.8% | 52.0% | 52.8% | 49.6% |

**读出来的三点**

1. **领域越集中优势越大**：Legal（实体关系密集）84.8%，Mix 只有 49.6%~60%。
   你的知识库大概率是**多主题混杂**的——恰好落在 LightRAG 相对最弱的区间。别指望纸面数字。
2. **数据规模越大优势越明显**：论文观察到 LightRAG 的优势随语料增长而扩大。对 100+ 篇的目标是利好。
3. **混合模式最均衡**：消融显示去掉任一层都会明显掉分。`QUERY_MODE=mix` 是对的。

### 6.2 本项目跑过 benchmark 吗

**没有。** README 里唯一带实测数据的是「切片策略实测行为」（§7 引用），那是**行为观察**，不是检索质量的量化评测。`lightrag.log` 里也没有任何评测记录。

### 6.3 评测工具其实已经躺在依赖里了

LightRAG 1.5.7 自带 `lightrag/evaluation/`：

| 文件 | 用途 |
|---|---|
| `eval_rag_quality.py` | RAGAS 四指标评测脚本（38KB） |
| `offline_retrieval_check.py` | 离线检索检查 |

指标：Faithfulness（忠实度）· Answer Relevance（答案相关性）· Context Recall（上下文召回）· Context Precision（上下文精确率）

#### 前置 1：装 ragas（2026-10-12 实测尚未安装）

```bash
# 测试 RAGAS_AVAILABLE 是否为 True
uv tool run --from lightrag-hku python -c "import lightrag.evaluation.eval_rag_quality as m; print(m.RAGAS_AVAILABLE)"

# 当前是 False，需要连同 ragas 重装一次 lightrag-hku（不会动掉已有数据）
uv tool install lightrag-hku --force --with ragas
```

> ⚠️ 重装后再跑一次 `scripts/start.sh`：WebUI 目录和 `rerank.py` 都会被覆盖回原样，靠 start.sh 里的自愈逻辑补回来。

#### 前置 2：自己写评测集

包里**没有** `sample_dataset.json`，得自己准备。格式：

```json
[
  {"question": "xxx 的部署步骤是什么？", "ground_truth": "..."},
  {"question": "为什么 A 场景下不能启用 B？", "ground_truth": "..."}
]
```

**量级**：先做 **20~30 题**就够看出趋势。别一上来搞几百题——个人项目维护不动。
建议覆盖三类：**实体事实题**（测 low-level）· **归纳概括题**（测 high-level）· **多轮追问**（测 §8 的缺口）。

#### 跑起来

```bash
LR_ROOT=$(uv tool run --from lightrag-hku python -c "import lightrag,os;print(os.path.dirname(lightrag.__file__))")
uv tool run --from lightrag-hku python "$LR_ROOT/evaluation/eval_rag_quality.py" \
  -d 评测集.json -r http://localhost:9621
# 结果输出到 evaluation/results/results_<时间戳>.{csv,json}
```

> ⚠️ 评测本身要调 LLM 当裁判，会花钱。用便宜模型（如 qwen-flash）跑裁判即可，**不影响相对对比的结论**。
> 需要设置 `EVAL_LLM_BINDING_HOST` 等变量，具体看脚本里的 `os.getenv`。

### 6.4 评测纪律

- 基线：先在当前「关重排」状态下跑一遍，记下来
- 每次改切片参数、加重排、加查询改写后**重跑同一套题**，用 Context Recall / Precision 对比
- **没有评测就不要调参**。这是本文档最重要的一条约束。

---

## 7. 切片策略评估

### 7.1 现状参数

| 参数 | 值 | 备注 |
|---|---|---|
| `CHUNKING_STRATEGY` | `paragraph` | 按段落边界切 |
| `CHUNK_SIZE` / `CHUNK_P_SIZE` | 1200 | LightRAG 默认值 |
| `CHUNK_OVERLAP_SIZE` / `CHUNK_P_OVERLAP_SIZE` | 100 | LightRAG 默认值 |
| `EMBEDDING_TOKEN_LIMIT` | 8192 | 上限，切片必须小于它 |

### 7.2 已验证的行为（README 实测结论，可直接引用）

- ✅ 切分点落在**标题边界**上，不会拦腰截断一节 —— 这是 paragraph 策略相对固定窗口的核心优势
- ⚠️ 单节超过 1200 token 时会被 overlap **重复计入**（同一节出现在相邻两块）—— `CHUNK_P_OVERLAP_SIZE=100` 的预期代价
- ✅ 文档小于 `CHUNK_P_SIZE` 时**整篇作为一块**，不会切碎

### 7.3 我的判断：先不动，等评测

1200 token 对**纯向量检索**偏大（业界常见 512），但 **LightRAG 是 GraphRAG**—— chunk 还要喂给 LLM 做实体关系抽取，太碎会破坏跨实体的语义关联。这个权衡没有标准答案，**取决于你的文档类型**。

真正值得留意的是：

1. **overlap 重复计入**会导致同一段话在 top_k 里占两个名额，等价于**有效候选变少**。量大的时候可以考虑把 `CHUNK_P_OVERLAP_SIZE` 降到 50~80。
2. **代码仓库**（单文件上万行）要改 `CHUNKING_STRATEGY=recursive`，按语法边界切。README 已提到。
3. **100+ 篇规模下**，chunk 数量上去后，`TOP_K=40` 的相对召回率会下降，可能需要同步上调（但先把 §3.3 的生效问题修了再调，否则调了个寂寞）。

**结论：切片参数列入待评测项（Q6），盲调没有意义。**

### 7.4 切片能不能后面再改？（2026-10-12 补充）

**能改，但不是改一下配置那么简单——要重建，而且要重新花钱。**

#### 为什么必须重建

三个环环相扣的原因：

1. **同一份文件重复导入会被跳过。** LightRAG 的 `doc_id` 由文档内容 hash 得来，已 `processed` 的文档二次 `insert` 会被去重逻辑拦掉。**必须先显式删除文档，再重新导入。**
2. **切片变了 → LLM 缓存全失效。** 缓存记录里存着完整的 `original_prompt`（里面就含着切片原文），key 是它的 md5。切片边界一变，prompt 内容就变，md5 跟着变，**一条都命中不了**。
3. **向量也要重算。** 新切片要重新 embed——这一步是本地 ollama，**免费**，几分钟就好。

#### 重建要花多少钱（估算，务必以实际账单为准）

实测当前：885 条抽取缓存 / 21 篇 ≈ **42 chunk/篇**。

| 项 | 估算 |
|---|---|
| 单次抽取输入 | prompt ~1000 + chunk ~1200 ≈ **2200 token** |
| 单次抽取输出 | 约 500 token |
| 重建 21 篇（885 次） | input ≈ 195 万 · output ≈ 44 万 |
| **费用（Sonnet 档价估算）** | **约 ¥85~110** |
| 重建 100+ 篇（按 5 倍） | **约 ¥400~550** |

> ⚠️ 上表价格是按 Sonnet 档位估的，**不是你实际账单**。改之前先去控制台看一次真实单价再算。

#### 三条省钱/省事的做法

1. **趁便宜早决策。** 现在重建约 ¥100，到 100 篇就是 ¥500。所以切片参数应该在 **25~30 篇之前定稿**。
2. **不要全量重来，除非必要。** LightRAG 支持增量插入。新文档用新参数、老文档保持旧切片，系统照样能跑——只是新旧 chunk 粒度不一致，会让 `top_k` 的语义变得有点参差。**作为过渡方案是完全可以接受的。**
3. **保留 `kv_store_llm_response_cache.json`。** 决定重建的话，别急着删缓存：万一要回滚到旧切片，缓存还在就能省下一次全量抽取的钱。

#### 哪些改动是"轻"的、哪些是"重"的

| 改动 | 代价 |
|---|---|
| `TOP_K` / `CHUNK_TOP_K` / `MAX_TOTAL_TOKENS` | **只影响查询**，不碰存储。随便试，秒级切换 ✅ |
| `COSINE_Better_Than_Threshold` | 同上，只影响召回过滤 ✅ |
| `QUERY_MODE` | 同上 ✅ |
| `CHUNK_*_OVERLAP_SIZE` | 边界变化 → **重建 + 重付费** ❌ |
| `CHUNK_SIZE` / `CHUNK_P_SIZE` | **重建 + 重付费** ❌ |
| `CHUNKING_STRATEGY` | **重建 + 重付费**，且图谱形态会明显不同 ❌ |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | 重建，**但 embedding 本地免费**，只是时间成本 ⚠️ |
| `LLM_MODEL` | 不改也能查（只是后续抽取用新模型）；**换模型会触发新的 cache miss** ⚠️ |

> 💡 一句话记忆：**检索参数随便玩，切片参数想清楚再定。**

---

## 8. 多轮对话与查询改写

### 8.1 现状

| 能力 | 状态 | 依据 |
|---|---|---|
| 会话历史留存 | ✅ 有 | 自建 `web/index.html` 维护 `history` 数组，按 `MAX_HISTORY_TURNS` 截断后传给 `conversation_history` |
| 历史影响**生成** | ✅ 有 | 正常传给 LLM |
| 历史影响**检索** | ❌ 无 | 源码注释明说 `does not affect retrieval results` |
| 查询改写 / 指代消解 | ❌ 无 | 全仓库无相关实现 |

### 8.2 缺口为什么严重

场景：第一轮问「XX 框架怎么用」，第二轮问「它的缓存策略是什么」。

- 检索阶段拿到的是「它的缓存策略是什么」→ 「它」在向量空间里没有任何意义
- 召回的是一堆无关 chunk
- LLM 拿到垃圾上下文，即使它「知道」上一轮在说什么，也答不出来

这个缺口的杀伤力 > 当前所有参数调优的总和。

### 8.3 四种补法（从轻到重）

| 方案 | 做法 | 成本 | 适用 |
|---|---|---|---|
| ① 前端改写 | 提问前先调一次 LLM：「结合历史把这句话改写成独立完整的问句」 | 多一次 LLM 调用，几十行 JS | **推荐起步** |
| ② 自建 API 层 | 在调用 `/query` 前包一层代改写 | 需一个中间服务 | 想统一管理时 |
| ③ HyDE | 先用 LLM 生成假设答案，拿它去检索 | 一次额外生成 | 学界已验证有效，但要实测才知道对你有没有用 |
| ④ 等上游 | LightRAG 后续版本自行支持 | 0 | 不可控 |

> ③ 有个细节：官方 benchmark 里 HyDE 是被 LightRAG 打的那个基线，但那不代表 HyDE **叠加**在 LightRAG 上无效——两者解决的是不同环节的问题。想试的话一样要用评测说话。

**决策待办**
- ☐ 先实现 ①，用同一批多轮问句做 A/B（改写前 vs 改写后看 Context Recall）
- ☐ 确认 `MAX_HISTORY_TURNS` 当前值，以及是否要在很长会话里做摘要压缩

### 8.4 结论：要加，但顺序排在评测之后

**我的判断：要加。** 理由是收益/成本比太悬殊——几十行 JS + 一次便宜 LLM 调用，换来的是第二轮以后检索质量的量级提升。

**但执行顺序建议这样：**

```
先补评测（Q5）  →  再加重排（DR-1）  →  最后做查询改写（Q2）
```

原因不是改写不重要，而是**它必须能被证明有用**。如果先改了代码却没法量化"改写前的 Context Recall 是 0.4，改完是 0.7"，你就分不清这次改动到底是赚是亏——而这类"直觉上肯定有用"的改动，翻车率其实不低。

具体到实现，两条建议：

1. **改写用小模型就够了。** 指代消解这种任务不需要 claude-sonnet-5，接个便宜模型（qwen-flash 之类）即可，成本占一次查询的比重很低。
2. **别漏掉一个现成的开关。** 改写前先在 `MAX_HISTORY_TURNS=5` 上做实验——这个值决定了往后看几轮。5 轮偏保守，长会话里指代可能超出窗口。但调大又会吃掉 token，**没有评测就别动它**。

**已知的现成状态**

| 项 | 值 | 位置 |
|---|---|---|
| 历史轮数上限 | **5 轮**（前端 `slice(-MAX_HISTORY_TURNS * 2)`） | `web/index.html:184` |
| API 侧硬上限 | 128 条消息（= 64 轮） | LightRAG `constants.py` |
| 历史开关 | 界面上可随时关（`useHistory`） | `web/index.html:182` |

> 💡 界面上已有「多轮：开/关」按钮，做 A/B 时**直接用它**，一行代码都不用改。

---

## 9. 路线图（勾选式）

### 近期（止血 + 建立度量）

- [x] 停止服务、关闭重排
- [ ] **备份 `data/knowledge/`**（154M，重点是 `kv_store_llm_response_cache.json`，那是花出去的钱）（§2.3）
- [ ] 给自建 `web/index.html` 加 `top_k` / `chunk_top_k` 输入框（§3.3.1）
- [ ] 整理 20~30 题评测集 + 跑一次基线（§6.3）
- [ ] **趁重建便宜，把切片参数定下来**（25~30 篇前做，成本约 ¥100；拖到 100 篇是 ¥500）（§7.4）
- [ ] 修正 README 中过时的「3 篇文档」表述

### 中期（50 篇规模）

- [ ] 决定并执行 DR-1 的重排长期方案（建议先走「换云端供应商」）
- [ ] 评估存储换 Postgres / Qdrant（Q4）
- [ ] 实现多轮查询改写（方案 ①）并做 A/B——**用界面已有的多轮开关即可**
- [ ] 给评测集补「多轮追问」类题目，专门盯 Q2

### 长期（100+ 篇）

- [ ] 按评测结论定稿切片参数（不再盲调）
- [ ] 若选本地重排：起 llama.cpp / Infinity + 实测延迟（§5.5）
- [ ] 建立「每月跑一次评测」的习惯，防止静默劣化

### 已经明确「不要做」的

- ❌ 不要指望 Ollama 原生支持 rerank——它没有这个端点（§5.3）
- ❌ 不要在没评测的情况下调切片参数（§7.3 / §7.4）
- ❌ 不要只备份 `input/`——原始文件没了能重找，LLM 缓存没了要重新付钱（§2.3）

---

## 10. 附录

### 10.1 关键路径

| 用途 | 路径 |
|---|---|
| 配置 | `.env`（备份：`.env.bak-before-qwen3`、`.env.bak-before-rerankoff`） |
| 启动脚本 | `scripts/start.sh`（自带 WebUI 自愈 + rerank 协议补丁） |
| 知识库 | `data/knowledge/`（21 篇全 processed） |
| 自制界面 | `web/index.html`（带多轮历史） |
| 日志 | `lightrag.log`（**排查问题第一站**） |
| PRD | `docs/PRD.md`（本文件） |

### 10.2 环境与版本

- LightRAG 核心：**1.5.7**
- Ollama：**0.17.7**
- 本机 Python（LightRAG 的 uv 环境）：`/Users/liushuang/.local/share/uv/tools/lightrag-hku/bin/python`
- 服务端口：**9621**（API / WebUI / workspace）· **11434**（Ollama）
- 本机硬件（2026-10-12 实测）：**Apple M4 · 16GB 统一内存 · 10 核 · 磁盘可用 53GB**
- 自建界面历史轮数：`MAX_HISTORY_TURNS = 5` · API 硬上限 128 条消息

### 10.3 排错先看这里

| 现象 | 首查 |
|---|---|
| 检索突然变差 | `lightrag.log` 里的 `Arrearage` / `empty results` |
| 调了 `.env` 没反应 | 确认是通过 `export` 启动的（§3.3），或直接走界面 |
| 中文输入串词 | `patch_webui.py` 是否成功注入（升级 LightRAG 后 webui 目录会被覆盖） |
| 入库时服务崩溃 | `PYTHONPATH` 未 `unset`（WorkBuddy 注入的 `sitecustomize` 会重写 `os.unlink`） |

### 10.4 参考

- LightRAG 官方 https://github.com/HKUDS/LightRAG
- 官方论文 benchmark 数据见 §6.1
- `patch_rerank.py` 头部注释写透了阿里云 gte-rerank 与 qwen3-rerank 的协议差异，改动前务必读一遍

---

## 11. Q&A 速查（2026-10-12 补充）

集中放问答，方便回来快速找。每条都给了详细出处。

### Q1：检索参数能不能在前端自己调？

**能，但要分界面。**

- **官方管理台 `/webui/`**：有检索参数面板，`top_k` / `chunk_top_k` 都能调，**改完立即生效**，不用重启 → 日常调参就用它
- **自建 `web/index.html`**：只有 `mix/local/global/naive` 模式按钮，**没有任何数值控件** → 走它查询时 `top_k` 永远是默认的 40

查询参数是 `QueryParam(**request_data)` 构造的，**请求体里的字段优先级最高**，所以界面传什么就是什么。

想让自建界面也能调，在请求体里补两个字段即可（服务端不用改）：

```js
query_mode: mode,
top_k: 60,        // 新增
chunk_top_k: 15,  // 新增
```

详见 §3.3.1。

---

### Q2：需不需要加查询改写？

**要加，但排在评测之后。**

缺口是确凿的：LightRAG 源码明说 `conversation_history` 只进 LLM、**不影响检索**，所以第二轮问「它的缓存策略是什么」时，「它」在向量空间里没有意义，召回直接崩。全仓库也没有任何 query rewrite 实现。

但别急着写——**先补评测**。理由是这类"直觉上肯定有用"的改动必须能被证明，否则你分不清盈亏。

正确的推进顺序：`补评测 → 加重排 → 做查询改写`。

省心的现成条件：界面上已有「多轮：开/关」按钮（`useHistory`），**做 A/B 实验直接用它，不用改代码**。

详见 §8.4。

---

### Q3：自己部署 reranker，性能和资源要多少？

**在你的 M4 / 16GB 上完全可行，甚至可能比调 API 更快。**

| 模型 | 磁盘 | 常驻内存 | 40 候选延迟 🔍 |
|---|---|---|---|
| bge-reranker-v2-m3（推荐） | ~600MB | ~0.7GB | 0.2~0.5s |
| Qwen3-Reranker-0.6B | ~0.4GB | ~0.6GB | 0.15~0.4s |
| Qwen3-Reranker-4B | ~2.5GB | ~2.8GB | 1~3s |

两个要点：

1. **本地可能比云端快。** 云端那个 0.3~1s 里大部分是网络往返；M4 有 Metal 加速，批处理下来可能 200~500ms。
2. **真正的代价是常驻内存，不是性能。** 每天才几十次查询，实际计算时间一天不到 3 分钟。选小模型常驻省心；选 4B 就该做成按需启停。

⚠️ 但别忘了 Ollama 没有原生 rerank 端点，本机跑得额外起 **llama.cpp server / Infinity**（别上 vLLM，太重）。

详见 §5.5。

---

### Q4：切片策略是不是定死了？改了要全部重新导入吗？

**能改，但要重建，而且要重新付 LLM 的钱。**

三个原因：

1. `doc_id` 是内容 hash，已处理的文档重复导入会被**跳过**，必须先删除
2. 缓存记录里存着完整的 `original_prompt`（含切片原文），key 是它的 md5 → **切片一变，缓存一条都命中不了**
3. 切片变了 → 向量重算（这一步本地 ollama，免费）

**费用估算**（按 Sonnet 档位，实际以账单为准）：重建 21 篇约 **¥85~110**；到 100+ 篇约 **¥400~550**。

三条建议：
- **趁便宜早决策**——现在重建 ¥100，到 100 篇就是 ¥500，建议在 25~30 篇前定稿
- **不必全量重来**——新文档用新参数、老文档留着，系统照样跑，只是粒度参差
- **先别删 `kv_store_llm_response_cache.json`**——万一回滚到旧切片，缓存还在就能省一次全量抽取

**一句话记忆：检索参数随便玩，切片参数想清楚再定。**

详见 §7.4。

---

### Q5：现在所有数据都是什么形式、存在哪？

**全部是 `data/knowledge/` 下的明文 JSON + 一个 GraphML，没有任何数据库。**

```
data/knowledge/
├── vdb_relationships.json              68M   ← 关系向量
├── vdb_entities.json                   49M   ← 实体向量
├── kv_store_llm_response_cache.json    18M   ← LLM 抽取缓存（885 条）
├── graph_chunk_entity_relation.graphml 6.9M  ← 图谱本体（Gephi 可打开）
├── vdb_chunks.json                     4.4M  ← 切片向量
└── 其余 6 个 kv_store_*.json          ≈6M   ← 原文/索引/状态
```

三条提醒：

1. **117M 里只有 4.4M 是切片向量**，其余 96% 是实体/关系向量——这是 GraphRAG 的固有开销，100+ 篇时会涨到 500M~1G 且每次全量读写 JSON（对应 §4 的 Q4）。
2. **备份不能只备 `input/`**。`kv_store_llm_response_cache.json` 里装的是你花出去的钱，丢了要重付一遍 LLM 费用。整体才 154M，随手 `tar` 一份。
3. `data/knowledge/scan_spool/` 是入库临时中转，**可安全删除**。

详见 §2.3。

---

### 修订历史

| 日期 | 改动 |
|---|---|
| 2026-10-12 | 初版：诊断快照、问题清单 Q1~Q8、DR-1 重排决策、官方 benchmark、切片评估、多轮缺口 |
| 2026-10-12 | 追加 §2.3 数据全景 · §3.3.1 参数生效路径 · §5.5 本机 M4 专属评估 · §7.4 切片变更代价 · §8.4 查询改写结论 · §11 Q&A 速查 |
