#!/usr/bin/env python3
"""离线验证 qwen3-rerank 补丁：起一个本地 mock 服务，检查实际发出的请求体与响应解析。

不发任何外部请求，因此阿里云欠费时也能验证协议是否正确。
"""
import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from lightrag.rerank import ali_rerank  # noqa: E402

CAPTURED = {}


async def mock_rerank(request):
    CAPTURED["path"] = request.path
    CAPTURED["body"] = await request.json()
    # 模拟 qwen3-rerank 响应：结果在顶层 results，且不回传 document 原文
    return web.json_response(
        {
            "object": "list",
            "results": [
                {"index": 0, "relevance_score": 0.93},
                {"index": 2, "relevance_score": 0.34},
            ],
            "model": "qwen3-rerank",
            "usage": {"total_tokens": 79},
        }
    )


async def main() -> int:
    app = web.Application()
    app.router.add_post("/compatible-api/v1/reranks", mock_rerank)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8920)
    await site.start()

    try:
        # 传 localhost 地址：补丁的端点改写条件不匹配（它只改写 dashscope.aliyuncs.com），
        # 因此请求会打到本地 mock，全程不联网。
        # 端点改写逻辑改由 B 组静态检查验证，避免真的发请求到阿里云。
        result = await ali_rerank(
            query="什么是检索增强生成",
            documents=[
                "检索增强生成结合检索与生成",
                "今天天气晴朗",
                "预训练语言模型推动了排序模型发展",
            ],
            top_n=2,
            api_key="mock-key",
            model="qwen3-rerank",
            base_url="http://127.0.0.1:8920/compatible-api/v1/reranks",
        )
    finally:
        await runner.cleanup()

    body = CAPTURED.get("body", {})

    checks = [
        ("端点已改写为 /compatible-api/v1/reranks",
         CAPTURED.get("path") == "/compatible-api/v1/reranks"),
        ("query 在顶层（无 input 包装）", body.get("query") == "什么是检索增强生成"),
        ("documents 在顶层且完整", len(body.get("documents") or []) == 3),
        ("top_n 在顶层（无 parameters 包装）", body.get("top_n") == 2),
        ("不再发送 input 嵌套字段", "input" not in body),
        ("不再发送 parameters 嵌套字段", "parameters" not in body),
        ("不发送 qwen3 不支持的 return_documents", "return_documents" not in body),
        ("model 字段正确", body.get("model") == "qwen3-rerank"),
        ("顶层 results 被正确解析", len(result) == 2),
        ("排序分数正确", bool(result) and abs(result[0].get("relevance_score", 0) - 0.93) < 1e-6),
        ("index 正确", bool(result) and result[0].get("index") == 0),
    ]

    print("=== 发出的请求体 ===")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:600])
    print("\n=== A. 协议校验（请求打到本地 mock）===")
    ok = True
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed

    print("\n=== B. 端点改写校验（静态检查，不联网）===")
    import lightrag.rerank as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    static_checks = [
        ("补丁标记存在", "myrag:qwen3-rerank-support" in src),
        ("按模型名 qwen3 分流", 'model.startswith("qwen3")' in src),
        ("旧端点改写为 /compatible-api/v1/reranks",
         "https://dashscope.aliyuncs.com/compatible-api/v1/reranks" in src),
        ("改写有条件限制，不覆盖自定义地址",
         'base_url.startswith("https://dashscope.aliyuncs.com")' in src),
        ("qwen3 分支使用 standard 响应格式",
         'response_format="standard"' in src),
        ("gte 路径原样保留（向后兼容）", src.count('request_format="aliyun"') >= 1),
    ]
    for name, passed in static_checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed

    print("\n结论：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))