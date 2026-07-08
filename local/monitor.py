#!/usr/bin/env python3
"""ChatGPT Bridge monitor GUI (small tkinter window).

Read-only monitor. Refreshes every 2 seconds and does not affect the backend.

启动: python monitor.py
"""
import json
import atexit
import os
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
MONITOR_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.lock")
MONITOR_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_config.json")
REFRESH_INTERVAL = 2000  # milliseconds



def acquire_monitor_lock():
    """Allow only one monitor GUI window per local checkout."""
    if sys.platform != "win32":
        return None
    try:
        import msvcrt
        lock_file = open(MONITOR_LOCK_PATH, "a+", encoding="utf-8")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()

        def _release():
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                lock_file.close()
            except Exception:
                pass
        atexit.register(_release)
        return lock_file
    except OSError:
        print("Monitor GUI is already running; not starting a duplicate window.", flush=True)
        sys.exit(0)


TEXT = {
    "en": {
        "connecting": "Connecting...",
        "backend_disconnected": "● Backend disconnected",
        "status": "Status",
        "window": "Window",
        "duration": "Duration",
        "messages": "Messages",
        "pause_refresh": "Pause refresh",
        "auto_reply": "Auto-reply",
        "prompt_button": "Prompt...",
        "refresh_every": "Refresh every 2s",
        "language": "Language",
        "untitled": "(Untitled)",
        "offline": "Offline",
        "generating": "Generating",
        "idle": "Idle",
        "summary": "Active {alive}  |  Generating {gen}  |  Idle {idle}  |  Offline {dead}",
        "auto_on": "Auto-reply ✓ on",
        "auto_off": "Auto-reply ✗ off",
        "prompt_title": "Supervisor Prompt",
        "prompt_heading": "Supervisor prompt",
        "advanced": "Advanced setting",
        "editing_guide": "Editing guide",
        "help_text": "You can change wording, language, examples, and strictness.\nKeep the output format: REPLY on the first line for a message, or SKIP on the first line to avoid replying.\nRecommended: keep {convo}; it marks where recent turns are inserted. If removed, the backend appends the conversation automatically.",
        "banned_words": "Banned words (comma-separated):",
        "save": "Save",
        "cancel": "Cancel",
        "reset_default": "Reset default",
        "prompt_error_unavailable": "Backend is not connected or config is unavailable.",
        "prompt_saved": "Prompt saved. The next auto-reply will use it.",
        "save_failed": "Save failed: {error}",
        "reset_failed": "Reset failed.",
    },
    "zh": {
        "connecting": "连接中...",
        "backend_disconnected": "● 后端未连接",
        "status": "状态",
        "window": "窗口",
        "duration": "时长",
        "messages": "消息",
        "pause_refresh": "暂停刷新",
        "auto_reply": "自动回复",
        "prompt_button": "提示词...",
        "refresh_every": "每 2 秒刷新",
        "language": "语言",
        "untitled": "(无标题)",
        "offline": "掉线",
        "generating": "生成中",
        "idle": "空闲",
        "summary": "活跃 {alive}  |  生成中 {gen}  |  空闲 {idle}  |  掉线 {dead}",
        "auto_on": "自动回复 ✓ 开",
        "auto_off": "自动回复 ✗ 关",
        "prompt_title": "监督器提示词",
        "prompt_heading": "监督器提示词",
        "advanced": "高级设置",
        "editing_guide": "编辑说明",
        "help_text": "可以修改措辞、语言、例子和严格程度。\n请保留输出格式：第一行 REPLY 表示发送消息，第一行 SKIP 表示不回复。\n建议保留 {convo}，它表示插入最近对话的位置；如果删除，后端会自动把对话追加到 prompt 后面。",
        "banned_words": "禁用词（逗号分隔）：",
        "save": "保存",
        "cancel": "取消",
        "reset_default": "恢复默认",
        "prompt_error_unavailable": "后端未连接，或配置不可用。",
        "prompt_saved": "提示词已保存，下一次自动回复会使用它。",
        "save_failed": "保存失败：{error}",
        "reset_failed": "恢复默认失败。",
    },
}

LANG_OPTIONS = {"English": "en", "中文": "zh"}
LANG_LABELS = {v: k for k, v in LANG_OPTIONS.items()}


def load_monitor_config():
    try:
        with open(MONITOR_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        lang = data.get("language", "en")
        return {"language": lang if lang in TEXT else "en"}
    except Exception:
        return {"language": "en"}


def save_monitor_config(language):
    if language not in TEXT:
        language = "en"
    with open(MONITOR_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"language": language}, f, ensure_ascii=False, indent=2)

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
        self.lang = load_monitor_config().get("language", "en")
        self.t = TEXT[self.lang]
        root.title("ChatGPT Bridge Monitor")
        root.geometry("640x440")
        root.minsize(480, 300)

        # 顶部状态栏
        top = ttk.Frame(root, padding=8)
        top.pack(fill=tk.X)
        self.lbl_status = ttk.Label(top, text=self.t["connecting"], font=("Consolas", 10, "bold"))
        self.lbl_status.pack(side=tk.LEFT)
        self.lbl_sup = ttk.Label(top, text="", font=("Consolas", 9))
        self.lbl_sup.pack(side=tk.RIGHT)

        # 表格
        frame = ttk.Frame(root, padding=(8, 0, 8, 8))
        frame.pack(fill=tk.BOTH, expand=True)

        cols = ("status", "title", "duration", "msgs")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=15)
        self.tree.heading("status", text=self.t["status"])
        self.tree.heading("title", text=self.t["window"])
        self.tree.heading("duration", text=self.t["duration"])
        self.tree.heading("msgs", text=self.t["messages"])
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
        self.chk_pause = ttk.Checkbutton(bottom, text=self.t["pause_refresh"], variable=self.var_pause)
        self.chk_pause.pack(side=tk.LEFT)

        # Auto-reply toggle
        self.var_autoreply = tk.BooleanVar(value=True)
        self.btn_autoreply = ttk.Checkbutton(bottom, text=self.t["auto_reply"], variable=self.var_autoreply, command=self._toggle_autoreply)
        self.btn_autoreply.pack(side=tk.LEFT, padx=(15, 0))
        self.btn_prompt = ttk.Button(bottom, text=self.t["prompt_button"], command=self._edit_prompt)
        self.btn_prompt.pack(side=tk.LEFT, padx=(8, 0))

        self.lbl_refresh = ttk.Label(bottom, text=self.t["refresh_every"], font=("Consolas", 8), foreground="#999")
        self.lbl_refresh.pack(side=tk.RIGHT)
        self.lang_combo = ttk.Combobox(bottom, values=list(LANG_OPTIONS.keys()), width=8, state="readonly")
        self.lang_combo.set(LANG_LABELS.get(self.lang, "English"))
        self.lang_combo.pack(side=tk.RIGHT, padx=(8, 0))
        self.lang_combo.bind("<<ComboboxSelected>>", self._change_language)
        self.lbl_language = ttk.Label(bottom, text=self.t["language"] + ":")
        self.lbl_language.pack(side=tk.RIGHT)

        # 记录每个页面进入当前状态的时间(用于算累积时长)
        # key: page_id, value: (状态名, 进入时刻)
        self._state_since = {}  # pid -> (state, timestamp)

        self._refresh()

    def _change_language(self, event=None):
        selected = self.lang_combo.get()
        self.lang = LANG_OPTIONS.get(selected, "en")
        self.t = TEXT[self.lang]
        save_monitor_config(self.lang)
        self._apply_language()
        self._update()

    def _apply_language(self):
        self.tree.heading("status", text=self.t["status"])
        self.tree.heading("title", text=self.t["window"])
        self.tree.heading("duration", text=self.t["duration"])
        self.tree.heading("msgs", text=self.t["messages"])
        self.chk_pause.configure(text=self.t["pause_refresh"])
        self.btn_autoreply.configure(text=self.t["auto_reply"])
        self.btn_prompt.configure(text=self.t["prompt_button"])
        self.lbl_refresh.configure(text=self.t["refresh_every"])
        self.lbl_language.configure(text=self.t["language"] + ":")
    def _load_prompt_config(self):
        cfg = fetch("/supervisor_config")
        if not cfg or not cfg.get("ok"):
            messagebox.showerror(self.t["prompt_title"], self.t["prompt_error_unavailable"])
            return None
        return cfg

    def _save_prompt_config(self, win, text_widget, banned_entry):
        prompt = text_widget.get("1.0", tk.END).strip()
        banned_words = [x.strip() for x in banned_entry.get().split(",") if x.strip()]
        result = post_json("/supervisor_config", {"prompt": prompt, "banned_words": banned_words})
        if result and result.get("ok"):
            messagebox.showinfo(self.t["prompt_title"], self.t["prompt_saved"])
            win.destroy()
        else:
            messagebox.showerror(self.t["prompt_title"], self.t["save_failed"].format(error=(result or {}).get('error', 'unknown error')))

    def _reset_prompt_config(self, text_widget, banned_entry):
        cfg = fetch("/supervisor_config/reset")
        if not cfg or not cfg.get("ok"):
            messagebox.showerror(self.t["prompt_title"], self.t["reset_failed"])
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
        win.title(self.t["prompt_title"])
        win.geometry("820x640")
        win.minsize(620, 500)
        win.transient(self.root)
        win.grab_set()

        header = ttk.Frame(win, padding=(10, 10, 10, 4))
        header.pack(fill=tk.X)
        ttk.Label(header, text=self.t["prompt_heading"], font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text=self.t["advanced"], foreground="#666").pack(side=tk.RIGHT)

        help_frame = ttk.LabelFrame(win, text=self.t["editing_guide"], padding=(10, 6, 10, 8))
        help_frame.pack(fill=tk.X, padx=10, pady=(0, 8))
        help_text = self.t["help_text"]
        ttk.Label(help_frame, text=help_text, justify=tk.LEFT, wraplength=720).pack(anchor=tk.W)

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
        ttk.Label(banned_frame, text=self.t["banned_words"]).pack(side=tk.LEFT)
        banned_entry = ttk.Entry(banned_frame)
        banned_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        banned_entry.insert(0, ", ".join(cfg.get("banned_words", [])))

        buttons = ttk.Frame(win, padding=(10, 0, 10, 10))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text=self.t["save"], command=lambda: self._save_prompt_config(win, text_widget, banned_entry)).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=self.t["cancel"], command=win.destroy).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(buttons, text=self.t["reset_default"], command=lambda: self._reset_prompt_config(text_widget, banned_entry)).pack(side=tk.LEFT)

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
            self.lbl_status.config(text=self.t["backend_disconnected"], foreground="#cc3333")
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
            title = (p.get("title") or "").strip()[:25] or self.t["untitled"]
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
            label = self.t["offline"] if state_name=="dead" else (self.t["generating"] if state_name=="gen" else self.t["idle"])
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
            text=self.t["summary"].format(alive=alive_n, gen=gen_n, idle=idle_n, dead=dead_n),
            foreground="#333333",
        )
        self.lbl_sup.config(
            text=self.t["auto_on"] if sup_on else self.t["auto_off"],
            foreground="#2a8a2a" if sup_on else "#cc3333",
        )


def main():
    monitor_lock = acquire_monitor_lock()
    root = tk.Tk()
    app = MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
