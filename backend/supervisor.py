"""Claude 监督器(可选)。

自动监控所有空闲的 ChatGPT 窗口,调用 Claude CLI 决定该说什么,
然后发鼓励/催促消息让 ChatGPT 继续。

prompt、禁用词、轮询间隔都从 config.yaml 读。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time

from .bridge_state import state

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Supervisor:
    """监督器:扫描空闲页面 → Claude 决策 → 自动发消息。"""

    def __init__(self, config: dict, stop_event: threading.Event):
        self.config = config
        self.stop_event = stop_event
        self.claude_cmd = config.get("claude_cmd") or shutil.which("claude") or "claude"
        self.poll_interval = config.get("poll_interval", 8)
        self.prompt_template = config.get("prompt", "监督助手。判断要不要继续,说一句鼓励。")
        self.banned_words: list[str] = config.get("banned_words", [])
        # page_id -> 上次处理的回复片段(防重复)
        self.last_handled: dict[str, str] = {}

    # ---- Claude 调用 ----
    def ask_claude(self, prompt: str) -> str:
        try:
            r = subprocess.run(
                [self.claude_cmd, "--print"],
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            return r.stdout.strip()
        except Exception as e:
            return f"[CLAUDE_ERROR: {e}]"

    def decide(self, recent_turns: list, last_reply: str) -> dict:
        """让 Claude 读对话,决定回复内容。"""
        convo = ""
        for t in recent_turns[-6:]:
            role = "用户" if t.get("role") == "user" else "ChatGPT"
            convo += f"[{role}] {t.get('text', '')[:600]}\n\n"

        prompt = f"""{self.prompt_template}

=== 对话 ===
{convo}
=== 你的判断 ==="""

        result = self.ask_claude(prompt)

        if result.upper().startswith("SKIP"):
            return {"action": "skip", "reason": result}

        lines = result.split("\n")
        msg = "\n".join(lines[1:]).strip() if lines[0].strip().upper() == "REPLY" else result.strip()

        # 禁用词过滤
        for word in self.banned_words:
            msg = msg.replace(word, "")
        msg = msg.strip()

        if not msg or len(msg) > 500:
            return {"action": "skip", "reason": "空或过长"}
        return {"action": "reply", "message": msg}

    # ---- 单页面处理(独立线程) ----
    def handle_page(self, page_id: str, turns: list, last_reply: str, snippet: str):
        log(f"页面 {page_id} 空闲,调 Claude...")
        d = self.decide(turns, last_reply)
        if d["action"] == "skip":
            log(f"  {page_id} 跳过: {d.get('reason', '')[:50]}")
            return
        msg = d["message"]
        log(f"  {page_id} Claude建议: {msg[:70]}")
        try:
            state.send_command(page_id, "send", msg)
            log(f"  {page_id} ✓ 已入队发送")
        except Exception as e:
            log(f"  {page_id} ✗ {e}")

    # ---- 主循环 ----
    def run(self):
        log(f"监督器启动 (每{self.poll_interval}s扫描, 禁用词={self.banned_words})")
        time.sleep(2)
        while not self.stop_event.is_set():
            try:
                pages = state.list_pages()
                idle = []
                for p in pages:
                    if not p["alive"] or p["is_generating"]:
                        continue
                    snap = state.get_snapshot(p["page_id"]) or {}
                    last = snap.get("lastAssistant", "")
                    snippet = last[-80:]
                    if self.last_handled.get(p["page_id"]) == snippet:
                        continue
                    if not last or len(last) < 20:
                        continue
                    idle.append((p["page_id"], snap.get("recentTurns", []), last, snippet))

                gen_count = sum(1 for p in pages if p["is_generating"])
                if idle:
                    log(f"扫描: {len(pages)}页 空闲{len(idle)}待处理 生成中{gen_count}")
                    for pid, turns, last, snippet in idle:
                        t = threading.Thread(
                            target=self.handle_page,
                            args=(pid, turns, last, snippet),
                            daemon=True,
                        )
                        t.start()
                        self.last_handled[pid] = snippet
            except Exception as e:
                log(f"监督器异常: {e}")

            # 分段 sleep,以便及时响应 stop
            for _ in range(self.poll_interval * 2):
                if self.stop_event.is_set():
                    break
                time.sleep(0.5)

        log("监督器已停止")
