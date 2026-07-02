"""ChatGPT WebUI Bridge — FastAPI 后端服务。

提供 HTTP API,让 agent 程序控制多个已登录的 ChatGPT 窗口。
油猴脚本(注入 ChatGPT 页面)与本服务通信,桥接 agent 和 ChatGPT。

启动后访问 /docs 查看自动生成的 API 文档(OpenAPI)。
"""
from __future__ import annotations

import sys
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .bridge_state import state

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 监督器懒加载(可选功能)
_supervisor_thread = None
_supervisor_stop_event = threading.Event()


# ====== 请求模型 ======
class RegisterReq(BaseModel):
    page_id: str | None = None
    page: str = "chatgpt"
    url: str = ""
    title: str = ""


class PollReq(BaseModel):
    page_id: str
    snapshot: dict[str, Any] | None = None


class ResultReq(BaseModel):
    id: str
    result: dict[str, Any] = {}


class SendReq(BaseModel):
    text: str
    page_id: str | None = None  # 不指定则自动选空闲页面


class NewChatReq(BaseModel):
    page_id: str | None = None


# ====== FastAPI 应用 ======
app = FastAPI(
    title="ChatGPT WebUI Bridge",
    description="通过油猴脚本桥接,让 agent 控制 ChatGPT 网页版窗口。"
                "装好油猴脚本后,agent 通过本 API 发消息、读回复、监控页面状态。",
    version="1.0.0",
)


# ---- 油猴脚本接口 ----
@app.post("/register", summary="油猴脚本注册页面")
def register(req: RegisterReq):
    """油猴脚本加载时调用,注册当前页面。返回分配的 page_id。"""
    pid = state.register(req.page_id, req.dict())
    return {"ok": True, "page_id": pid}


@app.post("/poll", summary="油猴脚本轮询:回传快照 + 取命令")
def poll(req: PollReq):
    """油猴脚本每隔几百毫秒调用。回传页面快照,取走待执行命令(若有)。"""
    cmd = state.poll(req.page_id, req.snapshot)
    return cmd


@app.post("/result", summary="油猴脚本回传命令执行结果")
def submit_result(req: ResultReq):
    """油猴脚本执行完命令(send/new_chat)后回传结果。"""
    state.submit_result(req.id, req.result)
    return {"ok": True}


# ---- agent 接口 ----
@app.get("/status", summary="服务状态")
def get_status():
    """返回连接的页面数、活跃页面数。"""
    pages = state.list_pages()
    alive = sum(1 for p in pages if p["alive"])
    return {
        "pages_connected": len(pages),
        "pages_alive": alive,
        "supervisor_running": _supervisor_thread is not None and _supervisor_thread.is_alive(),
    }


@app.get("/pages", summary="列出所有 ChatGPT 窗口")
def list_pages():
    """返回所有连接的 ChatGPT 窗口摘要(标题、状态、消息数、最后回复)。"""
    return {"pages": state.list_pages(), "total": len(state.list_pages())}


@app.get("/snapshot", summary="获取页面快照")
def get_snapshot(page_id: str | None = None):
    """
    获取某个窗口的详细快照(最近对话、输入框内容、是否生成中)。
    不传 page_id 则返回最近活跃的页面。
    """
    snap = state.get_snapshot(page_id)
    return snap or {"error": "无可用快照(油猴脚本未连接或页面未打开)"}


@app.get("/all_snapshots", summary="获取所有窗口快照")
def get_all_snapshots():
    """一次性返回所有窗口的快照,看每个页面在聊什么。"""
    return {"pages": state.get_all_snapshots()}


@app.post("/send", summary="发送消息并等待回复")
def send_message(req: SendReq):
    """
    向指定窗口发消息,阻塞等待 ChatGPT 回复完成。
    - page_id 不指定 → 自动选一个空闲窗口
    - 返回 {"ok": true, "reply": "..."} 或 {"ok": false, "error": "..."}
    """
    try:
        cid = state.send_command(req.page_id, "send", req.text)
        result = state.wait_result(cid, timeout=180)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send_async", summary="发送消息(异步,不等回复)")
def send_async(req: SendReq):
    """
    异步发消息:只入队,不等 ChatGPT 回复。
    适合批量发送或监督器场景。返回 cmd_id,可用 /snapshot 查回复。
    """
    try:
        cid = state.send_command(req.page_id, "send", req.text)
        return {"ok": True, "cmd_id": cid}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/new_chat", summary="开新对话")
def new_chat(req: NewChatReq):
    """在指定窗口开新对话。不传 page_id 则自动选窗口。"""
    try:
        cid = state.send_command(req.page_id, "new_chat")
        result = state.wait_result(cid, timeout=30)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/idle", summary="找一个空闲窗口")
def find_idle():
    """返回一个空闲(活着且未生成中)的 page_id。没有则返回 null。"""
    pid = state.find_idle_page()
    return {"page_id": pid}


# ---- 监督器控制 ----
@app.post("/supervisor/{action}", summary="启动/停止监督器")
def control_supervisor(action: str):
    """
    启动或停止 Claude 监督器。
    - /supervisor/start  启动(需在 config.yaml 里配置 supervisor)
    - /supervisor/stop   停止
    """
    global _supervisor_thread, _supervisor_stop_event
    from .supervisor import Supervisor

    if action == "start":
        if _supervisor_thread and _supervisor_thread.is_alive():
            return {"ok": True, "msg": "监督器已在运行"}
        _supervisor_stop_event = threading.Event()
        sup = Supervisor(stop_event=_supervisor_stop_event)
        _supervisor_thread = threading.Thread(target=sup.run, daemon=True)
        _supervisor_thread.start()
        return {"ok": True, "msg": "监督器已启动"}
    elif action == "stop":
        _supervisor_stop_event.set()
        return {"ok": True, "msg": "监督器已请求停止"}
    else:
        raise HTTPException(status_code=400, detail=f"未知操作: {action}")


@app.get("/", summary="健康检查")
def root():
    return {"service": "ChatGPT WebUI Bridge", "version": "1.0.0", "docs": "/docs"}
