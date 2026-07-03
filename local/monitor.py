#!/usr/bin/env python3
"""ChatGPT Bridge 监控 GUI(ttkinter 小窗口)。

纯只读监控,不影响后端。每 2 秒刷新。

启动: python monitor.py
"""
import json
import sys
import threading
import time
import urllib.request
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import tkinter as tk
from tkinter import ttk

BASE = "http://127.0.0.1:5000"
REFRESH_INTERVAL = 2000  # 毫秒


def fetch(path):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


class MonitorApp:
    def __init__(self, root):
        self.root = root
        root.title("ChatGPT Bridge 监控")
        root.geometry("560x440")
        root.minsize(480, 300)

        # 顶部状态栏
        top = ttk.Frame(root, padding=8)
        top.pack(fill=tk.X)
        self.lbl_status = ttk.Label(top, text="连接中...", font=("Consolas", 10, "bold"))
        self.lbl_status.pack(side=tk.LEFT)
        self.lbl_sup = ttk.Label(top, text="", font=("Consolas", 9))
        self.lbl_sup.pack(side=tk.RIGHT)

        # 表格
        frame = ttk.Frame(root, padding=(8, 0, 8, 8))
        frame.pack(fill=tk.BOTH, expand=True)

        cols = ("status", "title", "duration", "msgs")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=15)
        self.tree.heading("status", text="状态")
        self.tree.heading("title", text="窗口")
        self.tree.heading("duration", text="时长")
        self.tree.heading("msgs", text="消息")
        self.tree.column("status", width=50, anchor=tk.CENTER)
        self.tree.column("title", width=280, anchor=tk.W)
        self.tree.column("duration", width=120, anchor=tk.CENTER)
        self.tree.column("msgs", width=60, anchor=tk.CENTER)

        # 行颜色标签
        self.tree.tag_configure("gen", foreground="#2a8a2a")     # 绿
        self.tree.tag_configure("idle", foreground="#666666")    # 灰
        self.tree.tag_configure("idle_long", foreground="#cc8800", background="#fff8e0")  # 黄高亮
        self.tree.tag_configure("dead", foreground="#cc3333")    # 红

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 底部
        bottom = ttk.Frame(root, padding=(8, 0, 8, 8))
        bottom.pack(fill=tk.X)
        self.var_pause = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="暂停刷新", variable=self.var_pause).pack(side=tk.LEFT)

        # 自动回复开关
        self.var_autoreply = tk.BooleanVar(value=True)
        self.btn_autoreply = ttk.Checkbutton(bottom, text="自动回复", variable=self.var_autoreply, command=self._toggle_autoreply)
        self.btn_autoreply.pack(side=tk.LEFT, padx=(15, 0))

        ttk.Label(bottom, text="每2s刷新", font=("Consolas", 8), foreground="#999").pack(side=tk.RIGHT)

        # 记录每个页面进入当前状态的时间(用于算累积时长)
        # key: page_id, value: (状态名, 进入时刻)
        self._state_since = {}  # pid -> (state, timestamp)

        self._refresh()

    def _toggle_autoreply(self):
        """开关自动回复。"""
        if self.var_autoreply.get():
            fetch("/supervisor_on")
        else:
            fetch("/supervisor_off")

    def _refresh(self):
        """定时刷新(在主线程跑,线程安全)。"""
        if not self.var_pause.get():
            self._update()
        self.root.after(REFRESH_INTERVAL, self._refresh)

    def _update(self):
        status = fetch("/status")
        data = fetch("/all_snapshots")

        if status is None and data is None:
            self.lbl_status.config(text="● 后端未连接", foreground="#cc3333")
            self.lbl_sup.config(text="")
            return

        pages = (data or {}).get("pages", [])
        now = time.time()

        # 清空旧行
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 按状态排序:生成中 > 空闲 > 掉线
        def sort_key(p):
            age = p.get("age", 999)
            gen = p.get("isGenerating", False)
            if age >= 3:
                return (2, age)
            if gen:
                return (0, age)
            return (1, age)

        pages.sort(key=sort_key)

        alive_n = gen_n = idle_n = dead_n = 0
        for p in pages:
            pid = p.get("page_id") or "?"
            age = p.get("age", 999)
            gen = p.get("isGenerating", False)
            title = (p.get("title") or "").strip()[:25] or "(无标题)"
            msgs = p.get("assistantCount", 0)

            # 判断状态
            if age >= 3:
                state_name = "dead"
                icon = "🔴"
                tag = "dead"
                dead_n += 1
            elif gen:
                state_name = "gen"
                icon = "🟢"
                tag = "gen"
                gen_n += 1
                alive_n += 1
            else:
                state_name = "idle"
                icon = "✅"
                tag = "idle"
                idle_n += 1
                alive_n += 1

            # 累积时长:状态没变就累加,变了就重新计时
            prev = self._state_since.get(pid)
            if prev and prev[0] == state_name:
                dur_seconds = int(now - prev[1])
            else:
                self._state_since[pid] = (state_name, now)
                dur_seconds = 0

            # 空闲超30秒高亮
            if state_name == "idle" and dur_seconds > 30:
                tag = "idle_long"

            # 格式化时长
            if dur_seconds >= 3600:
                dur = f"{'掉线' if state_name=='dead' else '生成' if state_name=='gen' else '停'} {dur_seconds//3600}h{(dur_seconds%3600)//60}m"
            elif dur_seconds >= 60:
                dur = f"{'掉线' if state_name=='dead' else '生成' if state_name=='gen' else '停'} {dur_seconds//60}m{dur_seconds%60}s"
            else:
                dur = f"{'掉线' if state_name=='dead' else '生成' if state_name=='gen' else '停'} {dur_seconds}s"

            self.tree.insert("", tk.END, values=(icon, title, dur, msgs), tags=(tag,))

        # 顶部汇总
        sup_on = (status or {}).get("supervisor_on", True) if status else False
        # 同步 checkbox(不触发 command)
        if self.var_autoreply.get() != sup_on:
            self.var_autoreply.set(sup_on)
        self.lbl_status.config(
            text=f"活跃 {alive_n}  |  生成 {gen_n}  |  空闲 {idle_n}  |  掉线 {dead_n}",
            foreground="#333333",
        )
        self.lbl_sup.config(
            text=f"自动回复 {'✓开' if sup_on else '✗关'}",
            foreground="#2a8a2a" if sup_on else "#cc3333",
        )


def main():
    root = tk.Tk()
    app = MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
