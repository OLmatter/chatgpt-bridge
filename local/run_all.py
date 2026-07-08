#!/usr/bin/env python3
"""桥接服务 + Claude 监督器 一体化启动。

一个进程同时跑:
  - HTTP 桥接服务 (端口 5000): 接收油猴脚本的 poll/send
  - Claude 监督器: 后台线程,发现空闲页面就调 Claude 决定回复

用法: python run_all.py
停止: Ctrl+C
"""
from __future__ import annotations
import json, os, queue, shutil, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HTTP_PORT = 5000
CLAUDE_CMD = shutil.which("claude") or r"C:\Users\520hh\AppData\Roaming\npm\claude.cmd"
POLL_INTERVAL = 8  # 监督器扫描间隔

# ====== 桥接服务状态 ======
PAGES: dict[str, dict] = {}
RESULTS: dict[str, dict] = {}
LOCK = threading.Lock()
LAST_HANDLED = {}  # 监督器: page_id -> {"snippet": str, "until": float} 防重复
IN_FLIGHT: set[str] = set()  # 正在调 Claude 的 page_id,防止重复派发
SUPERVISOR_ENABLED = {"on": True}  # 监督器总开关,GUI 可控制
CLAUDE_LOCK = threading.Lock()    # Claude 串行锁(避免并发起多个 node 进程爆内存+卡死)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supervisor_config.json")
DEFAULT_SUPERVISOR_PROMPT = """Supervisor assistant. Read the recent ChatGPT conversation below and decide whether to send a short continuation nudge.

Task: decide whether this ChatGPT tab should continue working, and write one very short reply if needed.

Rules:
1. If the conversation hit a length limit or asks to start a new chat, output SKIP.
2. Otherwise output one short encouragement:
   - If progress improved, praise briefly.
   - If there is no improvement, tell it to continue.
   - Keep it under 8 Chinese characters or a similarly short English phrase.
   - Do not give technical advice, task names, method names, or explanations.
   - Do not use words implying conservative/stable behavior.

Output format:
REPLY
<one short message>

or

SKIP
<reason>

=== Conversation ===
{convo}
=== Decision ==="""
DEFAULT_BANNED_WORDS = ["稳", "保守", "求稳", "稳妥"]
DEFAULT_PROVIDER = "claude_cli"
DEFAULT_API_URL = ""
DEFAULT_API_MODEL = "gpt-4o-mini"
DEFAULT_FALLBACK_REPLY = "Continue"
SUPERVISOR_CONFIG = {"prompt": DEFAULT_SUPERVISOR_PROMPT, "banned_words": list(DEFAULT_BANNED_WORDS), "provider": DEFAULT_PROVIDER, "api_url": DEFAULT_API_URL, "api_model": DEFAULT_API_MODEL, "api_key": "", "fallback_reply": DEFAULT_FALLBACK_REPLY}


def load_supervisor_config():
    """Load supervisor config from local JSON, falling back to defaults."""
    global SUPERVISOR_CONFIG
    cfg = {"prompt": DEFAULT_SUPERVISOR_PROMPT, "banned_words": list(DEFAULT_BANNED_WORDS), "provider": DEFAULT_PROVIDER, "api_url": DEFAULT_API_URL, "api_model": DEFAULT_API_MODEL, "api_key": "", "fallback_reply": DEFAULT_FALLBACK_REPLY}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("prompt"), str) and data["prompt"].strip():
                cfg["prompt"] = data["prompt"]
            if isinstance(data.get("banned_words"), list):
                cfg["banned_words"] = [str(x) for x in data["banned_words"]]
            if data.get("provider") in ("claude_cli", "openai_compatible"):
                cfg["provider"] = data["provider"]
            if isinstance(data.get("api_url"), str):
                cfg["api_url"] = data["api_url"].strip()
            if isinstance(data.get("api_model"), str) and data["api_model"].strip():
                cfg["api_model"] = data["api_model"].strip()
            if isinstance(data.get("api_key"), str):
                cfg["api_key"] = data["api_key"]
            if isinstance(data.get("fallback_reply"), str) and data["fallback_reply"].strip():
                cfg["fallback_reply"] = data["fallback_reply"].strip()
    except Exception as e:
        log(f"Config load failed, using defaults: {e}")
    SUPERVISOR_CONFIG = cfg
    return cfg


def save_supervisor_config(prompt=None, banned_words=None, provider=None, api_url=None, api_model=None, api_key=None, clear_api_key=False, fallback_reply=None):
    """Persist supervisor config and update in-memory settings."""
    global SUPERVISOR_CONFIG
    cfg = dict(SUPERVISOR_CONFIG)
    if prompt is not None and str(prompt).strip():
        cfg["prompt"] = str(prompt)
    if banned_words is not None:
        cfg["banned_words"] = [str(x) for x in banned_words]
    if provider in ("claude_cli", "openai_compatible"):
        cfg["provider"] = provider
    if api_url is not None:
        cfg["api_url"] = str(api_url).strip()
    if api_model is not None and str(api_model).strip():
        cfg["api_model"] = str(api_model).strip()
    if clear_api_key:
        cfg["api_key"] = ""
    elif api_key is not None and str(api_key).strip():
        cfg["api_key"] = str(api_key).strip()
    if fallback_reply is not None and str(fallback_reply).strip():
        cfg["fallback_reply"] = str(fallback_reply).strip()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    SUPERVISOR_CONFIG = cfg
    return cfg



def _now(): return time.time()


def _cleanup_stale():
    """清理掉线超30秒的页面。"""
    cutoff = _now() - 30
    with LOCK:
        stale = [pid for pid, p in PAGES.items() if p["last_poll"] < cutoff]
        for pid in stale:
            del PAGES[pid]
    if stale:
        log(f"Cleaned {len(stale)} stale offline page(s)")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ====== Claude 调用(串行锁 + Popen,避免僵尸进程和内存爆炸)======
def ask_claude(prompt):
    """调 Claude CLI,超时杀子进程返回 None。串行锁保证同时只有一个。"""
    with CLAUDE_LOCK:
        for attempt in range(2):
            proc = None
            try:
                proc = subprocess.Popen(
                    [CLAUDE_CMD, "--print"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8",
                )
                stdout, _ = proc.communicate(input=prompt, timeout=30)
                return stdout.strip()
            except subprocess.TimeoutExpired:
                if proc:
                    proc.kill()
                    proc.wait(timeout=5)
                if attempt == 0:
                    time.sleep(2)
                    continue
                return None
            except Exception:
                if proc:
                    try: proc.kill()
                    except: pass
                return None
        return None



def ask_openai_compatible(prompt, cfg):
    """Call an OpenAI-compatible chat completions endpoint."""
    api_url = (cfg.get("api_url") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("api_model") or DEFAULT_API_MODEL).strip()
    if not api_url or not api_key or not model:
        return None
    if not api_url.rstrip("/").endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 80,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))
        return (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log(f"API provider failed: {e}")
        return None
def claude_decide(recent_turns, last_reply):
    convo = ""
    for t in recent_turns[-6:]:
        role = "User" if t["role"] == "user" else "ChatGPT"
        convo += f"[{role}] {t['text'][:600]}\n\n"
    cfg = load_supervisor_config()
    template = cfg.get("prompt") or DEFAULT_SUPERVISOR_PROMPT
    prompt = template.replace("{convo}", convo)
    if "{convo}" not in template:
        prompt = f"{template}\n\n=== Conversation ===\n{convo}\n=== Decision ==="
    result = ask_openai_compatible(prompt, cfg) if cfg.get("provider") == "openai_compatible" else ask_claude(prompt)
    if result is None:
        return {"action": "reply", "message": cfg.get("fallback_reply", DEFAULT_FALLBACK_REPLY)}
    if result.upper().startswith("SKIP"):
        return {"action": "skip", "reason": result}
    lines = result.split("\n")
    msg = "\n".join(lines[1:]).strip() if lines[0].strip().upper() == "REPLY" else result.strip()
    for bad in (SUPERVISOR_CONFIG.get("banned_words") or []):
        msg = msg.replace(str(bad), "")
    msg = msg.strip()
    if not msg or len(msg) > 500:
        return {"action": "skip", "reason": "empty or too long"}
    return {"action": "reply", "message": msg}


# ====== HTTP 桥接 ======
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_OPTIONS(self): self._send(200, {})

    def do_GET(self):
        # 自动清理掉线超30秒的页面
        try:
            _cleanup_stale()
        except Exception as e:
            log(f"Cleanup error: {e}")
        if self.path == "/status":
            with LOCK:
                alive = sum(1 for p in PAGES.values() if _now()-p["last_poll"]<3)
                n = len(PAGES)
                pid_obj = id(PAGES)
            self._send(200, {"pages_connected": n, "pages_alive": alive, "pages_obj_id": pid_obj, "supervisor_on": SUPERVISOR_ENABLED.get("on", True)})
        elif self.path == "/supervisor_on":
            SUPERVISOR_ENABLED["on"] = True
            log("Supervisor enabled from GUI")
            self._send(200, {"ok": True, "on": True})
        elif self.path == "/supervisor_off":
            SUPERVISOR_ENABLED["on"] = False
            log("Supervisor disabled from GUI")
            self._send(200, {"ok": True, "on": False})
        elif self.path == "/supervisor_config":
            cfg = load_supervisor_config()
            self._send(200, {"ok": True, "prompt": cfg.get("prompt", ""), "banned_words": cfg.get("banned_words", []), "provider": cfg.get("provider", DEFAULT_PROVIDER), "api_url": cfg.get("api_url", ""), "api_model": cfg.get("api_model", DEFAULT_API_MODEL), "api_key_set": bool(cfg.get("api_key"))})
        elif self.path == "/supervisor_config/reset":
            cfg = save_supervisor_config(DEFAULT_SUPERVISOR_PROMPT, DEFAULT_BANNED_WORDS)
            self._send(200, {"ok": True, "prompt": cfg.get("prompt", ""), "banned_words": cfg.get("banned_words", []), "provider": cfg.get("provider", DEFAULT_PROVIDER), "api_url": cfg.get("api_url", ""), "api_model": cfg.get("api_model", DEFAULT_API_MODEL), "api_key_set": bool(cfg.get("api_key"))})
        elif self.path == "/pages":
            with LOCK:
                ps = [{"page_id": pid, "title": (p.get("snapshot") or {}).get("title","")[:40],
                       "url": (p.get("snapshot") or {}).get("url","")[:70],
                       "alive": _now()-p["last_poll"]<3,
                       "is_generating": (p.get("snapshot") or {}).get("isGenerating",False),
                       "assistant_count": (p.get("snapshot") or {}).get("assistantCount",0),
                       "last_msg": ((p.get("snapshot") or {}).get("lastAssistant",""))[:80]}
                      for pid, p in PAGES.items()]
            self._send(200, {"pages": ps, "total": len(ps)})
        elif self.path.startswith("/snapshot"):
            pid = self.path.split("page=")[-1] if "page=" in self.path else None
            with LOCK:
                if pid and pid in PAGES: snap = PAGES[pid].get("snapshot")
                else:
                    alive = [(pid,p) for pid,p in PAGES.items() if _now()-p["last_poll"]<5]
                    snap = alive and sorted(alive,key=lambda x:-x[1]["last_poll"])[0][1].get("snapshot")
            self._send(200, snap or {"error":"no snapshot"})
        elif self.path == "/all_snapshots":
            with LOCK:
                out = []
                for pid,p in PAGES.items():
                    s = dict(p.get("snapshot") or {})
                    s["page_id"]=pid; s["alive"]=_now()-p["last_poll"]<3; s["age"]=round(_now()-p["last_poll"],1)
                    out.append(s)
            self._send(200, {"pages": out})
        else: self._send(404, {})

    def do_POST(self):
        try: body = self._body()
        except Exception as e: return self._send(400, {"error":str(e)})

        if self.path == "/register":
            pid = body.get("page_id") or str(uuid.uuid4())[:8]
            with LOCK:
                PAGES.setdefault(pid, {"info":{}, "queue":queue.Queue()}).update({"info":body, "last_poll":_now()})
            log(f"Registered {pid}: {body.get('title','')[:40]}")
            self._send(200, {"ok":True, "page_id":pid})

        elif self.path == "/poll":
            pid = body.get("page_id")
            if not pid: return self._send(400, {"error":"no page_id"})
            with LOCK:
                PAGES.setdefault(pid, {"info":{}, "queue":queue.Queue()})
                PAGES[pid]["last_poll"] = _now()
                if body.get("snapshot"): PAGES[pid]["snapshot"] = body["snapshot"]
                try: cmd = PAGES[pid]["queue"].get_nowait()
                except: cmd = {}
            # 发响应前先触发监督器(在 poll 线程里跑,有间隔限制不会频繁)
            supervisor_once()
            self._send(200, cmd)

        elif self.path == "/result":
            RESULTS[body.get("id")] = body.get("result",{})
            self._send(200, {"ok":True})

        elif self.path == "/supervisor_config":
            prompt = body.get("prompt")
            banned_words = body.get("banned_words")
            provider = body.get("provider")
            api_url = body.get("api_url")
            api_model = body.get("api_model")
            api_key = body.get("api_key")
            clear_api_key = bool(body.get("clear_api_key"))
            if not isinstance(prompt, str) or not prompt.strip():
                return self._send(400, {"ok": False, "error": "prompt is required"})
            if banned_words is not None and not isinstance(banned_words, list):
                return self._send(400, {"ok": False, "error": "banned_words must be a list"})
            if provider is not None and provider not in ("claude_cli", "openai_compatible"):
                return self._send(400, {"ok": False, "error": "provider must be claude_cli or openai_compatible"})
            cfg = save_supervisor_config(prompt, banned_words, provider, api_url, api_model, api_key, clear_api_key)
            self._send(200, {"ok": True, "prompt": cfg.get("prompt", ""), "banned_words": cfg.get("banned_words", []), "provider": cfg.get("provider", DEFAULT_PROVIDER), "api_url": cfg.get("api_url", ""), "api_model": cfg.get("api_model", DEFAULT_API_MODEL), "api_key_set": bool(cfg.get("api_key"))})

        elif self.path == "/send":
            text = body.get("text","").strip()
            pid = body.get("page_id")
            if not text: return self._send(400, {"error":"no text"})
            with LOCK:
                if pid and pid in PAGES: target=pid
                else:
                    alive=[p for p in PAGES if _now()-PAGES[p]["last_poll"]<5]
                    if not alive: return self._send(500, {"error":"no active page"})
                    target=alive[0]
            cid = str(uuid.uuid4())[:8]
            with LOCK: PAGES[target]["queue"].put({"id":cid,"cmd":"send","text":text})
            self._send(200, {"ok": True, "msg": "queued"})  # Do not wait for result; avoid blocking when ChatGPT is busy

        elif self.path == "/new_chat":
            with LOCK:
                alive=[p for p in PAGES if _now()-PAGES[p]["last_poll"]<5]
                if not alive: return self._send(500, {"error":"no active page"})
                target=body.get("page_id") or alive[0]
            cid=str(uuid.uuid4())[:8]
            with LOCK: PAGES[target]["queue"].put({"id":cid,"cmd":"new_chat"})
            self._send(200, _wait(cid,30))
        else: self._send(404,{})


def _wait(rid, timeout):
    deadline = _now()+timeout
    while _now()<deadline:
        if rid in RESULTS: return RESULTS.pop(rid)
        time.sleep(0.2)
    return {"ok":False, "error":"timeout"}


# ====== 监督器(由 HTTP poll 间接触发,不用独立线程)======
_last_supervisor_run = [0]

def _mark_handled(pid: str, snip: str, cooldown: int) -> None:
    """记录该页面已处理,并清除 in-flight 状态。"""
    with LOCK:
        LAST_HANDLED[pid] = {"snippet": snip, "until": _now() + cooldown}
        IN_FLIGHT.discard(pid)


def _clear_in_flight(pid: str) -> None:
    with LOCK:
        IN_FLIGHT.discard(pid)


def _snapshot_is_limited(snap: dict) -> bool:
    """Return True when ChatGPT says this conversation must move to a new chat."""
    if not snap:
        return False
    if snap.get("conversationLimited"):
        return True
    haystack = "\n".join(
        str(snap.get(k, ""))
        for k in ("editorText", "lastAssistant", "title", "url")
    ).lower()
    for turn in snap.get("recentTurns", []) or []:
        haystack += "\n" + str(turn.get("text", "")).lower()
    markers = (
        "maximum length for this conversation",
        "start a new chat",
        "starting a new chat",
        "达到此对话的最大长度",
        "对话的最大长度",
    )
    return any(marker in haystack for marker in markers)


def _snapshot_snippet(snap: dict) -> str:
    last = snap.get("lastAssistant", "") if snap else ""
    if last:
        return last[-80:]
    if _snapshot_is_limited(snap):
        return "conversation length limit"
    return "empty"


def supervisor_once():
    """监督器单次扫描。由油猴 poll 时触发(每 POLL_INTERVAL 秒最多跑一次)。"""
    now = _now()
    if now - _last_supervisor_run[0] < POLL_INTERVAL:
        return
    _last_supervisor_run[0] = now

    if not SUPERVISOR_ENABLED.get("on", True):
        log("Supervisor: disabled")
        return
    try:
        all_pages = list(PAGES.items())
        log(f"Supervisor scan: pages={len(all_pages)}")
        if not all_pages:
            return
        all_pages = list(PAGES.items())
        if not all_pages:
            return
        ps = [(pid, dict(p)) for pid, p in all_pages]
        idle_pages = []
        for pid, p in ps:
            snap = p.get("snapshot") or {}
            if now - p["last_poll"] >= 3:
                continue
            if snap.get("isGenerating"):
                continue
            last = snap.get("lastAssistant", "")
            snip = _snapshot_snippet(snap)
            turns = snap.get("recentTurns", [])
            with LOCK:
                if pid in IN_FLIGHT:
                    continue
                handled = LAST_HANDLED.get(pid)
                if handled and now < handled.get("until", 0):
                    continue
                IN_FLIGHT.add(pid)
            if not turns and not last:
                _clear_in_flight(pid)
                continue
            if _snapshot_is_limited(snap):
                log(f"  {pid} skipped: conversation length limit")
                _mark_handled(pid, snip, 3600)
                continue
            idle_pages.append((pid, turns, last or "(no reply)", snip))

        if idle_pages:
            gen = sum(1 for _, p in ps if (p.get("snapshot") or {}).get("isGenerating"))
            log(f"Scan: pages={len(ps)} pending={len(idle_pages)} generating={gen}")
            for pid, turns, last, snip in idle_pages:
                t = threading.Thread(target=handle_page, args=(pid, turns, last, snip), daemon=True)
                t.start()
    except Exception as e:
        log(f"Supervisor error: {e}")


def handle_page(pid, turns, last_reply, snip):
    """处理单个空闲页面:调 Claude + 发消息。在独立线程跑,不阻塞主循环。"""
    log(f"Page {pid} idle; calling supervisor provider...")
    d = claude_decide(turns, last_reply)
    if d["action"]=="skip":
        reason = d.get('reason','')[:50]
        log(f"  {pid} skipped: {reason}")
        # 跳过也临时标记 30 秒(Claude超时)或 90 秒(到长度上限),避免疯狂重试
        cooldown = 30 if "timeout" in reason.lower() or "timed out" in reason.lower() else 90
        _mark_handled(pid, snip, cooldown)
        return
    msg = d["message"]
    log(f"  {pid} supervisor suggestion: {msg[:70]}")
    try:
        cid = str(uuid.uuid4())[:8]
        with LOCK:
            if pid in PAGES:
                PAGES[pid]["queue"].put({"id":cid,"cmd":"send","text":msg})
                LAST_HANDLED[pid] = {"snippet": snip, "until": _now() + 90}  # 成功后标记,90秒后可重发
            IN_FLIGHT.discard(pid)
        log(f"  {pid} queued for send")
    except Exception as e:
        _clear_in_flight(pid)
        log(f"  {pid} ✗ {e}")


def main():
    load_supervisor_config()
    server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    log(f"Bridge service http://127.0.0.1:{HTTP_PORT} | supervisor triggered by poll every {POLL_INTERVAL}s")
    log("Waiting for userscript connections...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Stopped")


if __name__ == "__main__":
    main()
