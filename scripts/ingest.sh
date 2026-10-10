#!/bin/bash
# 把资料放入 input/ 后，扫描并入库
# 用法：./scripts/ingest.sh  或 ./scripts/ingest.sh ~/Documents/我的笔记
set -e
cd "$(dirname "$0")/.."

API="http://127.0.0.1:9621"

# 若传入目录参数，则复制其中的 md/pdf/代码文件进 input/
if [ -n "$1" ]; then
    echo "正在从 $1 导入文件..."
    find "$1" -type f \( -name "*.md" -o -name "*.pdf" -o -name "*.txt" \
        -o -name "*.py" -o -name "*.ts" -o -name "*.js" -o -name "*.tsx" \
        -o -name "*.go" -o -name "*.rs" -o -name "*.java" -o -name "*.c" \
        -o -name "*.cpp" -o -name "*.h" -o -name "*.yaml" -o -name "*.yml" \) \
        ! -path "*/node_modules/*" ! -path "*/.git/*" ! -path "*/dist/*" \
        -exec cp {} input/ \; 2>/dev/null || true
    echo "已复制到 input/"
fi

echo "触发扫描..."
TRACK=$(curl -s -X POST "$API/documents/scan" \
    -H "Content-Type: application/json" -d '{}' \
    | /Users/liushuang/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
      -c "import sys,json;print(json.load(sys.stdin).get('track_id',''))")

if [ -z "$TRACK" ]; then
    echo "扫描启动失败，请确认服务已运行"
    exit 1
fi

echo "扫描任务：$TRACK"
echo "实体关系抽取需要调用本地 LLM，耗时较长。进度查看："
echo "  观察命令：watch -n5 'curl -s http://127.0.0.1:9621/documents/status_counts'"
echo
echo "完成后用以下命令提问："
echo "  ./scripts/ask.sh \"你的问题\""