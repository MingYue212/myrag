# 个人 RAG 知识库

基于 **LightRAG** 的知识库。三路模型混搭，各自取所长：

| 用途 | 模型 | 位置 | 为什么这么配 |
|---|---|---|---|
| Embedding | `bge-m3`（1024 维） | **本地** Ollama | 换 API 会导致向量空间不兼容、需重建知识库；本地也避免文档内容外泄 |
| LLM | 中转站模型 | **API** | 抽取质量决定知识图谱质量，量小但要求高 |
| Reranker | `bge-reranker-v2-m3` | **本地** llama.cpp | 收益最大的一项，交叉编码器精排；零 API 成本 |
| VLM | `qwen-vl-max` | **API** 阿里云 | 仅处理 PDF 内嵌图片，日常关闭 |

- 存储：JSON 文件落盘，可直接备份、可 git 管理
- 服务地址：<http://127.0.0.1:9621>
- 管理台：<http://127.0.0.1:9621/webui/>（官方自带，含知识图谱可视化）

## ⚠️ 首次使用必须先配密钥

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，替换三处占位符：

```
LLM_BINDING_API_KEY=sk-xxx           # 中转站
LLM_BINDING_HOST=https://你的中转站域名/v1
LLM_MODEL=你的中转站支持的模型名

RERANK_BINDING_API_KEY=sk-xxx         # 阿里云
VLM_LLM_BINDING_API_KEY=sk-xxx       # 阿里云（用 VLM 时才需填）
```

**注意：LightRAG 用 `load_dotenv(override=False)`，不支持 `${VAR}` 变量插值**，必须填字面值。

Embedding 走本地，需要先装 Ollama 并拉模型：

```bash
ollama pull bge-m3
```

**换 embedding 模型必须重建知识库。** 向量空间不兼容，旧文档的向量无法与新模型的查询向量比较：

```bash
mv data/knowledge data/knowledge.bak-bge-m3   # 备份而非删除
./scripts/ingest.sh                              # 重新入库
```

## 快速开始

```bash
./scripts/rerank.sh                    # 启动本地重排（终端 A，前台运行）
./scripts/start.sh                     # 启动服务（终端 B）
open http://127.0.0.1:9621/webui/       # 官方管理台：文档 / 知识图谱 / 检索参数
./scripts/ingest.sh                    # 扫描 input/ 并入库
./scripts/ask.py "你的问题"            # 命令行提问
./scripts/list_docs.py                 # 列出文档（删除时需要 doc_id）
```

## 本地重排

重排不依赖任何云 API，用 **llama.cpp + bge-reranker-v2-m3**（Q8_0 量化，606MB，在 `models/`）。
llama.cpp 的 `/v1/rerank` 端点是 Jina 兼容的，正好对上 LightRAG 的 `jina` binding，无需写适配代码。

```bash
./scripts/rerank.sh     # 前台启动，Ctrl+C 停；已在运行会直接提示退出
```

`.env` 对应配置：

```
RERANK_BINDING=jina
RERANK_MODEL=bge-reranker-v2-m3
RERANK_BINDING_HOST=http://127.0.0.1:8012/v1/rerank
MIN_RERANK_SCORE=-6
```

模型下载（国内走 hf-mirror，直连 huggingface.co 会超时）：
<https://hf-mirror.com/gpustack/bge-reranker-v2-m3-GGUF>，放到 `models/` 即可。

> `bge-m3`（embedding，双编码器）和 `bge-reranker-v2-m3`（重排，交叉编码器）是两个不同的模型，
> Ollama 里的 `bge-m3` 不能用来做重排，必须单独下 GGUF 权重。

## 网页界面

有两套，都可用，按场景选。

### 官方管理台 <http://127.0.0.1:9621/webui/>

LightRAG 自带的完整界面，三个标签页：

- **文档** —— 上传、批量删除、扫描重试、入库流水线，按状态筛选（已完成 / 文件解析 /
  概念分析 / 图谱处理 / 失败），每篇显示摘要、长度、分块数与时间戳
- **知识图谱** —— Cytoscape 渲染，支持布局切换、缩放平移、节点搜索、按标签筛选、导出图片
- **检索** —— 逐项可调并可单独重置：查询模式、KG Top K、文本块 Top K、实体/关系/总词元上限、
  启用重排、仅靠上下文、仅需提示、流式响应、自定义提示词

纯聊天需求用 <http://127.0.0.1:9621/workspace/>，等价于自建界面的功能。

### 自建界面 `web/index.html`

单文件零依赖，双击即可用（无需构建、无需装包），也可 `open web/index.html`。

- 流式输出，答案逐字呈现
- 四个检索模式一键切换（综合 / 均衡 / 细节 / 概括）
- 多轮对话开关，仅用于消解"它""上面说的"这类指代，**不参与检索**
- 侧栏列出所有文档及状态，点文档名即就该文件提问
- 每个答案标注来源文件
- 头部实时显示当前模型配置与服务连接状态

比官方轻、启动快、代码可读可控，纳入 Git 管理；代价是没有图谱和文档管理。

界面通过 `http://127.0.0.1:9621` 调后端，需先启动服务。LightRAG 默认 `CORS Origins: *`，
所以 `file://` 直接打开不会被拦。

> ⚠️ 该服务的 `/query/stream` 返回的是**裸 JSON 逐行输出**，不是标准 SSE 格式。
> 若要自己接前端，注意按行解析而非按 `data:` 前缀切分。

## 目录约定

```
myrag/
├── .env                  配置（模型、检索参数、存储）
├── input/                放要入库的资料：.md / .pdf / 代码文件
├── data/knowledge/       知识库数据（备份这个目录即可）
├── models/               本地模型权重（GGUF，已 gitignore）
└── scripts/
    ├── start.sh          启动服务（含 WebUI 目录自愈 + 输入修复 + rerank 补丁）
    ├── rerank.sh         启动本地重排服务（llama.cpp，与 start.sh 并行）
    ├── ingest.sh         扫描入库（可传目录批量导入）
    ├── ask.py            提问
    ├── list_docs.py      列出文档与 doc_id
    ├── patch_webui.py        给官方 WebUI 注入中文输入修复（幂等）
    ├── patch_rerank.py       给 LightRAG 打 qwen3-rerank 协议补丁（幂等，供切回云端用）
    ├── verify_rerank_patch.py 离线校验补丁，不联网不消耗额度
    └── litrag-input-unlock.user.js  油猴脚本：同上修复，供不想改 site-packages 的人
```

## 放入你自己的资料

**方式一：直接复制**
把文件放进 `input/`，然后执行 `./scripts/ingest.sh`。

**方式二：批量导入某个目录**
```bash
./scripts/ingest.sh ~/Documents/我的笔记
```
会自动过滤掉 `node_modules`、`.git`、`dist` 等目录。

## 检索模式怎么选

| 模式 | 用途 | 例子 |
|---|---|---|
| `local` | 查实体细节，最快 | "RRF 函数在哪定义" |
| `global` | 查主题概括 | "这套评估体系讲了什么" |
| `hybrid` | 均衡，默认 | 大多数情况 |
| `mix` | 全局+局部+关键词，最全 | 综合性问题，稍慢 |

```bash
./scripts/ask.py "问题" --mode local
./scripts/ask.py "问题" --mode mix
```

**先用 `--context-only` 看看检索到了什么原文**，这样能判断问题出在检索还是生成：
```bash
./scripts/ask.py "问题" --context-only
```

## 调参指南（`.env`）

改了参数要重启服务才生效。

### 检索不准

| 参数 | 默认 | 调整方向 |
|---|---|---|
| `COSINE_Better_Than_Threshold` | 0.2 | 检索不到 → 降到 0.1；噪声太多 → 升到 0.3 |
| `TOP_K` | 60 | 漏召回 → 升到 100；太慢 → 降到 40 |
| `CHUNK_TOP_K` | 15 | 同上，控制送进 LLM 的文本块数量 |

### 答案被截断 / 不完整

`CHUNK_SIZE` 太小会把一个完整段落切两半，两个分块都不会被完整召回。中文技术文档建议 1200～2000。

```bash
CHUNK_SIZE=1600
CHUNK_OVERLAP_SIZE=150
```

注意：`CHUNK_SIZE` 必须 ≤ `EMBEDDING_TOKEN_LIMIT`（8192）。

### 切片策略实测行为

用 8 个二级标题、7338 字符的中文文档实测（`CHUNK_P_SIZE=1200`），结果：

| 块 | 字符 | tokens | 包含的标题 |
|---|---|---|---|
| 0 | 1646 | 1200 | 长文档测试 / 第1节技术概述 / 第2节部署细节 |
| 1 | 1646 | 1200 | 第3节监控告警 / 第4节数据模型 |
| 2 | 1649 | 1200 | 第5节权限设计 / 第6节缓存策略 |
| 3 | 1644 | 1200 | 第6节缓存策略 / 第7节限流熔断 |
| 4 | 1303 | 952 | 第8节灰度发布 |

两个结论：

1. **切分点落在标题边界上**，不会把一节内容拦腰截断—— 这是 paragraph 策略相比固定窗口的核心优势。
2. **但单节内容超过 1200 token 时会被 overlap 重复计入**（第 6 节同时出现在块 2 和块 3）。这是 `CHUNK_P_OVERLAP_SIZE=100` 的预期行为，目的是防止边界处的语义被割断，代价是同一节内容在两个块里各出现一次。

**文档小于 `CHUNK_P_SIZE` 时整篇作为一块**，不会切碎。本项目现有 3 篇文档都是如此（tokens 分别为 360 / 648 / 477）。

**什么时候该调大 `CHUNK_P_SIZE`：** 单个章节内容很长、检索时经常答不全。调大到 1600～2000 更稳，但注意必须 ≤ `EMBEDDING_TOKEN_LIMIT`（8192）。

**代码文件：** 按段落切分通常没问题。但如果投喂整个代码仓库（单文件上万行），建议改 `CHUNKING_STRATEGY=recursive`，它会按语法边界切，不会把函数切成两半。

### 实体抽取质量差

换更大的 API 模型即可：`LLM_MODEL=qwen-max`。

### 速度

- 走 API 后入库不再是本地推理的瓶颈，3 篇文档通常 1 分钟内完成
- `MAX_ASYNC_LLM=8`，注意百炼的 RPM 限制，报429 就调低
- 查询慢可以先用 `--mode local` 或 `--context-only`

## 常见问题

**入库时服务崩溃？**

本项目已处理。WorkBuddy 会在 `PYTHONPATH` 里注入 sitecustomize钩子，重写 `os.unlink` 走"移到废纸篓"。LightRAG 扫描结束要清理临时库，触发该钩子会因其临时目录不可写而抛 `SystemExit` 带崩服务。`scripts/start.sh` 里已加 `unset PYTHONPATH` 规避。手动启动时务必带上这一行。

**WebUI 打不开（`/webui` 返回 404）？**

PyPI 包里**是带前端构建产物**的（React + Cytoscape 图谱 + Mermaid + KaTeX，已内联进产物，离线可用），
但服务端硬编码去找 `lightrag/api/webui/` 这个目录。该目录一旦被改名或升级时丢失，路由就 404，
同时 `/health` 里 `webui_available` 会变成 `false`。

`scripts/start.sh` 已在启动前自动修复这种改名情况，无须手工处理。确认状态：

```bash
curl -s http://127.0.0.1:9621/health | grep -o '"webui_available":[a-z]*'
```

若显示 `false` 且启动时也没打印"已修复"，说明产物目录确实没了，重装即可：

```bash
uv tool install --force lightrag-hku
```

> 注意：这是在 `site-packages` 里改目录名，**每次升级 LightRAG 都可能被覆盖**，所以才需要 `start.sh` 里的自愈逻辑兜底。

**官方界面打中文有问题（回车误提交 / 变成字母且顺序颠倒 / 点不进输入框）？**

这是官方 WebUI 的交互问题，不是输入法坏了，也不是 LightRAG 出错。`query-input` 上有**三个
独立缺陷**，症状不同、成因也不同，只修其中一个会觉得"没治好"：

1. **组字中按回车，未上屏的拼音被直接提交**
   官方 keydown 处理只按 Shift 区分「换行 / 提交」，完全没有 IME 判断（整份 bundle 里
   `isComposing` 出现 **0 次**）。于是你打拼音、按回车想选词上屏，应用却把拼音当成问题发了出去。
   实测：`isComposing: true` 时派发 Enter，查询真的被提交（`Stop` 按钮出现、输入框被清空）。

2. **生成期间 `query-input` 被 disabled，焦点被丢到 `BODY`**
   生成答案期间（实测约 8～10 秒）会把 `query-input` 置为 `disabled`，此时点击输入框**完全不生效**。
   macOS 输入法原本绑定在输入框上，焦点一丢，正在组字的拼音无处安放、缓冲区错位，拼音被当作
   普通字母提交、顺序颠倒。这就是「第一次打字正常、第二次出问题」——第二次你等不及答案出来
   就点了输入框。

3. **每次按键都抢焦点、改选区**
   输入框组件每次按键都在 `requestAnimationFrame` 里执行 `focus()` 与
   `setSelectionRange(渲染前的旧光标位置)`。组字过程中用旧位置重置选区，会打断或提前提交输入法组字。
   实测：正常输入 7 个字符触发了 **14 次**这类调用（每键一次 `focus` + 一次 `setSel`）。

**处理**：`scripts/start.sh` 每次启动会自动把修复注入官方入口页（幂等），刷新即生效，无需手工操作。
注入内容见 `scripts/patch_webui.py`（版本标记 `myrag:ime-fix-v2`）。想确认是否已注入：

```bash
curl -s http://127.0.0.1:9621/webui/ | grep -o 'myrag:ime-fix-v2'
```

若不想让服务端改 `site-packages`，也可以改用油猴脚本
`scripts/litrag-input-unlock.user.js`（逻辑与注入版一致，装一个即可）：
篡改猴 → 管理面板 → 添加新脚本 → 全选删除默认内容 → 粘贴全文 → 保存。

> 实现上有几个坑：
> - React 渲染用的是 `setAttribute('disabled','')` 而非 `el.disabled = true`，两条路径都要拦。
> - 输入框在值里出现换行时会切换成 `<textarea>`，所以 `HTMLInputElement` 与
>   `HTMLTextAreaElement` 两个原型都要拦，只拦前者会在多行模式下失效。
> - 拦组字回车用 `stopImmediatePropagation` 而非 `preventDefault`，以免影响输入法自身的提交。
> - 这些修复只对 `query-input` 生效，图谱 / 文档管理 / 检索参数面板的按钮禁用逻辑不受影响。

> 自建界面 `web/index.html` 没有这些问题——它生成期间只禁用了发送按钮，输入框始终可聚焦，
> 也没有按键抢焦点、回车不看组字状态的逻辑。

**重排没生效（查询日志里有 `using original chunks`）？**

本项目重排已改成本地（llama.cpp + bge-reranker-v2-m3），不消耗额度也不受欠费影响。
LightRAG 对重排失败是**静默降级**（日志里 `Error during reranking, using original chunks`），
界面上完全看不出异常，但检索质量已经掉了——所以要主动查日志：

```bash
grep -iE "Successfully reranked|using original chunks" lightrag.log | tail
```

- `Successfully reranked: N chunks from M original chunks` → **生效**
- `using original chunks` → 又降级了，按下面排查

**重排没生效怎么排查？**

1. **本地重排服务没起**：先跑 `./scripts/rerank.sh`（前台），另开终端再跑 `./scripts/start.sh`。
   两个都得活着，LightRAG 是客户端，不会在需要时自动拉起它。

2. **batch size 太小（最常见）**：llama.cpp 默认 `ubatch=512`，而 RAG 切片拼上 query 后
   常超过 1000 token，会直接返回 500：

   ```bash
   grep "too large to process" lightrag.log
   ```

   `scripts/rerank.sh` 里已设 `-b 8192 -ub 4096`，若改过这个值要注意别调回去。

3. **`MIN_RERANK_SCORE` 已交给断崖检测接管**。本地模型返回未归一化 logit（-11 ~ +10），
   与这个按 0~1 设计的阈值量纲不匹配（曾把 61 个候选砍到只剩 2 个）。现在补丁会先把分数
   归一化，再按断崖动态决定保留多少，因此该阈值保持 `-6`（负数会让 LightRAG 跳过过滤）。

### 分数归一化 + 断崖检测

固定阈值在不同查询上表现不稳定：同一个模型，0.3 有的查询砍掉 97%、有的几乎不过滤，
且失败时完全静默。所以改成两步：

1. **归一化**：分数不在 [0,1] 时过一遍 sigmoid（logit → 概率）。已在 [0,1] 的原样保留，
   所以换回云端模型也不会被压到 0.5~1 浪费区分度。
2. **断崖截断**：分数降序后找相邻分差显著大于平均落差的位置作为边界；
   **没有显著断崖就全部保留**——不会像固定阈值那样误杀。

实测（108 / 41 / 84 / 120 个候选）：

| 查询 | 候选 | 保留 | 判定 |
|---|---|---|---|
| 知识管理平台的设计思路 | 108 | 3 | 断崖 @ #3 |
| 生产级 Agent 异常治理 | 84 | 4 | 断崖 @ #4 |
| Harness 工程核心思想 | 41 | 15 | 无断崖，全保留 |
| 缓存穿透怎么解决 | 120 | 15 | 无断崖，全保留 |

日志里会打印 `Rerank ... cliff cutoff: keep N/M (cliff at #K)`，没有这行说明无断崖、全保留。

参数在 `lightrag/rerank.py` 的补丁段里：`_MY_CLIFF_MIN_KEEP=3`（保底条数）、
`_MY_CLIFF_MIN_GAP=0.12`、`_MY_CLIFF_GAP_FACTOR=3.0`。
保底 3 条是因为实测断崖有时只留 2 个，对 LLM 来说上下文太薄容易答偏。

> 断崖检测依赖排序后的分差形态，对「有明确最佳文档 + 一堆噪声」的结构最有效；
> 若所有候选相关度都接近（本知识库小、文档高度相关时），检不出断崖就全保留，
> 这也是期望行为——此时依赖 `top_k` 兜底。

> 判断重排是否真的生效，别看界面，看日志：
>
> ```bash
> grep -iE "Successfully reranked|Rerank filtering|using original chunks" lightrag.log | tail
> ```
>
> - `Successfully reranked: N chunks from M original chunks` → **生效**
> - `Rerank filtering: K chunks remained` → 阈值又砍掉了 `15-K` 个，K 太小就是阈值配错了
> - `using original chunks` → 降级了，按上面排查

## 查询耗时实测

16GB M4 / 21 篇文档，实测各环节：

| 环节 | 耗时 | 占比 |
|---|---|---|
| 云端 LLM（`claude-sonnet-5` @ 中转站） | **1.5s 仅网络往返**，生成答案按输出长度计 | **主要瓶颈** |
| 关键词提取（mix 模式额外一次 LLM） | 2.7s | mix 模式才有 |
| Ollama `bge-m3` embedding | 1.1s | 可忽略 |
| 本地 rerank（15 篇切片） | **0.16s** | 可忽略 |

一次 mix 查询通常 11～20s，其中 **九成以上在等云端 LLM**，本地检索与重排加起来不到 2s。
所以"回复慢"不是本地组件的问题，要提速只能换更快的 LLM 模型或减小输出长度。

> 实测 `local` 模式反而比 `mix` 慢（32s vs 16s）：local 会把更完整的切片塞进上下文，
> LLM 要读更多 token。想靠切模式提速是行不通的。
>
> 另注意 LightRAG 开了 `enable_llm_cache`，**重复的 query 会直接命中缓存**（亚秒返回）。
> 测真实耗时时别拿重复 query，否则会被缓存误导。

> 若想换回云端重排：`scripts/patch_rerank.py` 仍在，它给 `ali_rerank` 加了 qwen3 协议支持
> （qwen3 与 gte 的端点、请求体嵌套、响应位置全不同，LightRAG 只硬编码了后者）。
> 补丁在 `site-packages` 内，升级会被覆盖，`start.sh` 每次启动自动补回（幂等）。
> 离线验证协议：`python scripts/verify_rerank_patch.py`（不联网、不消耗额度）。

**如何备份？**

整个 `data/knowledge/` 目录即为知识库，复制走即可。JSON 格式可直接 diff，能看出知识库随时间的变化。

**如何删除某篇文档？**

删除接口要的是 `doc_ids`，不是文件名。先查出 doc_id：

```bash
./scripts/list_docs.py
```

然后调用：

```bash
curl -X DELETE http://127.0.0.1:9621/documents/delete_document \
  -H "Content-Type: application/json" \
  -d '{"doc_ids":["doc-xxxx"]}'
```

会同步重建受影响的图谱关系。

**入库后原文件去哪了？**

LightRAG 会把已处理的原始文件移到 `input/__parsed__/`。这是正常行为 —— 文件移走后再扫描会被识别为已处理并跳过，避免重复抽取。

## 关于图片与 VLM

**当前默认关闭 VLM**（`VLM_PROCESS_ENABLE=false`），因为 md 和代码文件里没有图片，开着只是浪费。LightRAG 默认只做文本抽取，PDF 里的插图会被忽略（只取文字层）。

**需要读带图 PDF 时，正确的做法是让 VLM 直接看，而不是引入 MinIO。** 图片理解在模型侧完成，不需要对象存储这一层 —— MinIO 解决的是「图片存哪、答案里怎么引用链接」，而 VLM 路线整个绕开了这个问题。

**开启 VLM 有两个必要条件，缺一个都不生效：**

1. `VLM_PROCESS_ENABLE=true`
2. 解析规则要带 `i` 选项，否则图片分析根本不会被触发：

```bash
LIGHTRAG_PARSER=*:native-tePi
```

⚠️ 坑在于：**不带 `i` 规则时，命中图片分析开关的文档不是被跳过，而是直接判为失败**（报 "VLM analysis required but VLM role is not available"）。默认的 `*:native-teP` 也不含 `i`。

**VLM 调阿里云 qwen-vl-max：**

```bash
VLM_PROCESS_ENABLE=true
VLM_LLM_BINDING=openai
VLM_LLM_BINDING_HOST=https://dashscope.aliyuncs.com/compatible-mode/v1
VLM_LLM_BINDING_API_KEY=sk-xxx
VLM_LLM_MODEL=qwen-vl-max
```

## 后续可做

- **换更强模型**：抽取质量不满意时，在中转站换更擅长结构化输出的模型
- **接 MCP**：让 IDE 里的 AI 助手直接查这个知识库
- **PDF 支持**：按上面「关于图片与 VLM」开启 VLM
- **定期归档**：`input/__parsed__/` 里的文件可清空；同名文件重新扫描会按哈希跳过