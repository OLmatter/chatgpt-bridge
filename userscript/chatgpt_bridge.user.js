// ==UserScript==
// @name         ChatGPT WebUI Bridge
// @namespace    https://github.com/yourname/chatgpt-bridge
// @version      1.0.0
// @description  把已登录的 ChatGPT 页面通过 HTTP 桥接暴露给本地 agent。支持多窗口、快照回传、自动重连。
// @author       You
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM.xmlHttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

// ====================== 配置(按需修改) ======================
const BACKEND_URL = 'http://127.0.0.1:5000';  // 后端地址
const POLL_INTERVAL = 400;                      // 轮询间隔(ms)
// =============================================================

(function () {
  'use strict';

  // 只在主页面运行,不在 iframe(如 sentinel frame)里跑
  if (window.top !== window.self) return;

  const BASE = BACKEND_URL;

  // GM_xmlhttpRequest(绕过 CSP)
  const G = typeof GM_xmlhttpRequest !== 'undefined'
    ? GM_xmlhttpRequest
    : (typeof GM !== 'undefined' && GM.xmlHttpRequest ? GM.xmlHttpRequest : null);

  function gmFetch(method, path, data) {
    return new Promise((resolve, reject) => {
      const opts = {
        method, url: BASE + path,
        headers: { 'Content-Type': 'application/json' },
        timeout: 10000,
        onload: (r) => { try { resolve(JSON.parse(r.responseText)); } catch { resolve({}); } },
        onerror: () => reject(new Error('network error')),
        ontimeout: () => reject(new Error('timeout')),
      };
      if (data) opts.data = JSON.stringify(data);
      if (G) G(opts);
      else fetch(BASE + path, { method, headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined })
        .then(r => r.json()).then(resolve).catch(reject);
    });
  }

  // page_id: 用对话 URL 的 UUID + window.name 保证唯一
  function genPageId() {
    const m = location.pathname.match(/\/c\/([a-f0-9-]+)/i);
    const convoId = m ? m[1] : 'home';
    if (!window.name || !window.name.startsWith('bridge_')) {
      window.name = 'bridge_' + Math.random().toString(36).slice(2, 8);
    }
    return convoId + '_' + window.name.slice(-4);
  }
  const PAGE_ID = genPageId();

  // ====== 状态标签(页面右上角)======
  const badge = document.createElement('div');
  badge.style.cssText = `
    position: fixed; top: 10px; right: 10px; z-index: 999999;
    background: #888; color: white; padding: 4px 10px;
    border-radius: 12px; font: 12px monospace; opacity: 0.85;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3); pointer-events: none;
  `;
  badge.textContent = 'Bridge: 启动中';
  document.body.appendChild(badge);

  function setStatus(text, color) {
    badge.textContent = 'Bridge: ' + text;
    badge.style.background = color || '#888';
  }

  // ====== 页面快照(回传给后端)======
  function getEditor() {
    return document.querySelector('div[contenteditable="true"][role="textbox"]')
        || document.querySelector('textarea[name="prompt-textarea"]')
        || document.querySelector('#prosemirror-editor-container [contenteditable]');
  }

  function countAssistant() {
    return document.querySelectorAll('[data-message-author-role="assistant"]').length;
  }

  function isGenerating() {
    for (const s of ['button[data-testid="stop-button"]', 'button[aria-label="停止"]', 'button[aria-label="Stop"]']) {
      const b = document.querySelector(s);
      if (b && b.offsetParent !== null) return true;
    }
    return false;
  }

  function getSnapshot() {
    const editor = getEditor();
    const turns = document.querySelectorAll('[data-message-author-role]');
    const recent = [];
    const total = turns.length;
    for (let i = Math.max(0, total - 6); i < total; i++) {
      const t = turns[i];
      const role = t.getAttribute('data-message-author-role');
      const md = t.querySelector('.markdown');
      recent.push({ role, text: (md ? md.innerText : t.innerText).trim().slice(-600) });
    }
    const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
    const lastAssistant = msgs.length
      ? (msgs[msgs.length - 1].querySelector('.markdown') || msgs[msgs.length - 1]).innerText.trim().slice(-1000)
      : '';
    return {
      url: location.href,
      title: document.title,
      hasEditor: !!editor,
      editorText: editor ? (editor.innerText || editor.value || '').slice(0, 200) : '',
      assistantCount: msgs.length,
      isGenerating: isGenerating(),
      recentTurns: recent,
      lastAssistant,
    };
  }

  function isGenerating() {
    for (const s of ['button[data-testid="stop-button"]', 'button[aria-label="停止"]', 'button[aria-label="Stop"]', 'button[aria-label*="止"]', 'button[aria-label*="top"]']) {
      const b = document.querySelector(s);
      if (b && b.offsetParent !== null) return true;
    }
    const ed = getEditor();
    if (ed && (ed.getAttribute('data-disabled') === 'true' || ed.getAttribute('contenteditable') === 'false')) return true;
    return false;
  }

  // ====== 命令执行 ======
  async function sendMessage(text) {
    const editor = getEditor();
    if (!editor) throw new Error('找不到输入框(未登录?)');
    const before = countAssistant();
    editor.focus();
    await sleep(150);
    if (editor.tagName === 'TEXTAREA') {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
      setter.call(editor, text);
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
      editor.innerHTML = '';
      document.execCommand('insertText', false, text);
    }
    await sleep(400);
    // Enter 发送,2秒后检查是否成功,失败则点发送按钮兜底
    editor.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true,
    }));
    await sleep(2000);
    if ((editor.innerText || editor.value || '').trim().length > 0) {
      for (const sel of ['button[data-testid="send-button"]', 'button[aria-label="发送"]', 'button[aria-label="Send"]', 'form button[type="submit"]']) {
        const btn = document.querySelector(sel);
        if (btn && btn.offsetParent !== null && !btn.disabled) { btn.click(); break; }
      }
      await sleep(1500);
    }

    const start = Date.now();
    const TIMEOUT = 90000;
    let sawNew = false;
    let genDetected = false;
    while (Date.now() - start < TIMEOUT) {
      await sleep(800);
      const cur = countAssistant();
      if (!sawNew) { if (cur > before) { sawNew = true; genDetected = true; } continue; }
      const gen = isGenerating();
      if (gen) genDetected = true;
      if (sawNew && genDetected && !gen && Date.now() - start > 3000) {
        await sleep(1000);
        const n = countAssistant();
        if (n) return (document.querySelectorAll('[data-message-author-role="assistant"]')[n - 1].querySelector('.markdown')
          || document.querySelectorAll('[data-message-author-role="assistant"]')[n - 1]).innerText.trim();
        return '';
      }
      if (sawNew && !genDetected && Date.now() - start > 5000) {
        const n = countAssistant();
        if (n) return (document.querySelectorAll('[data-message-author-role="assistant"]')[n - 1].querySelector('.markdown')
          || document.querySelectorAll('[data-message-author-role="assistant"]')[n - 1]).innerText.trim();
      }
    }
    return sawNew ? '[超时但有回复]' : '[超时:无回复]';
  }

  function newChat() {
    for (const sel of ['a[href="/"]', 'a[data-testid="new-chat"]']) {
      const el = document.querySelector(sel);
      if (el) { el.click(); return 'ok'; }
    }
    location.href = 'https://chatgpt.com/';
    return 'ok';
  }

  async function processCommand(cmd) {
    if (cmd.cmd === 'send') return { ok: true, reply: await sendMessage(cmd.text) };
    if (cmd.cmd === 'new_chat') return { ok: true, result: newChat() };
    return { ok: false, error: 'unknown: ' + cmd.cmd };
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ====== 轮询主循环 ======
  let busy = false;
  async function poll() {
    while (true) {
      try {
        // busy 时也回传快照(让后端能看到生成进度)
        const snap = getSnapshot();
        const resp = await gmFetch('POST', '/poll', { page_id: PAGE_ID, snapshot: snap });
        if (resp && resp.cmd && !busy) {
          busy = true;
          setStatus('执行: ' + (resp.cmd === 'send' ? '发送' : resp.cmd), '#c83');
          try {
            const result = await processCommand(resp);
            await gmFetch('POST', '/result', { id: resp.id, result });
          } catch (e) {
            await gmFetch('POST', '/result', { id: resp.id, result: { ok: false, error: String(e.message || e) } });
          }
          busy = false;
        }
        if (!busy) setStatus('就绪' + (snap ? ` (${snap.assistantCount}条)` : ''), '#2a2');
      } catch (e) {
        setStatus('等待服务...', '#c33');
      }
      await sleep(POLL_INTERVAL);
    }
  }

  // ====== 抗节流:页面重新可见时立即恢复 ======
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !busy) {
      gmFetch('POST', '/poll', { page_id: PAGE_ID, snapshot: getSnapshot() }).catch(() => {});
      setStatus('就绪(恢复)', '#2a2');
    }
  });
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) {
      gmFetch('POST', '/register', { page_id: PAGE_ID, url: location.href, title: document.title }).catch(() => {});
      setStatus('就绪(BFCache)', '#2a2');
    }
  });

  // ====== 启动 ======
  setStatus('连接中...', '#c83');
  gmFetch('POST', '/register', { page_id: PAGE_ID, url: location.href, title: document.title })
    .then(() => setStatus('就绪', '#2a2'))
    .catch(() => setStatus('服务未启动', '#c33'));
  poll();
  console.log('[ChatGPT Bridge] 已加载, 后端:', BASE, '页面ID:', PAGE_ID);
})();
