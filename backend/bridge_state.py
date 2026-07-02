"""页面状态管理。

管理所有通过油猴脚本连接的 ChatGPT 页面:
  - 注册 / poll / result(油猴脚本调用)
  - 页面快照存储
  - 命令队列(send / new_chat)
  - 自动清理掉线页面
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any

# 掉线判定:超过这个秒数没 poll 就算掉线
STALE_THRESHOLD = 3
# 自动清理:超过这个秒数没 poll 就从列表移除
CLEANUP_THRESHOLD = 30


class PageState:
    """单个页面的状态。"""

    def __init__(self, page_id: str):
        self.page_id = page_id
        self.info: dict[str, Any] = {}
        self.snapshot: dict[str, Any] | None = None
        self.last_poll: float = time.time()
        self.cmd_queue: queue.Queue = queue.Queue()

    @property
    def is_alive(self) -> bool:
        return time.time() - self.last_poll < STALE_THRESHOLD

    @property
    def is_generating(self) -> bool:
        return bool((self.snapshot or {}).get("isGenerating", False))

    def to_summary(self) -> dict:
        snap = self.snapshot or {}
        return {
            "page_id": self.page_id,
            "title": snap.get("title", self.info.get("title", ""))[:50],
            "url": snap.get("url", self.info.get("url", ""))[:80],
            "alive": self.is_alive,
            "is_generating": self.is_generating,
            "assistant_count": snap.get("assistantCount", 0),
            "last_msg": snap.get("lastAssistant", "")[:100],
            "last_poll_ago": round(time.time() - self.last_poll, 1),
        }


class BridgeState:
    """全局状态管理(线程安全)。"""

    def __init__(self):
        self._pages: dict[str, PageState] = {}
        self._results: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---- 油猴脚本接口 ----
    def register(self, page_id: str | None, info: dict) -> str:
        pid = page_id or ("p" + uuid.uuid4().hex[:6])
        with self._lock:
            if pid not in self._pages:
                self._pages[pid] = PageState(pid)
            self._pages[pid].info = info
            self._pages[pid].last_poll = time.time()
        return pid

    def poll(self, page_id: str, snapshot: dict | None) -> dict:
        """油猴 poll:更新快照 + 返回待执行命令。"""
        with self._lock:
            page = self._pages.setdefault(page_id, PageState(page_id))
            page.last_poll = time.time()
            if snapshot:
                page.snapshot = snapshot
            try:
                cmd = page.cmd_queue.get_nowait()
                return cmd
            except queue.Empty:
                return {}

    def submit_result(self, cmd_id: str, result: dict) -> None:
        with self._lock:
            self._results[cmd_id] = result

    # ---- agent 接口 ----
    def list_pages(self) -> list[dict]:
        self._cleanup()
        with self._lock:
            return [p.to_summary() for p in self._pages.values()]

    def get_snapshot(self, page_id: str | None = None) -> dict | None:
        with self._lock:
            if page_id:
                return self._pages[page_id].snapshot if page_id in self._pages else None
            # 默认返回最近活跃页面
            alive = [(pid, p) for pid, p in self._pages.items() if p.is_alive]
            if alive:
                alive.sort(key=lambda x: -x[1].last_poll)
                return alive[0][1].snapshot
            return None

    def get_all_snapshots(self) -> list[dict]:
        self._cleanup()
        with self._lock:
            out = []
            for pid, p in self._pages.items():
                snap = dict(p.snapshot or {})
                snap["page_id"] = pid
                snap["alive"] = p.is_alive
                snap["age"] = round(time.time() - p.last_poll, 1)
                out.append(snap)
            return out

    def send_command(self, page_id: str | None, cmd: str, text: str = "") -> str:
        """入队一条命令,返回 cmd_id。"""
        target = self._resolve_page(page_id)
        if not target:
            raise RuntimeError("无活跃页面" if not page_id else f"页面 {page_id} 不存在或已掉线")
        cid = uuid.uuid4().hex[:8]
        payload = {"id": cid, "cmd": cmd}
        if text:
            payload["text"] = text
        with self._lock:
            self._pages[target].cmd_queue.put(payload)
        return cid

    def wait_result(self, cmd_id: str, timeout: float = 150) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if cmd_id in self._results:
                    return self._results.pop(cmd_id)
            time.sleep(0.2)
        return {"ok": False, "error": "等待油猴脚本执行超时"}

    def find_idle_page(self) -> str | None:
        """找一个空闲(活着且没在生成)的页面。"""
        with self._lock:
            for pid, p in self._pages.items():
                if p.is_alive and not p.is_generating:
                    return pid
        return None

    # ---- 内部 ----
    def _resolve_page(self, page_id: str | None) -> str | None:
        with self._lock:
            if page_id and page_id in self._pages:
                if self._pages[page_id].is_alive:
                    return page_id
                return None
            # 自动选空闲页面
            for pid, p in self._pages.items():
                if p.is_alive:
                    return pid
            return None

    def _cleanup(self):
        """移除长时间掉线的页面。"""
        now = time.time()
        with self._lock:
            stale = [pid for pid, p in self._pages.items() if now - p.last_poll > CLEANUP_THRESHOLD]
            for pid in stale:
                del self._pages[pid]


# 全局单例
state = BridgeState()
