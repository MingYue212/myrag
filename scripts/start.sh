#!/bin/bash
# 启动个人 RAG 知识库
set -e

# 必须切到项目根目录而非 scripts/：LightRAG 按当前工作目录找 .env，
# 在 scripts/ 下启动会找不到配置，并弹交互式确认（导致挂起）
cd "$(dirname "$0")/.."

export PATH="/Users/liushuang/.local/bin:$PATH"

# 必须清除 PYTHONPATH：WorkBuddy 注入了 sitecustomize 钩子，会重写 os.unlink
# 走"移到废纸篓"。LightRAG 扫描结束要清理 scan_spool 临时库，触发该钩子会
# 因其临时中转目录不可写而抛 SystemExit，把整个服务带崩。
unset PYTHONPATH

# 检查 Ollama 是否在运行（embedding 用本地 bge-m3，必须有Ollama）
if ! curl -s --max-time 3 http://localhost:11434/api/version > /dev/null 2>&1; then
    echo "Ollama 未运行，正在启动..."
    nohup ollama serve > /tmp/ollama_serve.log 2>&1 &
    sleep 4
fi

echo "知识库地址：http://127.0.0.1:9621"
echo "API 文档：http://127.0.0.1:9621/docs"
echo
exec lightrag-server