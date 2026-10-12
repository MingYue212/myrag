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
import re
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


# ---------------------------------------------------------------------------
# 分数后处理：归一化 + 断崖检测
# ---------------------------------------------------------------------------
# 为什么需要：不同 reranker 的分数量纲完全不同。
#   - 阿里云 gte / Jina / Cohere：0~1 归一化概率
#   - llama.cpp + bge-reranker：未归一化 logit，实测 -11 ~ +10
# LightRAG 的 min_rerank_score 是个全局固定阈值，遇上 logit 就失效：
# 实测同一个模型，0.3 会把「知识管理平台的设计思路」的 61 个候选砍到只剩 2 个，
# 而另一些查询又几乎不过滤——表现不稳定，且失败时完全静默。
#
# 这里改成两步：先把分数归一化到 0~1（无断崖时靠归一化后的阈值仍然可用），
# 再按「断崖」动态决定保留多少：把分数降序排列，找相邻分差显著大于平均落差
# 的位置作为边界。无断崖则全部保留——不会像固定阈值那样误杀。

POSTPROCESS = '''

# --- __MARKER__: 分数归一化与断崖检测 ---
_MY_CLIFF_MIN_KEEP = 3      # 断崖再深也至少保留这么多个，否则上下文太少 LLM 编不出东西
_MY_CLIFF_MIN_GAP = 0.12    # 相邻归一化分差超过这个值才可能是断崖
_MY_CLIFF_GAP_FACTOR = 3.0  # 或超过平均落差的这个倍数


def myrag_normalize_scores(results):
    """把 relevance_score 归一化到 0~1；已在该区间的原样返回。

    只有明显越界时才动，避免把云端模型的 0~1 概率压到 0.5~1 而浪费区分度。
    """
    if not results:
        return results
    scores = [float(r.get("relevance_score", 0.0)) for r in results]
    if min(scores) >= 0.0 and max(scores) <= 1.0:
        return results
    # logit -> 概率。sigmoid(-4)~0.018，sigmoid(4)~0.982，区分度足够
    normed = [1.0 / (1.0 + math.exp(-s)) for s in scores]
    for r, v in zip(results, normed):
        r["relevance_score"] = v
    return results


def myrag_cliff_cutoff(results, top_n=None):
    """按断崖位置决定保留多少条，降序分数无断崖时全部保留。

    返回 (保留条数, 断崖位置或 None)。不改原列表。
    """
    n = len(results)
    if n < 4:                      # 样本太少，断崖检测没有统计意义
        return n, None
    scores = sorted(
        (float(r.get("relevance_score", 0.0)) for r in results), reverse=True
    )
    diffs = [scores[i] - scores[i + 1] for i in range(n - 1)]
    avg_gap = sum(diffs) / len(diffs)
    threshold = max(_MY_CLIFF_MIN_GAP, avg_gap * _MY_CLIFF_GAP_FACTOR)
    for i, gap in enumerate(diffs):
        if gap > threshold:
            cut = i + 1
            break
    else:
        return n, None              # 没有显著断崖，全保留
    cut = max(_MY_CLIFF_MIN_KEEP, cut)
    if top_n:
        cut = min(cut, top_n)
    return min(cut, n), cut


def myrag_postprocess(results, top_n=None, label=""):
    """归一化 + 断崖截断，返回真正该进上下文的条数。"""
    results = myrag_normalize_scores(results)
    cut, cliff = myrag_cliff_cutoff(results, top_n)
    if cliff is not None:
        logger.info(
            "Rerank %s cliff cutoff: keep %d/%d (cliff at #%d)",
            label, cut, len(results), cliff,
        )
    return results[:cut]


def label_of(fn):
    return getattr(fn, "__name__", "rerank")


def myrag_wrap_rerank(inner):
    """给任一 rerank provider 套上归一化 + 断崖截断。"""
    import functools

    @functools.wraps(inner)
    async def _wrapped(*args, **kwargs):
        raw = await inner(*args, **kwargs)
        top_n = kwargs.get("top_n")
        if top_n is None and len(args) >= 4:
            top_n = args[3]
        return myrag_postprocess(raw, top_n, label=label_of(inner))

    return _wrapped
'''.replace("__MARKER__", MARKER)


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

    # math 供 sigmoid 用。注意锚点必须是**模块顶部**的导入：
    # rerank.py 顶部没有 import asyncio，唯一一处出现在 __main__ 块内部，
    # 拿它当锚点会把 import math 插进缩进块里，导致 unexpected indent。
    if not re.search(r"^import math$", src, re.M):
        for top_anchor in ("\nimport aiohttp\n", "\nimport os\n"):
            if src.count(top_anchor) == 1:
                src = src.replace(top_anchor, "\nimport math" + top_anchor, 1)
                break
        else:
            sys.exit("未找到模块顶部的导入锚点，无法插入 import math。")

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

    # 后处理代码必须插在 `if __name__ == "__main__":` **之前**：
    # 直接追加到文件末尾会落进那个块的内部，缩进对不上导致语法错误。
    tail_anchor = '\nif __name__ == "__main__":'
    if src.count(tail_anchor) != 1:
        sys.exit(
            f"未在 {target} 中唯一定位到 __main__ 块（找到 {src.count(tail_anchor)} 处），"
            "无法安全插入后处理代码。"
        )

    patched = (
        src.replace(ANCHOR, PATCHED)
        .replace(tail_anchor, POSTPROCESS + tail_anchor, 1)
    )
    target.write_text(patched, encoding="utf-8")

    # 语法自检：补丁写坏时立刻回滚，不给下次启动留一个坏文件
    try:
        compile(target.read_text(encoding="utf-8"), str(target), "exec")
    except SyntaxError as e:
        shutil.copy2(backup, target)
        sys.exit(f"补丁导致语法错误，已回滚：{e}")

    print(f"已为 qwen3-rerank + 断崖检测补丁：{target}（原文件备份为 {backup.name}）")
    return 0


# ---------------------------------------------------------------------------
# 第二处：把 jina / cohere binding 指向带后处理的包装版本
# ---------------------------------------------------------------------------
SERVER_ANCHOR = '''        rerank_functions = {
            "cohere": cohere_rerank,
            "jina": jina_rerank,
            "aliyun": ali_rerank,
        }'''

SERVER_PATCHED = f'''        rerank_functions = {{
            # {MARKER}: 套上归一化 + 断崖截断
            "cohere": myrag_wrap_rerank(cohere_rerank),
            "jina": myrag_wrap_rerank(jina_rerank),
            "aliyun": ali_rerank,
        }}'''


def find_server() -> Path:
    try:
        import lightrag.api.lightrag_server as srv
    except ImportError:
        sys.exit("找不到 lightrag.api.lightrag_server。")
    return Path(srv.__file__).resolve()


def patch_server() -> None:
    target = find_server()
    try:
        src = target.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"读取 {target} 失败：{e}")

    if MARKER in src:
        print("断崖检测已接入分发字典，跳过")
        return

    if src.count(SERVER_ANCHOR) != 1:
        sys.exit(
            f"分发字典锚点在 {target} 中匹配到 {src.count(SERVER_ANCHOR)} 次（预期 1 次）。\n"
            "LightRAG 可能已改版，请查看 rerank_functions 字典并更新 SERVER_ANCHOR。"
        )

    backup = target.with_suffix(".py.myrag-bak")
    if not backup.exists():
        shutil.copy2(target, backup)

    # 同时补上 myrag_wrap_rerank 的导入
    patched = src.replace(SERVER_ANCHOR, SERVER_PATCHED)
    import_anchor = "            ali_rerank,\n"
    if import_anchor in patched and "myrag_wrap_rerank" not in patched.split("rerank_functions")[0]:
        patched = patched.replace(
            import_anchor,
            import_anchor + "            myrag_wrap_rerank,\n",
            1,
        )

    target.write_text(patched, encoding="utf-8")

    try:
        compile(target.read_text(encoding="utf-8"), str(target), "exec")
    except SyntaxError as e:
        shutil.copy2(backup, target)
        sys.exit(f"服务端补丁导致语法错误，已回滚：{e}")

    print(f"已将断崖检测接入 rerank 分发：{target}")


if __name__ == "__main__":
    rc = main()
    if rc == 0:
        patch_server()
    sys.exit(rc)