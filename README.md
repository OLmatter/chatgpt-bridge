# ChatGPT WebUI Bridge

[English](#english) | [中文](#中文)

---
## English

ChatGPT WebUI Bridge lets a local agent control multiple logged-in ChatGPT browser tabs through an HTTP API: send prompts, read replies, monitor page state, and optionally auto-nudge idle tabs.

### How It Works

```text
Your agent  ←HTTP API→  Local backend service  ←HTTP polling→  Userscript injected into ChatGPT
                                                              ↕
                                                          ChatGPT Web UI
```

- The **userscript** runs inside logged-in ChatGPT pages, sends snapshots to the backend, and receives commands.
- The **backend service** manages page state and exposes HTTP APIs to agents.
- The optional **supervisor** scans idle tabs and calls Claude CLI to produce short continuation messages.

### Quick Start

#### 1. Install backend dependencies

```bash
cd chatgpt-bridge
pip install -r requirements.txt
```

#### 2. Start the service

```bash
python run.py
# Start with the Claude supervisor
python run.py --with-supervisor
```

Open `http://127.0.0.1:5000/docs` for the generated API docs.

#### 3. Install the userscript

1. Install the Tampermonkey extension in Chrome.
2. Open `chrome://extensions/` and enable **Developer mode**.
3. Create a new Tampermonkey script and paste the full contents of `userscript/chatgpt_bridge.user.js`.
4. Allow `GM_xmlhttpRequest` and cross-origin requests in the script settings.
5. Open or refresh `https://chatgpt.com/`.
6. A green `Bridge: ready` badge means the page is connected.

If the badge says `waiting for service`, start the backend first. If it says `connection error`, check Tampermonkey permissions and the backend port.

### Agent Example

```python
from examples.agent_client import ChatGPTBridge

bridge = ChatGPTBridge("http://127.0.0.1:5000")

for p in bridge.list_pages():
    print(p["page_id"], p["title"], "generating" if p["is_generating"] else "idle")

reply = bridge.send("Summarize this conversation")
print(reply)

snap = bridge.snapshot()
for t in snap["recentTurns"]:
    print(f"[{t['role']}] {t['text']}")
```

### Main APIs

| Method | Path | Description |
|---|---|---|
| GET | `/status` | Service status |
| GET | `/pages` | List connected tabs |
| GET | `/snapshot?page_id=` | Get one page snapshot |
| GET | `/all_snapshots` | Get all snapshots |
| POST | `/send` | Send a message and wait for reply |
| POST | `/send_async` | Send a message asynchronously |
| POST | `/new_chat` | Start a new chat |
| GET | `/idle` | Find an idle page |
| POST | `/supervisor/start` | Start the Claude supervisor |
| POST | `/supervisor/stop` | Stop the Claude supervisor |

### Maintained Entrypoints

Prefer the FastAPI version:

```bash
python run.py
```

Use the single-process local version when you do not want FastAPI dependencies:

```bash
cd local
python run_all.py
```

The local `run_all.py` now tracks per-page `IN_FLIGHT` state so the supervisor does not enqueue repeated auto-replies for the same idle tab while Claude is still deciding.

### Configuration

Edit `config.yaml`:

- `server.port`: backend port; keep it aligned with `BACKEND_URL` in the userscript.
- `supervisor.enabled`: whether the supervisor starts automatically.
- `supervisor.prompt`: prompt template for Claude supervisor decisions.
- `supervisor.banned_words`: words removed from supervisor replies.
- Local GUI prompt editor: run `cd local && python launcher.py`, click `Prompt...`, edit the prompt text box, and save. The local override is stored in `local/supervisor_config.json`; use `{convo}` where recent turns should be inserted. Changes apply to the next auto-reply.

### Project Layout

```text
chatgpt-bridge/
├── run.py                       # FastAPI entrypoint
├── config.yaml                  # Configuration
├── requirements.txt
├── backend/
│   ├── server.py                # FastAPI backend
│   ├── bridge_state.py          # Page state manager
│   └── supervisor.py            # Claude supervisor
├── userscript/
│   └── chatgpt_bridge.user.js   # Tampermonkey userscript
├── examples/
│   ├── agent_client.py          # Python client wrapper
│   └── neurogolf_config.yaml    # NeuroGolf sample config
└── local/                       # Single-process version
    ├── run_all.py               # Bridge + supervisor in one process
    ├── monitor.py               # tkinter monitor GUI
    └── restart.bat              # Windows restart helper
```

### Notes

- The userscript relies on `GM_xmlhttpRequest`; Tampermonkey is recommended.
- Background tabs may be throttled by the browser; switching back to a tab lets it reconnect.
- `page_id` is based on the conversation URL, so duplicate tabs of the same conversation can still be distinguished.
- Auto-supervision is intended for short continuation nudges; complex strategies should live in the external agent.

---

## 中文

让本地 agent 程序通过 HTTP API 控制多个已登录的 ChatGPT 网页窗口：发消息、读回复、监控页面状态，并可选自动监督空闲窗口。

### 工作原理

```text
你的 Agent 程序  ←HTTP API→  本地后端服务  ←HTTP 轮询→  油猴脚本(注入 ChatGPT 页面)
                                                  ↕
                                              ChatGPT 网页
```

- **油猴脚本** 注入已登录的 ChatGPT 页面，定时回传页面快照，并接收执行命令。
- **后端服务** 管理所有窗口状态，提供 HTTP API 给 agent 调用。
- **监督器** 可选启用，自动扫描空闲窗口，调用 Claude CLI 生成短回复，让 ChatGPT 继续。

### 快速开始

#### 1. 安装后端

```bash
cd chatgpt-bridge
pip install -r requirements.txt
```

#### 2. 启动服务

```bash
python run.py
# 同时启动 Claude 监督器
python run.py --with-supervisor
```

启动后访问 `http://127.0.0.1:5000/docs` 查看完整 API 文档。

#### 3. 安装油猴脚本

1. Chrome 安装 Tampermonkey 扩展。
2. 打开 `chrome://extensions/`，开启右上角 **开发者模式**。
3. 在 Tampermonkey 中新建脚本，粘贴 `userscript/chatgpt_bridge.user.js` 全部内容并保存。
4. 在脚本设置里允许 `GM_xmlhttpRequest` 和跨域请求。
5. 打开或刷新 `https://chatgpt.com/`。
6. 页面右上角出现绿色 `Bridge: 就绪` 标签即表示连接成功。

如果标签显示 `等待服务...`，先启动后端；如果显示 `连接错误`，检查 Tampermonkey 权限和后端端口。

### Agent 接入示例

```python
from examples.agent_client import ChatGPTBridge

bridge = ChatGPTBridge("http://127.0.0.1:5000")

for p in bridge.list_pages():
    print(p["page_id"], p["title"], "生成中" if p["is_generating"] else "空闲")

reply = bridge.send("帮我总结这段对话")
print(reply)

snap = bridge.snapshot()
for t in snap["recentTurns"]:
    print(f"[{t['role']}] {t['text']}")
```

### 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/status` | 服务状态 |
| GET | `/pages` | 列出所有窗口 |
| GET | `/snapshot?page_id=` | 获取某窗口快照 |
| GET | `/all_snapshots` | 获取所有窗口快照 |
| POST | `/send` | 发消息并等待回复 |
| POST | `/send_async` | 异步发消息 |
| POST | `/new_chat` | 开新对话 |
| GET | `/idle` | 找一个空闲窗口 |
| POST | `/supervisor/start` | 启动 Claude 监督器 |
| POST | `/supervisor/stop` | 停止 Claude 监督器 |

### 当前维护入口

推荐优先使用 FastAPI 版：

```bash
python run.py
```

如果不想安装 FastAPI 依赖，可以用单体版：

```bash
cd local
python run_all.py
```

单体版 `local/run_all.py` 已加入每页 `IN_FLIGHT` 锁，避免 Claude 决策未返回时对同一空闲页面重复自动回复。

### 配置

编辑 `config.yaml`：

- `server.port`：后端端口，需和油猴脚本里的 `BACKEND_URL` 一致。
- `supervisor.enabled`：是否启动时自动开启监督器。
- `supervisor.prompt`：Claude 监督器提示词。
- `supervisor.banned_words`：自动删除的禁用词。
- 本地 GUI 提示词编辑器：运行 `cd local && python launcher.py`，点击 `Prompt...`，在文本框里编辑并保存。覆盖配置会保存到 `local/supervisor_config.json`；用 `{convo}` 表示插入最近对话的位置。修改会从下一次自动回复开始生效。

### 项目结构

```text
chatgpt-bridge/
├── run.py                       # FastAPI 启动入口
├── config.yaml                  # 配置
├── requirements.txt
├── backend/
│   ├── server.py                # FastAPI 后端
│   ├── bridge_state.py          # 页面状态管理
│   └── supervisor.py            # Claude 监督器
├── userscript/
│   └── chatgpt_bridge.user.js   # 油猴脚本
├── examples/
│   ├── agent_client.py          # Python 客户端封装
│   └── neurogolf_config.yaml    # NeuroGolf 示例配置
└── local/                       # 单体版
    ├── run_all.py               # 桥接 + 监督器一体
    ├── monitor.py               # tkinter 监控 GUI
    └── restart.bat              # Windows 一键重启
```

### 注意事项

- 油猴脚本依赖 `GM_xmlhttpRequest`，建议使用 Tampermonkey。
- 后台标签页可能被浏览器节流，切回页面会自动恢复连接。
- `page_id` 基于对话 URL，同一对话多个标签页也能区分。
- 自动监督只适合短促继续型回复；复杂策略应由外部 agent 控制。

---
