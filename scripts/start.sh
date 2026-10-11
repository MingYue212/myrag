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

# lightrag-server 实际使用的解释器，供下面推导 site-packages 路径
LR_PY="$(head -1 "$(command -v lightrag-server)" | sed 's|^#!||')"

# 检查 Ollama 是否在运行（embedding 用本地 bge-m3，必须有Ollama）
if ! curl -s --max-time 3 http://localhost:11434/api/version > /dev/null 2>&1; then
    echo "Ollama 未运行，正在启动..."
    nohup ollama serve > /tmp/ollama_serve.log 2>&1 &
    sleep 4
fi

# 自动修复官方 WebUI。
# PyPI 包里前端构建产物是完整的，但目录名可能对不上：服务端硬编码找
# lightrag/api/webui/，一旦该目录被改名（如 webui.bak.<时间戳>）或升级时丢失，
# /webui 与 /workspace 就会 404，且 /health 里 webui_available 变成 false。
# 这里在启动前自愈，省去每次升级 LightRAG 后的手动修复。
fix_webui() {
  local api_dir bak
  # 从 lightrag-server 的 shebang 反推 site-packages，不硬编码 Python 版本号
  api_dir="$("$LR_PY" -c 'import lightrag.api, os; print(os.path.dirname(lightrag.api.__file__))' 2>/dev/null)" || return 0
  [ -n "$api_dir" ] || return 0

  # 用 if 而非 [ -f ] && return：后者在文件不存在时返回 1，
  # 会被顶部的 set -e 当作失败而直接终止脚本
  if [ -f "$api_dir/webui/index.html" ]; then
    return 0
  fi

  # 目录不在，但有改名备份 → 改回服务端期望的名字
  bak="$(find "$api_dir" -maxdepth 1 -type d -name 'webui.bak.*' 2>/dev/null | head -1)"
  if [ -n "$bak" ] && [ -f "$bak/index.html" ]; then
    if mv "$bak" "$api_dir/webui" 2>/dev/null; then
      echo "已修复官方 WebUI：$(basename "$bak") → webui"
    fi
    return 0
  fi

  echo "⚠️  官方 WebUI 目录缺失（$api_dir/webui），/webui 与 /workspace 将返回 404" >&2
  echo "    重新安装可恢复：uv tool install --force lightrag-hku" >&2
}

fix_webui || true

# 给官方 WebUI 的两个入口页注入中文输入修复：生成期间锁输入框、组字中回车误提交、
# 组字中改选区，三个问题都在 query-input 上。细节见 patch_webui.py。
# 与 webui 目录同理：升级后目录可能被覆盖，故每次启动幂等补一次。
"$LR_PY" "$(dirname "$0")/patch_webui.py" || \
  echo "⚠️  输入修复未注入，官方界面中文输入可能异常" >&2

# 给 LightRAG 打 qwen3-rerank 协议补丁。与 webui 目录同理，rerank.py 位于
# site-packages 内，升级后会被覆盖；补丁脚本幂等，已打过则跳过。
# 失败不阻断启动：最坏情况退回 gte-rerank 协议，行为与打补丁前一致。
"$LR_PY" "$(dirname "$0")/patch_rerank.py" || \
  echo "⚠️  qwen3-rerank 补丁未应用，RERANK_MODEL=qwen3-rerank 将无法工作" >&2

echo "知识库地址：http://127.0.0.1:9621"
echo "官方管理台：http://127.0.0.1:9621/webui/      文档 / 知识图谱 / 检索参数"
echo "官方聊天页：http://127.0.0.1:9621/workspace/   等价于自建界面"
echo "自建界面：  open web/index.html"
echo "API 文档：  http://127.0.0.1:9621/docs"
echo
exec lightrag-server