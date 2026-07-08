// ==UserScript==
// @name         ChatGPT WebUI Bridge
// @namespace    https://github.com/yourname/chatgpt-bridge
// @version      1.0.0
// @description  Expose logged-in ChatGPT pages to a local agent through an HTTP bridge. Supports multiple tabs, snapshots, and auto-reconnect.
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
const BACKEND_URL = 'http://127.0.0.1:5000';  // Backend URL
const POLL_INTERVAL = 400;                      // 轮询间隔(ms)
// =============================================================

(function () {
  'use strict';

  // Run only in the main page, not inside iframes such as sentinel frames.
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

  // ====== Status badge (top-right corner) ======
  const badge = document.createElement('div');
  badge.style.cssText = `
    position: fixed; top: 10px; right: 10px; z-index: 999999;
    background: #888; color: white; padding: 4px 10px;
    border-radius: 12px; font: 12px monospace; opacity: 0.85;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3); pointer-events: none;
  `;
  badge.textContent = 'Bridge: starting';
  document.body.appendChild(badge);

  function setStatus(text, color) {
    badge.textContent = 'Bridge: ' + text;
    badge.style.background = color || '#888';
  }

  // ====== Page snapshot sent back to backend ======
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

  function hasConversationLimit() {
    const text = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();
    return text.includes("you've reached the maximum length for this conversation")
      || text.includes('you have reached the maximum length for this conversation')
      || text.includes('maximum length for this conversation')
      || text.includes('starting a new chat')
      || text.includes('start a new chat')
      || text.includes('达到此对话的最大长度')
      || text.includes('对话的最大长度');
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
      conversationLimited: hasConversationLimit(),
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
    if (!editor) throw new Error('input editor not found (not logged in?)');
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
    // Press Enter to send. If that fails, click the send button as fallback after 2 seconds.
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
    return { ok: false, error: 'unknown command: ' + cmd.cmd };
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ====== 轮询主循环 ======
  let busy = false;
  let busySince = 0;
  async function poll() {
    while (true) {
      try {
        // 强制 busy 复位:超过 60 秒说明卡死了
        if (busy && Date.now() - busySince > 60000) {
          console.log('[Bridge] busy timeout; forcing reset');
          busy = false;
        }
        // Always send snapshots back, even while busy, so backend can see generation progress and page state.
        const snap = getSnapshot();
        const resp = await gmFetch('POST', '/poll', { page_id: PAGE_ID, snapshot: snap });
        if (resp && resp.cmd && !busy) {
          busy = true;
          busySince = Date.now();
          setStatus('running: ' + (resp.cmd === 'send' ? 'send' : resp.cmd), '#c83');
          // 关键:命令处理放独立异步函数,不阻塞 poll 循环
          executeCommand(resp);
        }
        // Show running state while busy; otherwise show ready state.
        if (busy) {
          setStatus('running...' + (snap ? ` (${snap.assistantCount} msg${snap.assistantCount === 1 ? '' : 's'} ${snap.isGenerating ? '...' : ''})` : ''), '#c83');
        } else {
          setStatus('ready' + (snap ? ` (${snap.assistantCount} msg${snap.assistantCount === 1 ? '' : 's'})` : ''), '#2a2');
        }
      } catch (e) {
        setStatus('waiting for service...', '#c33');
      }
      await sleep(POLL_INTERVAL);
    }
  }

  // 独立执行命令(不阻塞 poll)。超时 120 秒强制结束释放 busy。
  async function executeCommand(cmd) {
    const deadline = Date.now() + 120000; // 硬超时 120 秒
    const checkTimer = setInterval(() => {
      if (Date.now() > deadline) { busy = false; }
    }, 5000);
    try {
      const result = await processCommand(cmd);
      await gmFetch('POST', '/result', { id: cmd.id, result });
    } catch (e) {
      await gmFetch('POST', '/result', { id: cmd.id, result: { ok: false, error: String(e.message || e) } });
    } finally {
      clearInterval(checkTimer);
      busy = false; // 无论成功失败,都释放 busy
    }
  }

  // ====== Anti-throttling: recover immediately when the page becomes visible ======
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !busy) {
      gmFetch('POST', '/poll', { page_id: PAGE_ID, snapshot: getSnapshot() }).catch(() => {});
      setStatus('ready (resumed)', '#2a2');
    }
  });
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) {
      gmFetch('POST', '/register', { page_id: PAGE_ID, url: location.href, title: document.title }).catch(() => {});
      setStatus('ready (BFCache)', '#2a2');
    }
  });

  // ====== 启动 ======
  setStatus('connecting...', '#c83');
  gmFetch('POST', '/register', { page_id: PAGE_ID, url: location.href, title: document.title })
    .then(() => setStatus('ready', '#2a2'))
    .catch(() => setStatus('service offline', '#c33'));
  poll();
  console.log('[ChatGPT Bridge] loaded, backend:', BASE, 'page id:', PAGE_ID);
})();
