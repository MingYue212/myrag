#!/usr/bin/env python3
"""给 LightRAG 官方 WebUI 注入中文输入修复脚本。

修的是官方界面 query-input 上的三个问题（都不是输入法坏了）：

1. 生成期间输入框被 disabled
   官方在生成答案期间把 query-input 置为 disabled，点击输入框完全不生效，
   焦点被丢弃到 BODY。macOS 输入法原本绑定在输入框上，焦点一丢，正在组字的
   拼音无处安放、缓冲区错位，拼音被当成普通字母提交，顺序也随之颠倒。
   → 拦截 disabled 的两条设置路径，只对 query-input 生效。
   注意 input 在多行时会切换成 <textarea>（值里出现换行即触发），
   所以 HTMLInputElement 与 HTMLTextAreaElement 两个原型都要拦。

2. 组字状态下按回车会直接提交
   官方 keydown 处理只看 Shift 判断「换行还是提交」，没有 IME 判断
   （整份 bundle 里 isComposing 出现 0 次）。于是你打拼音、按回车想选词上屏，
   应用却把未上屏的拼音当成问题发出去了。
   → 捕获阶段拦 keydown：isComposing 或 keyCode===229 时不放行。
   用 stopImmediatePropagation 而非 preventDefault，以免影响输入法自身的提交。

3. 每次按键都抢焦点、改选区
   输入框组件有两个 useEffect，依赖里含输入长度，每次按键都在
   requestAnimationFrame 里执行 focus() 与 setSelectionRange(旧光标位置)。
   组字过程中用渲染前的旧位置重置选区，会打断/提前提交输入法组字。
   → 组字期间（compositionstart→compositionend）不放行 setSelectionRange。
   （focus() 对已聚焦元素是空操作，不动它，避免全局包装带来的副作用。）

做法
----
在 index.html 与 workspace.html 的 head 里、bundle 加载之前插入一段普通
script，赶在官方代码之前装好上述钩子。webui 目录本就在 site-packages 内、
随 LightRAG 一起升级或丢失，start.sh 已在启动前自动修复它；既然目录可控，
就把修复一并注入入口页，用户什么都不用装，刷新即生效。

本脚本幂等：以版本 marker 判定。若页面上还留着旧版本的注入块，先剥掉再插新的，
避免同一个原型被反复包装。

用法：由 start.sh 在启动前调用；也可手动
  /path/to/lightrag 环境的 python scripts/patch_webui.py
"""
import re
import sys
from pathlib import Path

# 家族标记：识别任意历史版本的注入块，用于剥离旧版本
MARKER = "myrag:ime-fix"
# 当前版本：页面上已含此串则视为已是最新，直接跳过
VERSION = "myrag:ime-fix-v2"

# 注入脚本压缩为单行，避免污染页面结构
INJECT = (
    "<script>/* myrag:ime-fix-v2 修复官方 WebUI 输入框："
    "①生成期间不锁输入框(input+textarea) ②组字中回车不提交 ③组字中不改选区 */"
    "(function(){var T='query-input',C=false;"
    "function isT(e){return !!(e&&e.id===T)}"
    # ① 解除 query-input 的 disabled：属性赋值与 setAttribute 两条路径都要拦
    "function unlock(p){if(!p)return;"
    "var d=Object.getOwnPropertyDescriptor(p,'disabled');"
    "if(!d||!d.get||!d.set)return;"
    "Object.defineProperty(p,'disabled',{configurable:true,enumerable:d.enumerable,"
    "get:function(){return d.get.call(this)},"
    "set:function(v){d.set.call(this,isT(this)?false:v)}})}"
    "unlock(HTMLInputElement.prototype);unlock(HTMLTextAreaElement.prototype);"
    "var sa=Element.prototype.setAttribute;"
    "Element.prototype.setAttribute=function(n,v){sa.call(this,n,v);"
    "if(String(n).toLowerCase()==='disabled'&&isT(this))this.disabled=false};"
    "var ra=Element.prototype.removeAttribute;"
    "Element.prototype.removeAttribute=function(n){ra.call(this,n);"
    "if(String(n).toLowerCase()==='disabled'&&isT(this))this.disabled=false};"
    # ② 组字中回车不提交
    "window.addEventListener('keydown',function(e){"
    "if(isT(e.target)&&e.key==='Enter'&&(e.isComposing||e.keyCode===229))"
    "e.stopImmediatePropagation()},true);"
    # ③ 组字中不改选区
    "document.addEventListener('compositionstart',function(e){if(isT(e.target))C=true},true);"
    "document.addEventListener('compositionend',function(e){if(isT(e.target))C=false},true);"
    "var si=HTMLInputElement.prototype.setSelectionRange;"
    "HTMLInputElement.prototype.setSelectionRange=function(){"
    "if(isT(this)&&C)return;return si.apply(this,arguments)};"
    "var st=HTMLTextAreaElement.prototype.setSelectionRange;"
    "HTMLTextAreaElement.prototype.setSelectionRange=function(){"
    "if(isT(this)&&C)return;return st.apply(this,arguments)}"
    "})();</script>"
)

# 稳定的注入锚点：服务端也用它注入运行时配置，两个入口页都有
ANCHOR = "<!-- __LIGHTRAG_RUNTIME_CONFIG__ -->"
TARGETS = ("index.html", "workspace.html")

# 匹配历史版本注入的整块 script（含 marker），用于剥掉后重插
_PREV_BLOCK = re.compile(r"<script>/\*\s*" + MARKER + r".*?</script>\s*", re.S)


def find_webui_dir() -> Path:
    try:
        import lightrag.api as api
    except ImportError:
        sys.exit("找不到 lightrag.api，请在 lightrag-server 所在的 Python 环境里运行。")
    return Path(api.__file__).resolve().parent / "webui"


def patch_file(path):
    """返回 True 表示本次发生了改动。"""
    text = path.read_text(encoding="utf-8")

    if VERSION in text:
        return False
    if ANCHOR not in text:
        print(f"  跳过 {path.name}：未找到注入锚点（LightRAG 可能改版了）")
        return False

    # 先剥掉旧版本注入，避免同一原型被重复包装
    stripped = _PREV_BLOCK.sub("", text)
    path.write_text(stripped.replace(ANCHOR, ANCHOR + "\n    " + INJECT, 1), encoding="utf-8")
    return True


def main():
    webui = find_webui_dir()
    if not webui.is_dir():
        print(f"webui 目录不存在：{webui}（应由 start.sh 先修复目录名）")
        return 1

    changed = []
    for name in TARGETS:
        target = webui / name
        if not target.is_file():
            continue
        if patch_file(target):
            changed.append(name)

    if changed:
        print(f"已注入输入修复：{'、'.join(changed)}")
    else:
        print("输入修复已是最新，跳过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
