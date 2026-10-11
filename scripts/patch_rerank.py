#!/usr/bin/env python3
"""给 LightRAG 的 rerank.py 打补丁，让 ali_rerank 支持 qwen3-rerank 系列模型。

背景
----
阿里云的 rerank 有两套互不兼容的协议：

    gte-rerank / gte-rerank-v2      qwen3-rerank
    ---------------------------     --------------------------------
    端点  /api/v1/services/         /compatible-api/v1/reranks
          rerank/text-rerank/
          text-rerank
    请求  input.query               query（顶层）
          input.documents           documents（顶层）
          parameters.top_n          top_n（顶层）
    响应  output.results            results（顶层）
    其它  支持 return_documents      不支持；支持 instruct

LightRAG 1.5.7 的 ali_rerank 把 gte 那套协议硬编码了，直接把 RERANK_MODEL
改成 qwen3-rerank 会导致：请求体阿里云不认 → 返回错误；即便侥幸返回，
响应也在 output.results 之外，LightRAG 永远解析出空列表 → 静默降级成
「跳过重排」，比直接报错更难发现。

补丁做法
--------
在 ali_rerank 内部按模型名分流：qwen3-* 走 standard 格式与新端点，
其余（gte-rerank 等）行为完全不变，向后兼容。

为什么需要这个脚本
------------------
rerank.py 位于 site-packages 内，每次升级 LightRAG 都会被覆盖。
scripts/start.sh 会在启动前调用本脚本，缺失时自动补回（与 webui 目录自愈同理）。
本脚本幂等：已打过补丁则直接退出；源码结构变化导致锚点匹配不到时报错而非静默失败。
"""
import shutil
import sys
from pathlib import Path

MARKER = "# myrag:qwen3-rerank-support"

# 锚点：ali_rerank 中按 gte 协议调用 generic_rerank_api 的那段，全文件唯一
ANCHOR = '''    return await generic_rerank_api(
        query=query,
        documents=documents,
        model=model,
        base_url=base_url,
        api_key=api_key,
        top_n=top_n,
        return_documents=False,  # Aliyun doesn't need this parameter
        extra_body=extra_body,
        response_format="aliyun",
        request_format="aliyun",
    )'''

PATCHED = f'''    {MARKER}
    # qwen3-rerank / qwen3-vl-rerank 使用 standard 格式与独立端点，
    # 与下方 gte 系列的嵌套协议不通用，这里按模型名分流。
    if model.startswith("qwen3"):
        # 只在指向阿里云旧端点时改写；自定义地址（如本地代理、mock）保持不变
        if base_url.startswith("https://dashscope.aliyuncs.com") and "services/rerank" in base_url:
            base_url = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
        return await generic_rerank_api(
            query=query,
            documents=documents,
            model=model,
            base_url=base_url,
            api_key=api_key,
            top_n=top_n,
            return_documents=None,  # qwen3 不接受该参数
            extra_body=extra_body,
            response_format="standard",
            request_format="standard",
        )

{ANCHOR}'''


def find_target() -> Path:
    """定位 site-packages 里的 rerank.py。"""
    try:
        import lightrag.rerank as mod
    except ImportError:
        sys.exit("找不到 lightrag，请确认在 lightrag-server 所在的 Python 环境里运行。")
    return Path(mod.__file__).resolve()


def main() -> int:
    target = find_target()

    try:
        src = target.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"读取失败：{e}")

    if MARKER in src:
        print(f"rerank 补丁已存在，跳过：{target}")
        return 0

    if src.count(ANCHOR) != 1:
        sys.exit(
            f"补丁锚点在 {target} 中匹配到 {src.count(ANCHOR)} 次（预期 1 次）。\n"
            "这通常意味着 LightRAG 已升级、ali_rerank 的代码结构变了，补丁需要重新适配。\n"
            "请查看该文件的 ali_rerank 函数并更新本脚本中的 ANCHOR。"
        )

    backup = target.with_suffix(".py.myrag-bak")
    if not backup.exists():
        shutil.copy2(target, backup)

    target.write_text(src.replace(ANCHOR, PATCHED), encoding="utf-8")

    # 语法自检：补丁写坏时立刻回滚，不给下次启动留一个坏文件
    try:
        compile(target.read_text(encoding="utf-8"), str(target), "exec")
    except SyntaxError as e:
        shutil.copy2(backup, target)
        sys.exit(f"补丁导致语法错误，已回滚：{e}")

    print(f"已为 qwen3-rerank 补丁：{target}（原文件备份为 {backup.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())