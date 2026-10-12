# myrag 项目全面评估报告

**评估日期**：2026-10-12 · **评估对象**：`myrag/`（LightRAG 1.5.7 · 23 篇文档 · 6217 节点/8616 边 · 160MB 数据 · Apple M4/16GB）
**评估方式**：通读全部源码与配置 + 读取运行日志 + 对运行中的服务做实测探针查询 + 核对 LightRAG 1.5.7 源码

---

## 0. 总体结论

这是一个**工程质量明显高于平均水平的个人 RAG 项目**：架构选型合理、踩坑文档化、成本意识强、隐私边界清晰。当前不构成"重写"级别的问题，但存在 **3 个当天即可修复、零成本的正确性缺陷**（token 预算自相矛盾导致 0-chunk 上下文、免费重排默认关闭、PRD 中一条被证伪的结论在误导后续决策），以及 1 个**时间敏感窗口**（切片参数定稿成本随文档量线性上涨，现在 23 篇正处在最便宜的窗口内）。

| 维度 | 评分 | 一句话判断 |
|---|---|---|
| 1. 实现思路 | ★★★★☆ | 链路清晰、边界划得清；配置生效路径碎片化是主要短板 |
| 2. 切分策略 | ★★★☆☆ | 思路对、有实测，但参数未经验证即固化，且正处成本窗口 |
| 3. 技术选型 | ★★★★☆ | embedding/rerank 本地化是正确的；LLM 第三方中转是最大单点 |
| 4. 部署方案 | ★★★☆☆ | 自愈补丁是聪明折中；无守护、无备份自动化、密钥有残留 |
| 5. 检索效果 | ★★★☆☆ | 管线完整且重排已打通，但预算配置错误 + 默认关重排 + 无评测基线 |
| 6. 资源消耗 | ★★★★☆ | 边际成本≈0，内存账清晰；存储增长曲线是唯一硬约束 |
| 7. 前端页面 | ★★★★☆ | 自用完成度很高（IME 处理是亮点）；缺参数控件与响应式 |

---

## 1. 实现思路（架构与核心流程）

### 优点

- **入库/查询两段链路分工明确**，且站在成熟框架（LightRAG）之上，没有重复造轮子：ingest（扫描→解析→paragraph 分块→LLM 实体关系抽取→图谱+向量落盘）与 query（关键词抽取→local/global/naive 三路召回→round-robin 合并去重→rerank→token 预算截断→生成）。
- **隐私/成本/质量的边界划得清楚**：文档内容只在本地流动（embedding 走 Ollama、rerank 走本地 llama.cpp），出网的只有实体抽取和答案生成——这是经过思考的分层，不是默认值堆砌。
- **可观测性有基础**：`lightrag.log` 信息密度高（每步的 top_k、召回数、去重数、预算截断都有记录），排障手册（README FAQ + PRD §10.3）降低了未来维护成本。

### 问题

1. **配置生效路径碎片化（实测修正了 PRD 的错误结论）**。PRD §3.3 断定"`.env` 的 TOP_K=60 对查询不起作用"——**这个结论是错的**。本次实测：向运行中的服务发送不带 `top_k` 的请求，日志显示 `top_k:60`、`chunk_top_k:15`、`max_total_tokens:12000`，`.env` 完全生效（机制：`base.py:43` 在模块导入时 `load_dotenv`，`QueryParam` 字段默认值紧随其后求值，`start.sh` 保证了 CWD 正确）。真实情况是：**请求体优先于一切默认值**，而官方 WebUI 检索面板自带默认值（`top_k:40, chunk_top_k:20, max_total_tokens:30000, max_entity_tokens:6000, max_relation_tokens:8000`），会整体覆盖 `.env`——于是同一个服务，自建界面走 `.env`（60/15/12000），官方面板走它自己的（40/20/30000），**两套界面行为不一致**，且面板值持久化在浏览器 localStorage 里，换浏览器行为又变。日志中 `top_k:8` 的记录即来自某个客户端显式传值。
2. **token 预算配置自相矛盾（新发现的 P0，详见 §5-F2）**：`MAX_TOTAL_TOKENS=12000` 小于 `MAX_ENTITY_TOKENS(6000)+MAX_RELATION_TOKENS(8000)` 的默认上限，图谱数据可吃光全部预算。
3. 单进程同时承担入库与查询：长任务入库期间查询变慢（`/health` 的 `pipeline_busy` 可见但无任何提示）。
4. 无查询改写层（PRD Q2 已正确识别，是全链路最大的结构性缺口）。

### 改进方向

- **统一参数来源**：自建界面请求体显式传 `top_k / chunk_top_k / max_total_tokens / enable_rerank`（与 `.env` 同源），或在自建界面加设置面板并 localStorage 持久化；官方面板的值手动对齐 `.env` 一次。目标：消除"同一服务两套行为"。
- 修 token 预算三角（见 §5）。
- 加查询改写前置层（见 §5）。
- 进阶可选：入库与查询分离进程（个人量级不必要，记录备选）。

---

## 2. 切分策略

### 优点

- **paragraph 策略是正确选择且经过实测验证**：切分点落在标题边界、不拦腰截断章节（README §切片策略实测行为有 7338 字符文档的完整分块表），小文档整篇成块不切碎——这是相对固定窗口的核心优势，且用真实数据说话而非抄默认值。
- 对 overlap 重复计入的代价有清醒认知（同一长节出现在相邻两块 = 有效候选变少），并给出了量化场景。
- 对不同语料有切换意识（代码仓库建议 `recursive`）。
- README 自己指出中文技术文档 1600~2000 更稳——**但当前配置没有采纳**。

### 问题

1. **参数未经验证即固化**：1200/100 是 LightRAG 默认值，PRD 自己也承认"盲调没有意义"，但 23 篇文档已经按这套参数付过费、图谱已固化。改动成本 = 重建 + 重抽（约 ¥85~110 @23 篇，100+ 篇时 ¥400~550）。
2. **overlap=100 偏大**：长章节双计导致 top_k 有效候选缩水，量级上来后是净损失。
3. **无语义/结构保护**：markdown 表格、代码块、面试题问答对可能被段落边界切断（"问/答"被分到两块对检索是实质性伤害——你的语料里面试题占比很高）。
4. **CHUNK_P_SIZE=1200 与 EMBEDDING_TOKEN_LIMIT=8192 之间有巨大浪费**：中文技术文档完全用得到 1600~2000。

### 改进方向

- **时间敏感**：趁 23 篇还在成本窗口（25~30 篇前定稿，晚一个数量级差 5 倍价），先建评测集（§5-4）再定稿：建议 `CHUNK_P_SIZE=1600`、`CHUNK_P_OVERLAP_SIZE=50~80`，重建一次。
- 对"面试题问答对"类内容，可在 ingest 前用脚本把 Q/A 合并为单块再入库（预处理比重构切分器轻）。
- 代码文件占比上升时按目录拆库或切 `recursive`，README 已有预案。
- 若暂时不动切片：接受"新旧粒度参差"的过渡方案（LightRAG 增量插入允许），但记录在案。

---

## 3. 技术选型

### 优点

- **bge-m3 本地 embedding 是完全正确的选择**：中英文混合、1024 维、零边际成本、文档内容不外泄、向量空间稳定（换模型=重建的代价被明确文档化）。
- **LightRAG（GraphRAG）适配这个场景**：个人知识库 = 多主题混杂 + 大量"某主题整体讲了什么"的概括类问题，正是双层次检索的主场；`QUERY_MODE=mix` 默认值与官方消融研究一致。
- **llama.cpp + bge-reranker-v2-m3 是本案最漂亮的决策**：Jina 兼容端点无缝对上 LightRAG binding、零 API 成本、Metal 加速、隐私最好。PRD §5.5 的"本地可能比调 API 还快"判断已被日志证实（rerank 全程约 12s 含 3 次 500 重试的失败案例；修复 batch size 后 15 chunks 精排一次完成）。
- `uv tool` 隔离安装，不污染系统 Python；`models/` gitignore；`.env.example` 存在且 `.env` 从未进过 git（已用 `git log --all -- .env` 验证）。

### 问题

1. **LLM 走第三方中转站（`cloud.hongqiye.com`）而非官方 API**：整个系统唯一的云单点——稳定性、服务连续性、ToS 风险集于一身，且密钥在中转站侧多一个副本。抽取和生成两条关键路径都压在它上面。
2. **JSON/NanoVectorDB 存储是第一个硬瓶颈**（PRD Q4 已识别，判断准确）：160MB 中 96% 是实体/关系向量，100+ 篇时 500MB~1G，每次写入全量序列化、启动全量加载（当前启动约 1.3s，届时 10s+ 且吃内存）。
3. **0.57B 的 reranker 用 Q8_0 量化偏重**：文件 635MB、实测常驻 RSS 约 752MB。Q4/Q5_K_M 可省 ~300MB，精排质量损失对这个量级的模型可忽略。
4. **qwen3:4b 闲置**：Ollama 里装着 2.5GB 的 qwen3:4b 却没承担任何任务——它本可以零成本接手关键词抽取、查询改写、闲聊检测这些"不需要 claude"的环节。

### 改进方向

- 中转站保留为主，但把 `.env` 改成"官方 API 一行切换"的备用配置（只改 host/key），并实测一次官方端点连通性。
- 关键词抽取 + 查询改写迁到本地 qwen3:4b（Ollama 有 OpenAI 兼容 `/v1/chat/completions`），claude 只留给最终生成——预计省 30~50% API 调用量。
- 50 篇节点评估 PG+pgvector 或 Qdrant；迁移前先试"只对高频实体向量化"的裁剪，可能直接避免迁移。
- rerank 模型换 Q5_K_M/Q4_K_M，常驻内存降到 ~450MB。

---

## 4. 部署方案

### 优点

- **`start.sh` 的三重自愈（WebUI 目录修复 + IME 注入 + qwen3-rerank 补丁）是对"site-packages 补丁式方案"的正确工程折中**：幂等、失败不阻断启动、每次启动自动补回。对个人项目，这比维护 fork 或等待上游现实得多。
- **环境特异性问题定位精准**：`unset PYTHONPATH` 规避 WorkBuddy 的 `sitecustomize` 钩子（会重写 `os.unlink` 导致 LightRAG 清理临时库时 `SystemExit` 带崩服务）——这类坑能定位到根因并写进注释，体现了真实的排障深度。
- Ollama 未运行时自动拉起；127.0.0.1 绑定；`.env` 权限 600；`verify_rerank_patch.py` 提供离线协议验证（不联网不花钱）。
- git 卫生好：历史清晰（10 个 commit 全是修复/功能）、`.gitignore` 考虑到了 `.env.bak-*`。

### 问题

1. **双终端手动启动、无守护、无开机自启**：`rerank.sh` 忘了起 → LightRAG 静默降级（README FAQ 兜底但体验割裂）；server 崩了要人肉拉。
2. **补丁打在 site-packages，升级即失效**——自愈逻辑缓解了症状，但每次 `uv tool install --force` 后都是一轮"目录被改名/覆盖"的俄罗斯轮盘。油猴脚本是好的替代交付，但两套修复路径要同步维护。
3. **无备份自动化**：160MB 知识库里 `kv_store_llm_response_cache.json`（19MB）是**已经付过钱的抽取结果**，丢了要重付 ¥85~110。目前只有 PRD 里的一句"随手 tar"。
4. **API 无鉴权**（`/health` 显示 `auth_mode: disabled`）：绑 127.0.0.1 时可接受，但哪天套 Tailscale/内网穿透就是裸奔；LightRAG 自带 auth 模式没用。
5. 小硬伤：`scripts/ingest.sh:37` 提示用户运行 `./scripts/ask.sh`（实际文件名是 `ask.py`）；ingest.sh 硬编码了 `/Users/liushuang/.workbuddy/binaries/python/...` 绝对路径，换机即断。
6. 仓库有未提交改动（`rerank.sh` 未 track、README/.gitignore 已修改未 commit）——本地重排这套最重要的基础设施不在版本控制里。

### 改进方向

- 写一个 `start-all.sh`（或 launchd plist）：拉起 rerank → 等 `/health` 就绪 → 拉起 server → 双向健康检查；rerank 不在时启动阶段就明确告警而非静默降级。
- 备份 cron：`tar czf` 轮换保留最近 N 份 `data/knowledge/`，优先外置盘/Time Machine。
- 需要远程访问时：Tailscale + 开启 LightRAG auth（token 模式），不要放宽 CORS。
- `ingest.sh` 改用 `command -v python3` 探测；顺手修 `ask.sh`→`ask.py` 提示。
- 把 rerank 相关改动 commit 掉。

---

## 5. 检索效果

### 优点

- 检索管线完整且每步可观测：关键词抽取（LLM，带缓存）→ 三路召回 → round-robin 合并去重（日志实测 `44 -> 30 deduplicated 14`）→ rerank → token 预算截断 → 生成。
- **本地重排已打通且验证生效**：日志 `16:40:45 Successfully reranked: 15 chunks from 30 original chunks` + `Rerank filtering: 12 chunks remained (min rerank score: 0.3)`——从"阿里云欠费白等 10s"恢复到"零成本精排"，这是本项目自 PRD 写作以来最大的实质进展。
- LLM 答案缓存命中机制在跑（实测重复提问直接 `Query cache hit`）。
- 调试入口齐全：`--context-only`、日志 grep 手册、状态计数接口。

### 问题

1. **【P0·新发现】token 预算错误配置导致 top_k=60 时最终上下文 0 个 chunk**。实测探针（不带 top_k，走 `.env` 默认值 60）：`Local query: 60 entites, 161 relations` → `After truncation: 67 entities, 170 relations` → **`Final context: 67 entities, 170 relations, 0 chunks`**。机制：统一 token 控制下 `MAX_ENTITY_TOKENS`(6000) + `MAX_RELATION_TOKENS`(8000) = 14000 > `MAX_TOTAL_TOKENS`(12000)，图谱数据吃光预算，文本块一个都进不去。对照证据：此前 `top_k=8` 的查询还能剩 4 chunks。这与 PRD 自己定的质量优先级"**答案不完整不可接受**"直接冲突——`TOP_K=60` 一旦真正生效，答案完整性反而受损。
2. **【P0】免费重排默认关闭**：`RERANK_BY_DEFAULT=false`，且自建界面请求体（`web/index.html:311-320`）不传 `enable_rerank`——本地重排零成本零延迟代价，却只有官方面板手动开开关时才用得上。等于花了精力部署的最佳组件默认不工作。
3. **多轮无查询改写（PRD Q2，判断准确）**：`conversation_history` 只进 LLM 不进检索，第二轮"它的缓存策略"里的"它"在向量空间无意义，召回崩。这是比所有参数调优加起来都大的缺口。
4. **从未跑过评测（Q5）**：RAGAS 四指标工具就躺在依赖里，但零基线——意味着一切调参（切片、阈值、模式）都无法证明盈亏。
5. 闲聊退化仅靠前端正则检测（`web/index.html:379-387`），模式库有限，漏判即"模型自由作答冒充知识库答案"。
6. 双阈值（COSINE 0.2 / MIN_RERANK_SCORE 0.3，后者是未归一化 logit 量纲）均无评测支撑。

### 改进方向（按序执行）

1. **修 token 预算（当天，零成本）**：`MAX_TOTAL_TOKENS` 12000 → 30000（LightRAG 默认值，claude-sonnet-5 的 200k 窗口完全扛得住），或把 `MAX_ENTITY_TOKENS/MAX_RELATION_TOKENS` 砍到 3000/4000 给 chunk 留位。改完用 `--context-only` 复测 chunks 数 > 0。
2. **打开重排（当天，零成本）**：`RERANK_BY_DEFAULT=true` + 自建界面请求体补 `enable_rerank: true, top_k: 60, chunk_top_k: 15`（顺手消除 §1 的两套界面不一致）。
3. **建评测集（1~2 天）**：20~30 题，覆盖实体事实题/归纳概括题/多轮追问三类；用 RAGAS（依赖已就绪，重装时 `--with ragas`）+ 便宜裁判模型；先跑"关重排"基线存档。
4. **查询改写（评测之后）**：前端预调用本地 qwen3:4b 做指代消解改写，A/B 直接复用界面已有的"多轮：开/关"按钮。
5. 闲聊检测从正则升级为"refs 为空 + 回答不含任何检索实体"的双条件，或干脆在无 refs 时前端明示。

---

## 6. 资源消耗

### 优点

- **单次查询边际成本≈0**：embedding 本地、rerank 本地，只有 LLM API 花钱；按 20~50 次/天的量级，月成本可控。PRD §5.2 的"重排成本相对 LLM 可忽略"判断正确。
- **内存账算得清且实测吻合**：server RSS 约 183MB + rerank 约 752MB + Ollama(bge-m3) 约 1.2~1.5GB ≈ 2.5GB，M4/16G 余量充足。
- VLM 默认关闭、按需开启（且文档化了"不带 `i` 选项会判失败而非跳过"的坑）——成本意识是贯穿项目的。
- `MAX_ASYNC_LLM=6` 对中转站限流有认知，429 处理有预案。

### 问题

1. **存储增长曲线是唯一硬约束**：96% 数据是实体/关系向量，23 篇 160MB → 100+ 篇 500MB~1G，JSON 全量读写 + 启动全量加载线性恶化；入库期间缓存文件（19MB）反复全量序列化。
2. rerank Q8_0 常驻 752MB 有约 300MB 优化空间（见 §3）。
3. **无用量/费用观测（Q8）**：花了多少钱只能月中翻中转站账单，无法归因到"哪次调参导致抽取量暴涨"。
4. 残留物：`data/knowledge/scan_spool/`（10 月 10 日的入库临时目录）未清理；`scripts/inputs/`、`scripts/rag_storage/`、`code/`、`md/`、`pdf/` 五个空目录是早期脚手架残留，README 的目录约定里也没有它们。

### 改进方向

- rerank 量化降档（省 ~300MB 常驻）。
- 每月一次轻量对账（中转站账单），调参前后各记一次，建立"改动→费用"的关联。
- `scan_spool` 加入定期清理；空目录删除。
- 50 篇节点做存储迁移评估（见 §3）；迁移前实测启动耗时作为触发指标。

---

## 7. 前端页面

### 优点

- **单文件零依赖 + 流式 + 构建标记**，对一个自用界面完成度很高；`open web/index.html` 即用，纳入 git 可 diff。
- **IME 处理是真正的亮点**：根因分析出官方 WebUI 的三个独立缺陷（组字回车误提交 / 生成期 disabled 丢焦点 / 按键抢焦点改选区），自建界面用 `activeElement` 判断做到了等价修复且不依赖按键细节——这种"定位到根因再修"的质量在全项目一贯。
- 流式解析稳健：按行解析裸 JSON、处理跨 chunk 截断、处理流结束残留行、空流回退非流式 `/query`。
- 安全：`esc()` 先转义再套 markdown，无 XSS 面。
- 体验细节：一键就文档提问、来源标注、多轮开关、闲聊退化警告、历史不污染（闲聊回答不入历史）。
- 视觉一致性：CSS 变量体系干净，配色克制。

### 问题

1. **无任何检索参数控件**：请求体只有 `query/query_mode/response_type/conversation_history`（`web/index.html:311-320`）——top_k/chunk_top_k/enable_rerank/max_total_tokens 全都不可调，做 A/B 测试必须改代码。这是 PRD §3.3.1 就提出、至今未做的项。
2. **无响应式**：全文无 `@media` 查询；侧栏固定 260px + `overflow:hidden`，手机上聊天区被压到约 100px，基本不可用。
3. **refs 只是文字 chip，不可点击**：知道答案来自哪个文件，但无法查看引用了哪段原文——对"验证答案是否忠实于资料"这个 RAG 核心诉求是断点。
4. **无 fenced code block 渲染**（`md()` 只处理行内 code）：技术知识库高频场景下代码答案观感差；也无复制按钮。
5. **会话不持久化**：刷新页面历史清零（内存态）；文档列表 `page_size:200` 封顶，超 200 篇静默截断。
6. 无深色模式（小事，记录备选）。

### 改进方向

- **设置面板（P1，一次开发长期受益）**：top_k / chunk_top_k / max_total_tokens / rerank 开关四个控件，localStorage 持久化——这同时是 §5 所有 A/B 实验的基础设施。
- `@media (max-width: 720px)`：侧栏改为顶部抽屉/下拉。
- refs chip 点击展开对应 chunk 原文（请求 `include_chunk_content=true` 或调 `/query/data`）。
- fenced code 渲染 + 代码块复制按钮。
- `localStorage` 持久化会话历史；文档列表加分页。

---

## 8. 实测发现汇总（含对既有文档的修正）

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| F1 | `.env` 的 TOP_K/CHUNK_TOP_K/MAX_TOTAL_TOKENS **对查询生效**，PRD §3.3"不生效"结论证伪 | 探针查询日志：`top_k:60`、`chunk_top_k:15`；源码 `base.py:43,110-115` | PRD Q3 的"export 修法"没必要；真正问题是官方面板默认值(40/20/30000)覆盖 `.env`，两套界面不一致 |
| F2 | `MAX_TOTAL_TOKENS=12000` < entity+relation 预算上限(14000)，top_k=60 时**最终 0 chunk** | 探针日志：`Final context: 67 entities, 170 relations, 0 chunks`；对照 top_k=8 时有 4 chunks | **P0**：答案完整性受损，与质量优先级直接冲突 |
| F3 | 本地重排已生效但 `RERANK_BY_DEFAULT=false` + 自建界面不传 `enable_rerank`，默认请求用不上 | `.env` + `web/index.html:311-320`；日志 16:40:45 证明能力就绪 | **P0**：零成本组件默认不工作 |
| F4 | README 过时：「现有 3 篇文档」（实际 23 篇）、密钥说明仍写 RERANK 用阿里云（已本地化） | `README.md:209`；`.env` | 误导后续维护者 |
| F5 | `ingest.sh:37` 提示运行不存在的 `ask.sh`（实为 `ask.py`） | 源码 | 小 bug |
| F6 | `.env` 的 `RERANK_BINDING_API_KEY` 残留真实阿里云密钥，而 rerank 已完全本地化 | `.env` | 密钥卫生：建议清除并轮换该 key |
| F7 | PRD §2.1/§5 状态过时：重排已从"云端欠费关闭"落地为"本地 llama.cpp 可用"，DR-1 决策已被现实取代 | PRD vs 运行进程 | 决策记录与现实脱节 |
| F8 | rerank 核心改动未提交 git（`scripts/rerank.sh` untracked） | `git status` | 基础设施不在版本控制内 |
| F9 | `scan_spool/` 残留 + 5 个空目录（`code/ md/ pdf/ scripts/inputs/ scripts/rag_storage/`） | `ls` | 仓库卫生 |

---

## 9. 优先级排序的行动清单

### P0 — 正确性缺陷，当天可完成，零成本

| # | 动作 | 工作量 | 预期收益 |
|---|---|---|---|
| 1 | `MAX_TOTAL_TOKENS` 12000→30000（或 entity/relation 砍半），`--context-only` 复测 chunks>0 | 5 分钟 | 解除 0-chunk 上下文，答案完整性恢复 |
| 2 | `RERANK_BY_DEFAULT=true` + 自建界面请求体补 `enable_rerank/top_k/chunk_top_k` | 10 分钟 | 免费精排默认生效，top_k=60 的宽召回才有意义 |
| 3 | 修正 PRD §3.3 结论（改为 F1 的真实机制）+ README 过时表述 + `ask.sh`→`ask.py` | 30 分钟 | 阻止错误结论继续误导决策 |
| 4 | 清除 `.env` 残留的阿里云 rerank 密钥并轮换；commit rerank 相关改动 | 15 分钟 | 密钥卫生 + 版本控制完整 |

### P1 — 质量与体验，2 周内

| # | 动作 | 工作量 | 预期收益 |
|---|---|---|---|
| 5 | 建 20~30 题评测集（实体/概括/多轮三类）+ RAGAS 跑基线存档 | 1~2 天 | 后续一切调参的前提；Q5 清零 |
| 6 | **趁 23 篇成本窗口定稿切片**（建议 `CHUNK_P_SIZE=1600`、overlap 50~80）并重建 | 0.5 天 + ~¥100 | 拖到 100 篇同操作是 ¥500 |
| 7 | 查询改写：本地 qwen3:4b 前置指代消解，A/B 用界面多轮开关 | 1 天 | 修复最大的检索结构性缺口（Q2） |
| 8 | 自建界面加参数设置面板 + localStorage 持久化 | 0.5 天 | A/B 基础设施；消除两套界面行为分裂 |
| 9 | 关键词抽取迁本地 qwen3:4b，claude 只做生成 | 0.5 天 | 省 30~50% API 量，降中转站单点依赖 |

### P2 — 规模化与运维，按需排期

| # | 动作 | 触发条件 |
|---|---|---|
| 10 | `start-all.sh` / launchd 守护 + 启动时 rerank 健康检查 | 随时（体验） |
| 11 | 备份 cron（轮换 tar `data/knowledge/`） | 随时（`llm_response_cache` = 已付的钱） |
| 12 | rerank 量化降档 Q8_0→Q5_K_M；清理空目录与 scan_spool | 随时（省 ~300MB） |
| 13 | 存储迁移评估（PG+pgvector / Qdrant，或实体向量裁剪） | 50 篇或启动 >5s |
| 14 | 前端响应式 + refs 可点击 + code block 渲染 + 会话持久化 | 手机使用需求出现时 |
| 15 | 多知识库分区（按主题拆 working dir） | 跨域检索噪声变明显时 |
| 16 | 远程访问方案（Tailscale + LightRAG auth） | 有库外访问需求时 |

### 明确不做（与 PRD 结论一致）

- 不上 vLLM（个人量级过重）；不指望 Ollama 原生 rerank（无此端点）；不在无评测情况下调切片；不只备份 `input/`。

---

## 10. 结语

这个项目最值得保持的三个习惯：**一切结论要有证据**（实测数据、日志、源码核对）、**决策写进 PRD 可追溯**（DR-xxx）、**成本与隐私意识贯穿每个选型**。当前最紧要的只有一件事：把 §9 的 P0 四项在文档量涨到 30 篇之前做掉——其中 token 预算和重排开关是纯粹的"配置错误导致好组件不工作"，修复它们比任何新功能都更能提升答案质量。
