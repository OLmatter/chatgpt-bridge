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
LAST_HANDLED = {}  # 监督器: page_id -> snippet 防重复
SUPERVISOR_ENABLED = {"on": True}  # 监督器总开关,GUI 可控制


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


# ====== Claude 调用 ======
def ask_claude(prompt):
    """调 Claude CLI,超时则跳过返回 None(监督器下次再试)。"""
    for attempt in range(2):  # 最多重试2次
        try:
            r = subprocess.run([CLAUDE_CMD, "--print"], input=prompt,
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
            return r.stdout.strip()
        except subprocess.TimeoutExpired:
            if attempt == 0:
                time.sleep(2)  # 第一次超时,等2秒重试
                continue
            return None  # 第二次还超时,跳过
        except Exception:
            return None
    return None


def claude_decide(recent_turns, last_reply):
    convo = ""
    for t in recent_turns[-6:]:
        role = "用户" if t["role"] == "user" else "ChatGPT"
        convo += f"[{role}] {t['text'][:600]}\n\n"
    prompt = f"""监督助手。下面是 ChatGPT 对话,做 Kaggle 比赛。

任务:判断要不要继续,说一句极短的鼓励。

判断涨分大小:
- 如果回复里出现 +0.00X 这种小数(比如 +0.002, +0.008),那就是"微分"(≤0.01)
- 如果出现 +0.01 或更大(比如 +0.05),那才是"大涨分"

规则:
1. 到长度上限(对话太长/请开新对话/can't continue/too long)→ 输出 SKIP
2. 否则:
   - 大涨分(>0.01)→ 夸一句,像"漂亮,继续""干得好"
   - 微分(≤0.01,就是+0.00X这种)→ 催它吃大分,像"微分够了该冲大的了""别磨0.00X了冲大分""蚊子腿吃够该换大目标了""小分别浪费token了"
   - 没涨分 → 催换方向,像"换个方向冲大分""别纠结了换路子"
   - 最多12个字,口语
   - 绝不给具体技术建议、不提task名/方法名
   - ⚠️ 绝对不能用"稳"字或保守暗示词
   - 核心:别让它满足于磨微分,催它大胆冲大分

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
            r = _wait(cid, 150)
            self._send(200, r)

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


# ====== 监督器线程 ======
def supervisor_loop():
    time.sleep(3)
    log("监督器启动: 扫描空闲页面 → Claude 决策 → 自动回复(并发)")
    tick = 0
    while True:
        # 开关关闭时跳过(但仍打印心跳)
        if not SUPERVISOR_ENABLED.get("on", True):
            tick += 1
            if tick % 5 == 1:
                log(f"心跳#{tick}: 监督器已关闭(PAGES={len(PAGES)})")
            time.sleep(POLL_INTERVAL)
            continue
        try:
            tick += 1
            with LOCK:
                all_pages = list(PAGES.items())
            if tick % 5 == 1:
                log(f"心跳#{tick}: PAGES={len(all_pages)} id={id(PAGES)}")
            ps = [(pid, dict(p)) for pid,p in all_pages]
            # 调试:看活跃页面的状态
            alive_debug = [(pid, _now()-p.get("last_poll",0), (p.get("snapshot") or {}).get("isGenerating"), bool((p.get("snapshot") or {}).get("lastAssistant"))) for pid,p in ps if _now()-p.get("last_poll",0)<10]
            idle_pages = []
            for pid, p in ps:
                snap = p.get("snapshot") or {}
                if _now()-p["last_poll"]>=3: continue
                if snap.get("isGenerating"): continue
                last = snap.get("lastAssistant","")
                snip = last[-80:]
                handled = LAST_HANDLED.get(pid)
                # handled 格式: "时间戳|snip" —— 90秒内同 snip 不重发,超时重发
                should_skip = False
                if handled and "|" in handled:
                    parts = handled.split("|", 1)
                    try:
                        ts = float(parts[0])
                        old_snip = parts[1] if len(parts) > 1 else ""
                        if old_snip == snip and _now() - ts < 90:
                            should_skip = True  # 90秒内同回复不重发
                    except ValueError:
                        pass
                if should_skip: continue
                # 即使 lastAssistant 为空也处理(新对话/刚刷新的页面需要督促)
                turns = snap.get("recentTurns", [])
                idle_pages.append((pid, turns, last or "(无回复历史,新开对话)", last[-80:] if last else "empty"))
            gen = sum(1 for _,p in ps if (p.get("snapshot") or {}).get("isGenerating"))
            idle_count = len([1 for _,p in ps if _now()-p["last_poll"]<3 and not (p.get("snapshot") or {}).get("isGenerating")])
            if idle_pages:
                log(f"扫描: {len(ps)}页 空闲{idle_count} 待处理{len(idle_pages)} 生成中{gen} | 活跃debug: {[(p[:12], f'{a:.1f}s', g, has) for p,a,g,has in alive_debug[:4]]}")
                for pid, turns, last, snip in idle_pages:
                    # 不提前标记!在 handle_page 里成功发送后才标记
                    t = threading.Thread(target=handle_page, args=(pid, turns, last, snip), daemon=True)
                    t.start()
            elif idle_count > 0:
                log(f"扫描: {len(ps)}页 空闲{idle_count} 但都已被处理(等新回复) 生成中{gen} | debug: {[(p[:12], f'{a:.1f}s', g) for p,a,g,has in alive_debug[:4]]}")
            else:
                # 有活跃页面但全在生成
                if alive_debug:
                    log(f"扫描: {len(ps)}页 活跃{len(alive_debug)} 全生成中 gen={gen}")
        except Exception as e:
            log(f"监督器异常: {e}")
        time.sleep(POLL_INTERVAL)


def handle_page(pid, turns, last_reply, snip):
    """处理单个空闲页面:调 Claude + 发消息。在独立线程跑,不阻塞主循环。"""
    log(f"页面 {pid} 空闲,调 Claude...")
    d = claude_decide(turns, last_reply)
    if d["action"]=="skip":
        reason = d.get('reason','')[:50]
        log(f"  {pid} 跳过: {reason}")
        # 跳过也临时标记 30 秒(Claude超时)或 90 秒(到长度上限),避免疯狂重试
        cooldown = 30 if '超时' in reason else 90
        LAST_HANDLED[pid] = f"{_now()}|{snip}"
        return
    msg = d["message"]
    log(f"  {pid} Claude建议: {msg[:70]}")
    try:
        cid = str(uuid.uuid4())[:8]
        with LOCK:
            if pid in PAGES:
                PAGES[pid]["queue"].put({"id":cid,"cmd":"send","text":msg})
                LAST_HANDLED[pid] = f"{_now()}|{snip}"  # 成功后标记,90秒后可重发
        log(f"  {pid} ✓ 已入队发送")
    except Exception as e:
        log(f"  {pid} ✗ {e}")


def main():
    t = threading.Thread(target=supervisor_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    log(f"桥接服务 http://127.0.0.1:{HTTP_PORT} | 监督器每{POLL_INTERVAL}s扫描")
    log("等待油猴脚本连接...")
    try: server.serve_forever()
    except KeyboardInterrupt: log("已停止")


if __name__ == "__main__":
    main()
