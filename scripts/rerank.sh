#!/bin/bash
# 启动本地重排服务（llama.cpp + bge-reranker-v2-m3）
# .env 里 RERANK_BINDING_HOST 指向的就是本服务；LightRAG 与本服务需同时运行。
set -e
cd "$(dirname "$0")/.."

MODEL="${RERANK_MODEL_PATH:-models/bge-reranker-v2-m3-Q8_0.gguf}"
PORT="${RERANK_PORT:-8012}"
LLAMA="${LLAMA_SERVER:-/opt/homebrew/bin/llama-server}"

if [ ! -f "$MODEL" ]; then
  echo "模型不存在：$MODEL" >&2
  echo "下载：https://hf-mirror.com/gpustack/bge-reranker-v2-m3-GGUF" >&2
  exit 1
fi
command -v "$LLAMA" >/dev/null 2>&1 || [ -x "$LLAMA" ] || {
  echo "未找到 llama-server。安装：brew install llama.cpp" >&2; exit 1; }

# 已在运行就直接退出，避免重复占用端口
if curl -s --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "重排服务已在运行：http://127.0.0.1:$PORT"; exit 0
fi

echo "启动本地重排服务…"
echo "  模型：$MODEL"
echo "  端口：$PORT"
# --pooling rank 是 rerank 必需；--embedding 一并开启是 llama.cpp 的要求。
# -b/-ub 必须放大：RAG 切片 + query 拼接后常超过 1000 token，
# 而 llama.cpp 默认 ubatch=512 会直接返回 500
#（"input (N tokens) is too large to process"），表现为 LightRAG 静默降级、
# 退回未重排的结果。
#
# -c/-np 是特意压低的：llama.cpp 默认 4 slots × 8192 ctx，
# 按 bge-reranker 的 36 层 × 1024 hidden × f16 估算，仅 KV cache 就要约 4.5GB
#（走 Metal 统一内存，ps 的 RSS 看不到，所以很容易被忽略）。
# 重排是单请求短任务，query + 切片拼起来 2000 token 以内足够，
# 改成 1 slot × 4096 后 KV cache 约 0.6GB，省下约 3.9GB。
# 若要重排特别长的文档，可把 -c 调到 8192（内存相应翻倍）。
exec "$LLAMA" -m "$MODEL" --rerank --embedding --pooling rank \
  -b 4096 -ub 2048 -c 4096 -np 1 \
  --host 127.0.0.1 --port "$PORT"
