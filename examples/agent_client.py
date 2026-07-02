"""ChatGPT Bridge Python 客户端 —— agent 集成示例。

封装了常用的 API 调用,agent 程序直接 import 使用。

用法:
    from agent_client import ChatGPTBridge

    bridge = ChatGPTBridge("http://127.0.0.1:5000")

    # 看有哪些窗口
    pages = bridge.list_pages()
    for p in pages:
        print(f"  [{p['page_id']}] {p['title']} {'生成中' if p['is_generating'] else '空闲'}")

    # 找一个空闲窗口
    pid = bridge.find_idle()
    if pid:
        # 发消息等回复
        reply = bridge.send(pid, "总结一下我们刚才讨论的")
        print(reply)

    # 看某个窗口的完整对话
    snap = bridge.snapshot(pid)
    for t in snap["recentTurns"]:
        print(f"  [{t['role']}] {t['text'][:100]}")
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error


class ChatGPTBridge:
    """ChatGPT WebUI Bridge 客户端。"""

    def __init__(self, base_url: str = "http://127.0.0.1:5000"):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = self.base_url + path
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=200) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"}
        except urllib.error.URLError as e:
            return {"error": f"连接失败: {e}"}

    # ---- 查询 ----
    def status(self) -> dict:
        """服务状态。"""
        return self._request("GET", "/status")

    def list_pages(self) -> list[dict]:
        """列出所有 ChatGPT 窗口摘要。"""
        return self._request("GET", "/pages").get("pages", [])

    def snapshot(self, page_id: str | None = None) -> dict:
        """获取页面快照(最近对话、输入框、生成状态)。"""
        path = f"/snapshot?page={page_id}" if page_id else "/snapshot"
        return self._request("GET", path)

    def all_snapshots(self) -> list[dict]:
        """所有窗口的快照。"""
        return self._request("GET", "/all_snapshots").get("pages", [])

    def find_idle(self) -> str | None:
        """找一个空闲窗口的 page_id,没有返回 None。"""
        r = self._request("GET", "/idle")
        return r.get("page_id")

    # ---- 操作 ----
    def send(self, text: str, page_id: str | None = None, timeout: int = 180) -> str:
        """发消息并等回复。不指定 page_id 则自动选空闲窗口。

        返回 ChatGPT 的回复文本。失败返回错误信息。
        """
        r = self._request("POST", "/send", {"text": text, "page_id": page_id})
        if r.get("ok"):
            return r.get("reply", "")
        return f"[错误] {r.get('error', r.get('detail', '未知'))}"

    def send_async(self, text: str, page_id: str | None = None) -> dict:
        """异步发消息(不等回复)。适合批量发或监督器场景。"""
        return self._request("POST", "/send_async", {"text": text, "page_id": page_id})

    def new_chat(self, page_id: str | None = None) -> dict:
        """开新对话。"""
        return self._request("POST", "/new_chat", {"page_id": page_id})

    # ---- 监督器 ----
    def start_supervisor(self) -> dict:
        """启动 Claude 监督器。"""
        return self._request("POST", "/supervisor/start")

    def stop_supervisor(self) -> dict:
        """停止监督器。"""
        return self._request("POST", "/supervisor/stop")


# ====== 命令行交互(演示) ======
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore

    bridge = ChatGPTBridge()

    print("=== ChatGPT Bridge 客户端 ===")
    print("命令: 直接输入对话 | :pages | :snap <id> | :idle | :start | :stop | :quit\n")

    while True:
        try:
            msg = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            break
        if not msg:
            continue
        if msg in (":quit", ":q"):
            break
        if msg == ":pages":
            for p in bridge.list_pages():
                st = "🟢生成中" if p["is_generating"] else ("✅空闲" if p["alive"] else "🔴掉线")
                print(f"  [{p['page_id']}] {st} {p['title'][:30]}")
        elif msg.startswith(":snap"):
            pid = msg.split(" ", 1)[1] if " " in msg else None
            snap = bridge.snapshot(pid)
            for t in snap.get("recentTurns", []):
                print(f"  [{t['role']}] {t['text'][:100]}")
        elif msg == ":idle":
            pid = bridge.find_idle()
            print(f"  空闲窗口: {pid}" if pid else "  无空闲窗口")
        elif msg == ":start":
            print(bridge.start_supervisor())
        elif msg == ":stop":
            print(bridge.stop_supervisor())
        else:
            reply = bridge.send(msg)
            print(f"\nGPT > {reply}\n")
