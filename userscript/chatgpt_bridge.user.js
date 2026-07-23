// ==UserScript==
// @name         AI WebUI Bridge (ChatGPT + Gemini + Doubao)
// @namespace    https://github.com/OLmatter/chatgpt-bridge
// @version      2.1.0
// @description  把已登录的 ChatGPT / Gemini / 豆包 页面通过 HTTP 桥接暴露给本地 agent。支持多窗口、快照回传、自动重连。
// @author       You
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @match        https://gemini.google.com/*
// @match        https://www.doubao.com/*
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

  // 检测当前是哪个 AI 网站
  const HOST = location.hostname;
  const SITE = HOST.includes('gemini') ? 'gemini' : (HOST.includes('doubao') ? 'doubao' : 'chatgpt');

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
    if (SITE === 'gemini') {
      return document.querySelector('div[contenteditable="true"][role="textbox"]')
          || document.querySelector('.ql-editor[contenteditable="true"]')
          || document.querySelector('rich-textarea [contenteditable="true"]')
          || document.querySelector('textarea');
    }
    if (SITE === 'doubao') {
      // 豆包: contenteditable 富文本输入
      return document.querySelector('div[contenteditable="true"][data-testid*="input"]')
          || document.querySelector('[data-testid="chat_input_input"]')
          || document.querySelector('div[contenteditable="true"]')
          || document.querySelector('textarea[placeholder]')
          || document.querySelector('textarea');
    }
    // ChatGPT
    return document.querySelector('div[contenteditable="true"][role="textbox"]')
        || document.querySelector('textarea[name="prompt-textarea"]')
        || document.querySelector('#prosemirror-editor-container [contenteditable]');
  }

  function countAssistant() {
    if (SITE === 'gemini') {
      return document.querySelectorAll('model-response, .model-response-text').length;
    }
    if (SITE === 'doubao') {
      // 豆包: v_list_row 里文本>50字的算AI回复
      let count = 0;
      document.querySelectorAll('[class*="v_list_row"]').forEach(e => {
        if (e.innerText.trim().length > 50) count++;
      });
      return count;
    }
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
    let recent = [];
    let lastAssistant = '';
    let msgCount = 0;

    if (SITE === 'doubao') {
      // 豆包: v_list_row 是每条消息,用户和AI交替(用文本长度判断AI回复)
      const rows = document.querySelectorAll('[class*="v_list_row"]');
      const totalD = rows.length;
      msgCount = 0;
      for (let i = Math.max(0, totalD - 6); i < totalD; i++) {
        const t = rows[i];
        const txt = t.innerText.trim();
        if (!txt) continue;
        // AI回复通常较长(>50字)或含"搜索"
        const isAI = txt.length > 50 || txt.includes('搜索');
        if (isAI) msgCount++;
        recent.push({ role: isAI ? 'assistant' : 'user', text: txt.slice(-600) });
      }
      // 最后一条AI回复: 从后往前找文本长的
      for (let i = totalD - 1; i >= 0; i--) {
        const txt = rows[i].innerText.trim();
        if (txt.length > 50) {
          lastAssistant = txt.slice(-1000);
          break;
        }
      }
    } else if (SITE === 'gemini') {
      const queries = document.querySelectorAll('user-query, .user-query');
      const responses = document.querySelectorAll('model-response, .model-response-text, .response-container');
      msgCount = responses.length;
      // 拼最近对话
      const allTurns = document.querySelectorAll('user-query, model-response, .query-text, .model-response-text');
      const totalG = allTurns.length;
      for (let i = Math.max(0, totalG - 6); i < totalG; i++) {
        const t = allTurns[i];
        const tag = t.tagName.toLowerCase();
        const role = (tag.includes('user') || tag.includes('query')) ? 'user' : 'assistant';
        recent.push({ role, text: t.innerText.trim().slice(-600) });
      }
      if (responses.length) {
        lastAssistant = responses[responses.length - 1].innerText.trim().slice(-1000);
      }
    } else {
      // ChatGPT
      const turns = document.querySelectorAll('[data-message-author-role]');
      const total = turns.length;
      for (let i = Math.max(0, total - 6); i < total; i++) {
        const t = turns[i];
        const role = t.getAttribute('data-message-author-role');
        const md = t.querySelector('.markdown');
        recent.push({ role, text: (md ? md.innerText : t.innerText).trim().slice(-600) });
      }
      const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
      msgCount = msgs.length;
      if (msgs.length) {
        lastAssistant = (msgs[msgs.length - 1].querySelector('.markdown') || msgs[msgs.length - 1]).innerText.trim().slice(-1000);
      }
    }

    return {
      site: SITE,
      url: location.href,
      title: document.title,
      hasEditor: !!editor,
      editorText: editor ? (editor.innerText || editor.value || '').slice(0, 200) : '',
      assistantCount: msgCount,
      isGenerating: isGenerating(),
      recentTurns: recent,
      lastAssistant,
    };
  }

  function isGenerating() {
    if (SITE === 'doubao') {
      // 豆包: 生成时有停止按钮或 loading 状态
      for (const s of ['button[data-testid*="stop"]', 'button[aria-label*="停止"]', '.loading', '[class*="loading"]', '[class*="generating"]']) {
        const b = document.querySelector(s);
        if (b && b.offsetParent !== null) return true;
      }
      return false;
    }
    if (SITE === 'gemini') {
      for (const s of ['button[aria-label*="止"]', 'button[aria-label*="top"]', 'mat-progress-spinner', '.loading-indicator', 'mat-spinner']) {
        const b = document.querySelector(s);
        if (b && b.offsetParent !== null) return true;
      }
      return false;
    }
    for (const s of ['button[data-testid="stop-button"]', 'button[aria-label="停止"]', 'button[aria-label="Stop"]', 'button[aria-label*="止"]', 'button[aria-label*="top"]']) {
      const b = document.querySelector(s);
      if (b && b.offsetParent !== null) return true;
    }
    const ed = getEditor();
    if (ed && (ed.getAttribute('data-disabled') === 'true' || ed.getAttribute('contenteditable') === 'false')) return true;
    return false;
  }

  function getLastReply() {
    if (SITE === 'gemini') {
      const responses = document.querySelectorAll('model-response, .model-response-text, .response-container');
      if (responses.length) return responses[responses.length - 1].innerText.trim();
      return '';
    }
    if (SITE === 'doubao') {
      // 豆包: 从后往前找文本>50字的v_list_row
      const rows = document.querySelectorAll('[class*="v_list_row"]');
      for (let i = rows.length - 1; i >= 0; i--) {
        const txt = rows[i].innerText.trim();
        if (txt.length > 50) return txt;
      }
      return '';
    }
    const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
    if (msgs.length) return (msgs[msgs.length - 1].querySelector('.markdown') || msgs[msgs.length - 1]).innerText.trim();
    return '';
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
      const sendBtns = SITE === 'gemini'
        ? ['button[aria-label="发送消息"]', 'button[aria-label="Send message"]', 'button.send-button', '.send-button-container button', 'mat-icon[fonticon="send"]']
        : SITE === 'doubao'
        ? ['button[data-testid*="send"]', 'div[data-testid*="send"]', '.send-btn', '[class*="send-button"]']
        : ['button[data-testid="send-button"]', 'button[aria-label="发送"]', 'button[aria-label="Send"]', 'form button[type="submit"]'];
      for (const sel of sendBtns) {
        const btn = document.querySelector(sel);
        if (btn && btn.offsetParent !== null) { btn.click(); break; }
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
        return getLastReply();
      }
      if (sawNew && !genDetected && Date.now() - start > 5000) {
        return getLastReply();
      }
    }
    return sawNew ? '[超时但有回复]' : '[超时:无回复]';
  }

  function newChat() {
    if (SITE === 'gemini') {
      location.href = 'https://gemini.google.com/app';
      return 'ok';
    }
    if (SITE === 'doubao') {
      location.href = 'https://www.doubao.com/chat';
      return 'ok';
    }
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
  let busySince = 0;
  async function poll() {
    while (true) {
      try {
        // 强制 busy 复位:超过 60 秒说明卡死了
        if (busy && Date.now() - busySince > 60000) {
          console.log('[Bridge] busy 超时强制复位');
          busy = false;
        }
        // 始终回传快照(即使 busy,让后端看到生成进度和页面状态)
        const snap = getSnapshot();
        const resp = await gmFetch('POST', '/poll', { page_id: PAGE_ID, snapshot: snap });
        if (resp && resp.cmd && !busy) {
          busy = true;
          busySince = Date.now();
          setStatus('执行: ' + (resp.cmd === 'send' ? '发送' : resp.cmd), '#c83');
          // 关键:命令处理放独立异步函数,不阻塞 poll 循环
          executeCommand(resp);
        }
        // busy 时显示执行中,否则显示就绪
        if (busy) {
          setStatus('执行中...' + (snap ? ` (${snap.assistantCount}条 ${snap.isGenerating ? '⏳' : ''})` : ''), '#c83');
        } else {
          setStatus('就绪' + (snap ? ` (${snap.assistantCount}条)` : ''), '#2a2');
        }
      } catch (e) {
        setStatus('等待服务...', '#c33');
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

  // ====== 抗节流:页面重新可见时立即恢复 ======
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !busy) {
      gmFetch('POST', '/poll', { page_id: PAGE_ID, snapshot: getSnapshot() }).catch(() => {});
      setStatus('就绪(恢复)', '#2a2');
    }
  });
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) {
      gmFetch('POST', '/register', { page_id: PAGE_ID, page: SITE, url: location.href, title: document.title }).catch(() => {});
      setStatus('就绪(BFCache)', '#2a2');
    }
  });

  // ====== 启动 ======
  setStatus('连接中...', '#c83');
  gmFetch('POST', '/register', { page_id: PAGE_ID, page: SITE, url: location.href, title: document.title })
    .then(() => setStatus(`${SITE} 就绪`, '#2a2'))
    .catch(() => setStatus('服务未启动', '#c33'));
  poll();
  console.log('[ChatGPT Bridge] 已加载, 后端:', BASE, '页面ID:', PAGE_ID);
})();
