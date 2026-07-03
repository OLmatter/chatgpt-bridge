# ChatGPT WebUI Bridge

让 agent 程序通过 API 控制多个已登录的 ChatGPT 网页窗口:发消息、读回复、监控页面状态、自动监督。

## 工作原理

```
你的 Agent 程序  ←HTTP API→  本地后端服务  ←HTTP轮询→  油猴脚本(注入ChatGPT页面)
                                                  ↕
                                              ChatGPT 网页
```

- **油猴脚本**注入到你已登录的 ChatGPT 页面,定时回传页面快照(对话内容、生成状态),并接收执行命令
- **后端服务**(FastAPI)管理所有窗口状态,提供 HTTP API 给 agent 调用
- **监督器**(可选)自动监控空闲窗口,调用 Claude CLI 发鼓励消息让 ChatGPT 继续

## 快速开始

### 1. 安装后端

```bash
cd chatgpt-bridge
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python run.py
# 带 Claude 监督器启动:
python run.py --with-supervisor
```

启动后访问 `http://127.0.0.1:5000/docs` 查看完整 API 文档。

### 3. 安装油猴脚本

1. Chrome 装 [Tampermonkey](https://www.tampermonkey.net/) 扩展

   > ⚠️ **必须开启 Chrome 扩展的「开发者模式」**:地址栏输入 `chrome://extensions/` → 右上角打开 **开发者模式** 开关。Tampermonkey 在开发者模式关闭时会被 Chrome 限制,脚本无法注入或权限被拦。

2. 打开 Tampermonkey 管理面板(`chrome-extension://dhdgffkkebhmkfjojejmpbldmpobfkfo/options.html`),点 **"+"** 新建脚本

3. 把默认内容全删,粘贴 `userscript/chatgpt_bridge.user.js` 的全部内容,Ctrl+S 保存

4. **给脚本开全部权限**(关键!不开权限会连不上):
   - 回到 Tampermonkey 管理面板(已安装脚本列表)
   - 找到 "ChatGPT WebUI Bridge",点右边的 **编辑** 铅笔图标
   - 进入 **设置** 标签页
   - 把以下权限全部改为 **允许/Always**:
     - `GM_xmlhttpRequest` → 允许(核心!不发权限就连不上后端)
     - 跨域请求 → 允许
   - 底部 **保存**

   > 如果 Tampermonkey 弹出权限确认框(首次发请求时),必须点 **允许**,否则 `GM_xmlhttpRequest` 无法访问 `http://127.0.0.1:5000`。

5. 打开/刷新 ChatGPT 页面(`https://chatgpt.com/`)

6. 页面右上角出现绿色 **"Bridge: 就绪"** 标签 = 成功

> 可开多个 ChatGPT 标签页,每个都会自动连接。

> 如果标签显示 **"等待服务..."**(红色):后端没启动,先跑 `python run.py`。
> 如果显示 **"连接错误"**:检查 Tampermonkey 权限是否开了 + 后端是否在跑。

### 4. 你的 agent 接入

```python
from examples.agent_client import ChatGPTBridge

bridge = ChatGPTBridge("http://127.0.0.1:5000")

# 看有哪些窗口
for p in bridge.list_pages():
    print(p["page_id"], p["title"], "生成中" if p["is_generating"] else "空闲")

# 找空闲窗口,发消息
reply = bridge.send("帮我总结这段对话")
print(reply)

# 看页面快照(完整对话)
snap = bridge.snapshot()
for t in snap["recentTurns"]:
    print(f"[{t['role']}] {t['text']}")
```

## API 文档

启动后访问 `/docs` 有交互式文档。主要接口:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/status` | 服务状态(连接了多少窗口) |
| GET | `/pages` | 列出所有窗口(标题、状态、消息数) |
| GET | `/snapshot?page_id=` | 某窗口的快照(最近对话、生成状态) |
| GET | `/all_snapshots` | 所有窗口快照 |
| POST | `/send` | 发消息并等回复 `{"text":"...","page_id":"..."}` |
| POST | `/send_async` | 异步发(不等回复) |
| POST | `/new_chat` | 开新对话 |
| GET | `/idle` | 找一个空闲窗口 |
| POST | `/supervisor/start` | 启动 Claude 监督器 |
| POST | `/supervisor/stop` | 停止监督器 |

## 配置

编辑 `config.yaml`:

- **server.port** — 后端端口(油猴脚本里的 `BACKEND_URL` 要同步改)
- **supervisor.enabled** — 是否启动时自动开监督器
- **supervisor.prompt** — Claude 的提示词(改成你的场景:学习/工作/比赛)
- **supervisor.banned_words** — 禁用词(Claude 回复里包含会被自动删除)

`examples/neurogolf_config.yaml` 是 Kaggle NeuroGolf 比赛的专用配置示例。

## 监督器(可选)

监督器自动扫描空闲的 ChatGPT 窗口,调 Claude CLI 决定该说什么鼓励话,然后发过去让 ChatGPT 继续。适合让多个窗口持续干活。

需要先装 Claude CLI:
```bash
npm install -g @anthropic-ai/claude-code
```

然后在 `config.yaml` 里设 `supervisor.enabled: true`,或启动时加 `--with-supervisor`。

## 项目结构

```
chatgpt-bridge/
├── run.py                       # 一键启动 (FastAPI 版)
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
│   └── neurogolf_config.yaml    # NeuroGolf 比赛配置示例
└── local/                       # 单体版(不想装 FastAPI 时用)
    ├── run_all.py               # 桥接+监督器一体(标准库,零依赖)
    ├── monitor.py               # tkinter 监控 GUI(窗口状态+自动回复开关)
    └── restart.bat              # 一键重启(杀掉旧进程避免端口冲突)
```

### FastAPI 版 vs 单体版

| | FastAPI 版(`run.py`) | 单体版(`local/run_all.py`) |
|---|---|---|
| 依赖 | fastapi, uvicorn, pyyaml | 零依赖(Python 标准库) |
| API 文档 | `/docs` 交互式 | 无 |
| 监控 GUI | 无 | `local/monitor.py` |
| 适用场景 | 给 agent 正式集成 | 自己快速用 |

## 注意事项

- 油猴脚本用 `GM_xmlhttpRequest` 绕过 CSP,需要 Tampermonkey(不是 Greasemonkey)
- 后台标签页可能被浏览器节流,脚本有自动恢复机制(切回来会重连)
- `page_id` 基于对话 URL,每个对话唯一;同一对话开多个标签页也能区分
- 监督器的 Claude CLI 调用是同步的,但多个窗口并发处理
