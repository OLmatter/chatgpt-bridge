#!/usr/bin/env python3
"""桥接服务 + Claude 监督器 一体化启动。

一个进程同时跑:
  - HTTP 桥接服务 (端口 5000): 接收油猴脚本的 poll/send
  - Claude 监督器: 后台线程,发现空闲页面就调 Claude 决定回复

用法: python run_all.py
停止: Ctrl+C
"""
from __future__ import annotations
import json, queue, shutil, subprocess, sys, threading, time, uuid
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


def _now(): return time.time()


def _cleanup_stale():
    """清理掉线超30秒的页面。"""
    cutoff = _now() - 30
    with LOCK:
        stale = [pid for pid, p in PAGES.items() if p["last_poll"] < cutoff]
        for pid in stale:
            del PAGES[pid]
    if stale:
        log(f"清理 {len(stale)} 个掉线页面")


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


def claude_decide(recent_turns, last_reply):
    convo = ""
    for t in recent_turns[-6:]:
        role = "用户" if t["role"] == "user" else "ChatGPT"
        convo += f"[{role}] {t['text'][:600]}\n\n"
    prompt = f"""监督助手。下面是 ChatGPT 对话,做 Kaggle 比赛。

任务:判断要不要继续,说一句极短的鼓励。

规则:
1. 到长度上限(对话太长/请开新对话/can't continue/too long)→ 输出 SKIP
2. 否则输出一句鼓励,要求:
   - 涨分了 → 夸一句
   - 没涨分 → 说继续
   - 最多8个字,像"恭喜,请再接再厉""那就继续吧""干得漂亮,继续"
   - 绝不给建议、不提task/方法名、不解释、不加标点堆砌
   - 就一句口语,越短越好
   - ⚠️ 绝对不能用"稳"字,也不能用任何暗示保守/求稳的词

输出格式:
REPLY
<一句话>

或

SKIP
<原因>

=== 对话 ===
{convo}
=== 你的判断 ==="""
    result = ask_claude(prompt)
    if result is None:
        return {"action": "skip", "reason": "Claude超时,下次再试"}
    if result.upper().startswith("SKIP"):
        return {"action": "skip", "reason": result}
    lines = result.split("\n")
    msg = "\n".join(lines[1:]).strip() if lines[0].strip().upper() == "REPLY" else result.strip()
    # 兜底:替换掉"稳"字及保守暗示词
    for bad, good in [("稳", ""), ("保守", ""), ("求稳", ""), ("稳妥", "")]:
        msg = msg.replace(bad, good)
    msg = msg.strip()
    if not msg or len(msg) > 500:
        return {"action": "skip", "reason": "空或过长"}
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
            log(f"清理异常: {e}")
        if self.path == "/status":
            with LOCK:
                alive = sum(1 for p in PAGES.values() if _now()-p["last_poll"]<3)
                n = len(PAGES)
                pid_obj = id(PAGES)
            self._send(200, {"pages_connected": n, "pages_alive": alive, "pages_obj_id": pid_obj, "supervisor_on": SUPERVISOR_ENABLED.get("on", True)})
        elif self.path == "/supervisor_on":
            SUPERVISOR_ENABLED["on"] = True
            log("监督器已开启(GUI)")
            self._send(200, {"ok": True, "on": True})
        elif self.path == "/supervisor_off":
            SUPERVISOR_ENABLED["on"] = False
            log("监督器已关闭(GUI)")
            self._send(200, {"ok": True, "on": False})
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
            self._send(200, snap or {"error":"无快照"})
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
            log(f"注册 {pid}: {body.get('title','')[:40]}")
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

        elif self.path == "/send":
            text = body.get("text","").strip()
            pid = body.get("page_id")
            if not text: return self._send(400, {"error":"no text"})
            with LOCK:
                if pid and pid in PAGES: target=pid
                else:
                    alive=[p for p in PAGES if _now()-PAGES[p]["last_poll"]<5]
                    if not alive: return self._send(500, {"error":"无活跃页面"})
                    target=alive[0]
            cid = str(uuid.uuid4())[:8]
            with LOCK: PAGES[target]["queue"].put({"id":cid,"cmd":"send","text":text})
            self._send(200, {"ok": True, "msg": "已入队"})  # 不等result,避免busy卡死阻塞

        elif self.path == "/new_chat":
            with LOCK:
                alive=[p for p in PAGES if _now()-PAGES[p]["last_poll"]<5]
                if not alive: return self._send(500, {"error":"无活跃页面"})
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
    return {"ok":False, "error":"超时"}


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

def supervisor_once():
    """监督器单次扫描。由油猴 poll 时触发(每 POLL_INTERVAL 秒最多跑一次)。"""
    now = _now()
    if now - _last_supervisor_run[0] < POLL_INTERVAL:
        return
    _last_supervisor_run[0] = now

    if not SUPERVISOR_ENABLED.get("on", True):
        log("监督器: 已关闭")
        return
    try:
        all_pages = list(PAGES.items())
        log(f"监督器扫描: PAGES={len(all_pages)}")
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
            snip = last[-80:] if last else "empty"
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
            idle_pages.append((pid, turns, last or "(无回复)", snip))

        if idle_pages:
            gen = sum(1 for _, p in ps if (p.get("snapshot") or {}).get("isGenerating"))
            log(f"扫描: {len(ps)}页 待处理{len(idle_pages)} 生成中{gen}")
            for pid, turns, last, snip in idle_pages:
                t = threading.Thread(target=handle_page, args=(pid, turns, last, snip), daemon=True)
                t.start()
    except Exception as e:
        log(f"监督器异常: {e}")


def handle_page(pid, turns, last_reply, snip):
    """处理单个空闲页面:调 Claude + 发消息。在独立线程跑,不阻塞主循环。"""
    log(f"页面 {pid} 空闲,调 Claude...")
    d = claude_decide(turns, last_reply)
    if d["action"]=="skip":
        reason = d.get('reason','')[:50]
        log(f"  {pid} 跳过: {reason}")
        # 跳过也临时标记 30 秒(Claude超时)或 90 秒(到长度上限),避免疯狂重试
        cooldown = 30 if '超时' in reason else 90
        _mark_handled(pid, snip, cooldown)
        return
    msg = d["message"]
    log(f"  {pid} Claude建议: {msg[:70]}")
    try:
        cid = str(uuid.uuid4())[:8]
        with LOCK:
            if pid in PAGES:
                PAGES[pid]["queue"].put({"id":cid,"cmd":"send","text":msg})
                LAST_HANDLED[pid] = {"snippet": snip, "until": _now() + 90}  # 成功后标记,90秒后可重发
            IN_FLIGHT.discard(pid)
        log(f"  {pid} ✓ 已入队发送")
    except Exception as e:
        _clear_in_flight(pid)
        log(f"  {pid} ✗ {e}")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    log(f"桥接服务 http://127.0.0.1:{HTTP_PORT} | 监督器由 poll 触发(每{POLL_INTERVAL}s)")
    log("等待油猴脚本连接...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("已停止")


if __name__ == "__main__":
    main()
