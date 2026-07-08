#!/usr/bin/env python3
"""ChatGPT Bridge monitor GUI (small tkinter window).

Read-only monitor. Refreshes every 2 seconds and does not affect the backend.

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
from tkinter import messagebox, ttk

BASE = "http://127.0.0.1:5000"
REFRESH_INTERVAL = 2000  # 毫秒


def fetch(path):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def post_json(path, payload):
    try:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}

class MonitorApp:
    def __init__(self, root):
        self.root = root
        root.title("ChatGPT Bridge Monitor")
        root.geometry("560x440")
        root.minsize(480, 300)

        # 顶部状态栏
        top = ttk.Frame(root, padding=8)
        top.pack(fill=tk.X)
        self.lbl_status = ttk.Label(top, text="Connecting...", font=("Consolas", 10, "bold"))
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
        ttk.Checkbutton(bottom, text="Pause refresh", variable=self.var_pause).pack(side=tk.LEFT)

        # Auto-reply toggle
        self.var_autoreply = tk.BooleanVar(value=True)
        self.btn_autoreply = ttk.Checkbutton(bottom, text="Auto-reply", variable=self.var_autoreply, command=self._toggle_autoreply)
        self.btn_autoreply.pack(side=tk.LEFT, padx=(15, 0))
        self.btn_prompt = ttk.Button(bottom, text="Prompt...", command=self._edit_prompt)
        self.btn_prompt.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(bottom, text="Refresh every 2s", font=("Consolas", 8), foreground="#999").pack(side=tk.RIGHT)

        # 记录每个页面进入当前状态的时间(用于算累积时长)
        # key: page_id, value: (状态名, 进入时刻)
        self._state_since = {}  # pid -> (state, timestamp)

        self._refresh()

    def _load_prompt_config(self):
        cfg = fetch("/supervisor_config")
        if not cfg or not cfg.get("ok"):
            messagebox.showerror("Prompt", "Backend is not connected or config is unavailable.")
            return None
        return cfg

    def _save_prompt_config(self, win, text_widget, banned_entry):
        prompt = text_widget.get("1.0", tk.END).strip()
        banned_words = [x.strip() for x in banned_entry.get().split(",") if x.strip()]
        result = post_json("/supervisor_config", {"prompt": prompt, "banned_words": banned_words})
        if result and result.get("ok"):
            messagebox.showinfo("Prompt", "Prompt saved. The next auto-reply will use it.")
            win.destroy()
        else:
            messagebox.showerror("Prompt", f"Save failed: {(result or {}).get('error', 'unknown error')}")

    def _reset_prompt_config(self, text_widget, banned_entry):
        cfg = fetch("/supervisor_config/reset")
        if not cfg or not cfg.get("ok"):
            messagebox.showerror("Prompt", "Reset failed.")
            return
        text_widget.delete("1.0", tk.END)
        text_widget.insert("1.0", cfg.get("prompt", ""))
        banned_entry.delete(0, tk.END)
        banned_entry.insert(0, ", ".join(cfg.get("banned_words", [])))

    def _edit_prompt(self):
        cfg = self._load_prompt_config()
        if cfg is None:
            return
        win = tk.Toplevel(self.root)
        win.title("Supervisor Prompt")
        win.geometry("760x560")
        win.minsize(560, 420)
        win.transient(self.root)
        win.grab_set()

        header = ttk.Frame(win, padding=(10, 10, 10, 4))
        header.pack(fill=tk.X)
        ttk.Label(header, text="Supervisor prompt", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="Use {convo} where recent turns should be inserted.", foreground="#666").pack(side=tk.RIGHT)

        body = ttk.Frame(win, padding=(10, 0, 10, 8))
        body.pack(fill=tk.BOTH, expand=True)
        text_widget = tk.Text(body, wrap=tk.WORD, undo=True, height=20)
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scroll.set)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.insert("1.0", cfg.get("prompt", ""))

        banned_frame = ttk.Frame(win, padding=(10, 0, 10, 8))
        banned_frame.pack(fill=tk.X)
        ttk.Label(banned_frame, text="Banned words (comma-separated):").pack(side=tk.LEFT)
        banned_entry = ttk.Entry(banned_frame)
        banned_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        banned_entry.insert(0, ", ".join(cfg.get("banned_words", [])))

        buttons = ttk.Frame(win, padding=(10, 0, 10, 10))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Save", command=lambda: self._save_prompt_config(win, text_widget, banned_entry)).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(buttons, text="Reset default", command=lambda: self._reset_prompt_config(text_widget, banned_entry)).pack(side=tk.LEFT)

    def _toggle_autoreply(self):
        """Toggle auto-reply."""
        if self.var_autoreply.get():
            fetch("/supervisor_on")
        else:
            fetch("/supervisor_off")

    def _refresh(self):
        """Periodic refresh on the main thread."""
        if not self.var_pause.get():
            self._update()
        self.root.after(REFRESH_INTERVAL, self._refresh)

    def _update(self):
        status = fetch("/status")
        data = fetch("/all_snapshots")

        if status is None and data is None:
            self.lbl_status.config(text="● Backend disconnected", foreground="#cc3333")
            self.lbl_sup.config(text="")
            return

        pages = (data or {}).get("pages", [])
        now = time.time()

        # 清空旧行
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 先算每个页面的状态 + 累积时长(用于排序和显示)
        page_infos = []
        for p in pages:
            pid = p.get("page_id") or "?"
            age = p.get("age", 999)
            gen = p.get("isGenerating", False)
            if age >= 3:
                state_name = "dead"
            elif gen:
                state_name = "gen"
            else:
                state_name = "idle"
            prev = self._state_since.get(pid)
            if prev and prev[0] == state_name:
                dur = now - prev[1]
            else:
                self._state_since[pid] = (state_name, now)
                dur = 0
            page_infos.append((p, pid, state_name, dur))

        # 排序: 生成中(时长越长越上) > 空闲(时长越短越上) > 掉线
        # 组号: gen=0, idle=1, dead=2; gen组内按时长降序, idle组内按时长升序
        page_infos.sort(key=lambda x: (
            0 if x[2]=="gen" else (1 if x[2]=="idle" else 2),
            -x[3] if x[2]=="gen" else x[3]
        ))

        alive_n = gen_n = idle_n = dead_n = 0
        for p, pid, state_name, dur in page_infos:
            title = (p.get("title") or "").strip()[:25] or "(无标题)"
            msgs = p.get("assistantCount", 0)
            dur_seconds = int(dur)

            if state_name == "dead":
                icon = "🔴"; tag = "dead"; dead_n += 1
            elif state_name == "gen":
                icon = "🟢"; tag = "gen"; gen_n += 1; alive_n += 1
            else:
                icon = "✅"; tag = "idle"; idle_n += 1; alive_n += 1

            # 空闲超30秒高亮
            if state_name == "idle" and dur_seconds > 30:
                tag = "idle_long"

            # 格式化时长
            label = "掉线" if state_name=="dead" else ("生成" if state_name=="gen" else "停")
            if dur_seconds >= 3600:
                dur_s = f"{label} {dur_seconds//3600}h{(dur_seconds%3600)//60}m"
            elif dur_seconds >= 60:
                dur_s = f"{label} {dur_seconds//60}m{dur_seconds%60}s"
            else:
                dur_s = f"{label} {dur_seconds}s"

            self.tree.insert("", tk.END, values=(icon, title, dur_s, msgs), tags=(tag,))

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
            text=f"Auto-reply {'✓ on' if sup_on else '✗ off'}",
            foreground="#2a8a2a" if sup_on else "#cc3333",
        )


def main():
    root = tk.Tk()
    app = MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

