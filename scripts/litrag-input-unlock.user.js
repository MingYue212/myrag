// ==UserScript==
// @name         LightRAG 中文输入修复
// @namespace    local.myrag
// @version      2.0
// @description  修复官方 WebUI 输入框的三个问题：生成期间锁输入框、组字中回车误提交、组字中改选区
// @match        http://127.0.0.1:9621/*
// @grant        none
// ==/UserScript==

// 背景：官方界面 query-input 上有三个独立问题，都会让中文输入翻车。
//
// 1) 生成期间输入框被 disabled
//    官方在生成答案期间把 query-input 置为 disabled（实测约 8～10 秒），此时点击输入框
//    完全不生效，焦点被丢弃到 BODY。macOS 输入法原本绑定在输入框上，焦点一丢，正在组字的
//    拼音无处安放、缓冲区错位，拼音被当成普通字母提交，顺序也随之颠倒。
//    → 拦截 disabled 的两条设置路径。注意 input 在值里出现换行时会切换成 <textarea>，
//      所以 HTMLInputElement 与 HTMLTextAreaElement 两个原型都要拦。
//
// 2) 组字状态下按回车会直接提交
//    官方 keydown 处理只看 Shift 判断「换行还是提交」，没有 IME 判断（整份 bundle 里
//    isComposing 出现 0 次）。于是你打拼音、按回车想选词上屏，应用却把未上屏的拼音当成
//    问题发了出去。
//    → 捕获阶段拦 keydown：isComposing 或 keyCode===229 时不放行。
//
// 3) 每次按键都抢焦点、改选区
//    输入框组件每次按键都在 requestAnimationFrame 里执行 focus() 与 setSelectionRange(旧位置)，
//    组字过程中用渲染前的旧光标位置重置选区，会打断/提前提交输入法组字。
//    → 组字期间（compositionstart→compositionend）不放行 setSelectionRange。
//
// 只对 id 为 query-input 的元素生效；图谱、文档管理、检索参数面板的按钮禁用逻辑不受影响。
//
// 导入方式：篡改猴 → 管理面板 → 添加新脚本 → 全选删除默认内容 → 粘贴本文件 → 保存
// 想恢复官方默认行为，禁用本脚本即可，无需改动任何安装文件。
//
// 说明：本脚本与 scripts/patch_webui.py 注入的 v2 补丁逻辑一致，二者装一个即可。
// 若服务端已注入补丁，本脚本无需安装。

(function () {
  'use strict';

  const TARGET_ID = 'query-input';
  let composing = false;

  const isTarget = (el) => !!(el && el.id === TARGET_ID);

  function hook() {
    // ① 解除 disabled（input 与 textarea 都要，多行时是 textarea）
    for (const ctor of [HTMLInputElement, HTMLTextAreaElement]) {
      const proto = ctor.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'disabled');
      if (!desc || !desc.get || !desc.set) continue;
      Object.defineProperty(proto, 'disabled', {
        configurable: true,
        enumerable: desc.enumerable,
        get() {
          return desc.get.call(this);
        },
        set(value) {
          desc.set.call(this, isTarget(this) ? false : value);
        },
      });
    }

    // React 渲染实际走的是 setAttribute('disabled','')，属性赋值那条也拦了，这条不能漏
    const origSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      origSetAttribute.call(this, name, value);
      if (String(name).toLowerCase() === 'disabled' && isTarget(this)) this.disabled = false;
    };
    const origRemoveAttribute = Element.prototype.removeAttribute;
    Element.prototype.removeAttribute = function (name) {
      origRemoveAttribute.call(this, name);
      if (String(name).toLowerCase() === 'disabled' && isTarget(this)) this.disabled = false;
    };

    // ② 组字中回车不提交
    // 用 stopImmediatePropagation 而非 preventDefault，避免影响输入法自身的提交行为
    window.addEventListener(
      'keydown',
      (e) => {
        if (isTarget(e.target) && e.key === 'Enter' && (e.isComposing || e.keyCode === 229)) {
          e.stopImmediatePropagation();
        }
      },
      true
    );

    // ③ 组字中不改选区
    document.addEventListener(
      'compositionstart',
      (e) => {
        if (isTarget(e.target)) composing = true;
      },
      true
    );
    document.addEventListener(
      'compositionend',
      (e) => {
        if (isTarget(e.target)) composing = false;
      },
      true
    );
    for (const ctor of [HTMLInputElement, HTMLTextAreaElement]) {
      const orig = ctor.prototype.setSelectionRange;
      ctor.prototype.setSelectionRange = function () {
        if (isTarget(this) && composing) return;
        return orig.apply(this, arguments);
      };
    }
  }

  function init() {
    try {
      hook();
    } catch (e) {
      console.warn('[LightRAG] 输入修复注入失败：', e);
    }
  }

  if (document.documentElement) init();
  else document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('DOMContentLoaded', init);

  console.log('[LightRAG] 中文输入修复已启用：生成期间可继续输入，组字中回车不会误提交');
})();
