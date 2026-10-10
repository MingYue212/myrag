#!/usr/bin/env python3
"""列出知识库中的文档及其 doc_id。

删除文档时需要 doc_id 而非文件名，先用这个查。

用法：
    ./scripts/list_docs.py
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:9621"


def post(path: str, payload: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    try:
        # 分页接口要求 POST，page_size 上限 200
        result = post("/documents/paginated", {"page": 1, "page_size": 200})
    except Exception as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        print("请确认服务已启动：./scripts/start.sh", file=sys.stderr)
        return 1

    # 分页接口的返回结构在不同版本略有差异，兼容几种常见形态
    docs = result
    if isinstance(result, dict):
        for key in ("documents", "data", "items", "results"):
            if key in result:
                docs = result[key]
                break
        # 嵌套一层的情况：{"data": {"documents": [...]}}
        if isinstance(docs, dict):
            for key in ("documents", "items", "results"):
                if key in docs:
                    docs = docs[key]
                    break

    if isinstance(docs, dict):
        docs = docs.get("items", docs.get("documents", []))

    if not isinstance(docs, list) or not docs:
        print("知识库为空。")
        return 0

    print(f"{'文件名':<40} {'状态':<12} {'doc_id'}")
    print("-" * 100)
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        name = doc.get("file_path", "?").split("/")[-1]
        status = doc.get("status", "?")
        doc_id = doc.get("id") or doc.get("doc_id", "?")
        print(f"{name:<40} {status:<12} {doc_id}")

    print()
    print(f"共 {len(docs)} 篇。删除示例：")
    print("  curl -X DELETE http://127.0.0.1:9621/documents/delete_document \\")
    print("    -H 'Content-Type: application/json' \\")
    print("""    -d '{"doc_ids":["doc-xxx"]}'""")
    return 0


if __name__ == "__main__":
    sys.exit(main())