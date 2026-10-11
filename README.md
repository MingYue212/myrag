# 个人 RAG 知识库

基于 **LightRAG** 的知识库。三路模型混搭，各自取所长：

| 用途 | 模型 | 位置 | 为什么这么配 |
|---|---|---|---|
| Embedding | `bge-m3`（1024 维） | **本地** Ollama | 换 API 会导致向量空间不兼容、需重建知识库；本地也避免文档内容外泄 |
| LLM | 中转站模型 | **API** | 抽取质量决定知识图谱质量，量小但要求高 |
| Reranker | `gte-rerank-v2` | **API** 阿里云 | 收益最大的一项，交叉编码器精排 |
| VLM | `qwen-vl-max` | **API** 阿里云 | 仅处理 PDF 内嵌图片，日常关闭 |

- 存储：JSON 文件落盘，可直接备份、可 git 管理
- 服务地址：<http://127.0.0.1:9621>

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
./scripts/start.sh                     # 启动服务
open web/index.html                     # 打开网页界面
./scripts/ingest.sh                    # 扫描 input/ 并入库
./scripts/ask.py "你的问题"            # 命令行提问
./scripts/list_docs.py                 # 列出文档（删除时需要 doc_id）
```

## 网页界面

`web/index.html` 是单文件零依赖界面，双击即可用（无需构建、无需装包）。

- 流式输出，答案逐字呈现
- 四个检索模式一键切换（综合 / 均衡 / 细节 / 概括）
- 侧栏列出所有文档及状态，点文档名即就该文件提问
- 每个答案标注来源文件
- 头部实时显示当前模型配置与服务连接状态

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
└── scripts/
    ├── start.sh          启动服务
    ├── ingest.sh         扫描入库（可传目录批量导入）
    └── ask.py            提问
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

**WebUI 打不开？**

PyPI 版不含前端。需要图形界面就自行构建，或直接用 `http://127.0.0.1:9621/docs` 的 API 文档 + 命令行脚本。

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