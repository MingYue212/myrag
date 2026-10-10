#!/usr/bin/env python3
"""向本地知识库提问。

用法：
    ./scripts/ask.py "你的问题"
    ./scripts/ask.py "你的问题" --mode local

检索模式：
    local   只查低层（实体细节），快，适合"XX 函数在哪定义"
    global  只查高层（主题概括），适合"整体讲了什么"
    hybrid  均衡，默认
    mix     全局+局部+关键词，最全，稍慢
"""
import argparse
import json
import sys
import urllib.request

API = "http://127.0.0.1:9621"


def post(path: str, payload: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", help="要查询的问题")
    parser.add_argument(
        "--mode",
        default="mix",
        choices=["local", "global", "hybrid", "mix"],
        help="检索模式，默认 mix",
    )
    parser.add_argument("--top-k", type=int, default=60, help="候选实体/关系数")
    parser.add_argument("--chunk-top-k", type=int, default=15, help="候选文本块数")
    parser.add_argument("--context-only", action="store_true", help="只显示检索到的原文，不生成回答")
    args = parser.parse_args()

    payload = {
        "query": args.question,
        "query_mode": args.mode,
        "response_type": "Multiple Paragraphs",
        "top_k": args.top_k,
        "chunk_top_k": args.chunk_top_k,
    }
    if args.context_only:
        payload["only_need_context"] = None

    print(f"模式={args.mode} 检索中...", file=sys.stderr)
    try:
        result = post("/query", payload)
    except Exception as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        print("请确认服务已启动：./scripts/start.sh", file=sys.stderr)
        return 1

    if args.context_only:
        print(result.get("context", ""))
        return 0

    print()
    print(result.get("response", "（无回答）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())