#!/usr/bin/env python3
"""ChatGPT WebUI Bridge 一键启动入口。

用法:
    python run.py                      # 启动后端 API 服务
    python run.py --with-supervisor    # 同时启动 Claude 监督器
    python run.py --config other.yaml  # 指定配置文件
    python run.py --port 8080          # 覆盖配置里的端口
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

# 确保能 import backend 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_config(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        print("[!] 需要 PyYAML: pip install pyyaml")
        return {}
    if not os.path.exists(path):
        print(f"[!] 配置文件不存在: {path}, 使用默认配置")
        return {"server": {"host": "127.0.0.1", "port": 5000}, "supervisor": {"enabled": False}}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    ap = argparse.ArgumentParser(description="ChatGPT WebUI Bridge")
    ap.add_argument("--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    ap.add_argument("--with-supervisor", action="store_true", help="启动时同时开 Claude 监督器")
    ap.add_argument("--port", type=int, default=None, help="覆盖配置里的端口")
    ap.add_argument("--host", default=None, help="覆盖配置里的 host")
    args = ap.parse_args()

    config = load_config(args.config)

    host = args.host or config.get("server", {}).get("host", "127.0.0.1")
    port = args.port or config.get("server", {}).get("port", 5000)

    # 是否启动监督器:命令行 --with-supervisor 或 config 里 enabled=true
    sup_config = config.get("supervisor", {})
    start_sup = args.with_supervisor or sup_config.get("enabled", False)

    print("=" * 50)
    print("  ChatGPT WebUI Bridge")
    print(f"  地址: http://{host}:{port}")
    print(f"  API 文档: http://{host}:{port}/docs")
    print(f"  监督器: {'✓ 启动' if start_sup else '✗ 不启动(用 /supervisor/start 手动开)'}")
    print("=" * 50)
    print("\n等待油猴脚本连接...")
    print("(在 ChatGPT 页面安装 userscript/chatgpt_bridge.user.js 并刷新)\n")

    # 启动监督器(如果需要)
    if start_sup:
        from backend.supervisor import Supervisor, log
        stop_event = threading.Event()
        sup = Supervisor(config=sup_config, stop_event=stop_event)
        t = threading.Thread(target=sup.run, daemon=True)
        t.start()
        # 把 stop_event 挂到全局,server.py 的 /supervisor/stop 能控制
        import backend.server as srv
        srv._supervisor_stop_event = stop_event
        srv._supervisor_thread = t

    # 启动 FastAPI(用 uvicorn)
    import uvicorn
    uvicorn.run(
        "backend.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
