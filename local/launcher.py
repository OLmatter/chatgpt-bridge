#!/usr/bin/env python3
"""一键启动器:自检杀旧进程 → 启动后端 → 启动监控 → 验证全部正常。

用法: python launcher.py
"""
import os, sys, time, subprocess, socket

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 5000


def kill_pid(pid):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def find_port_pid(port):
    """找占用指定端口的 PID。"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                return parts[-1]
    except Exception:
        pass
    return None


def find_monitor_pids():
    """找所有跑 monitor.py 的 python 进程 PID(python.exe 和 python3.12.exe 都查)。"""
    pids = []
    for proc_name in ["python3.12.exe", "python.exe"]:
        try:
            r = subprocess.run(
                ["wmic", "process", "where", f"name='{proc_name}'", "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if "monitor.py" in line:
                    for tok in line.split():
                        if tok.isdigit():
                            pids.append(tok)
        except Exception:
            pass
    return pids


def is_port_up(port):
    """检查端口是否在监听。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False


def main():
    print("=" * 50)
    print("  ChatGPT Bridge 一键启动器")
    print("=" * 50)

    # === Step 1: 杀旧后端(占用5000端口的) ===
    print("\n[1/5] 自检:杀旧后端进程...")
    old_pid = find_port_pid(PORT)
    if old_pid:
        print(f"  发现端口 {PORT} 被 PID {old_pid} 占用,杀掉")
        kill_pid(old_pid)
        time.sleep(2)
    else:
        print(f"  端口 {PORT} 空闲")

    # === Step 2: 杀旧监控 ===
    print("\n[2/5] 自检:杀旧监控进程...")
    mon_pids = find_monitor_pids()
    for pid in mon_pids:
        print(f"  杀旧监控 PID {pid}")
        kill_pid(pid)
    if not mon_pids:
        print("  无旧监控")
    time.sleep(1)

    # === Step 3: 确认端口释放 ===
    print("\n[3/5] 确认端口释放...")
    for i in range(5):
        if not find_port_pid(PORT):
            print(f"  端口 {PORT} 已释放")
            break
        print(f"  端口仍被占用,等待...({i+1}/5)")
        time.sleep(2)
    else:
        print(f"  ⚠️ 端口 {PORT} 释放失败,强制继续")

    # === Step 4: 启动后端 ===
    print("\n[4/5] 启动后端...")
    backend = subprocess.Popen(
        [sys.executable, "run_all.py"],
        cwd=DIR,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    # 等后端起来
    ok = False
    for i in range(10):
        time.sleep(1)
        if is_port_up(PORT):
            ok = True
            break
    if ok:
        print(f"  ✓ 后端启动成功(PID {backend.pid}),端口 {PORT} 在监听")
    else:
        print(f"  ✗ 后端启动失败")
        return

    # === Step 5: 启动监控 ===
    print("\n[5/5] 启动监控 GUI...")
    monitor = subprocess.Popen(
        [sys.executable, "monitor.py"],
        cwd=DIR,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    time.sleep(2)
    if monitor.poll() is None:
        print(f"  ✓ 监控 GUI 启动成功(PID {monitor.pid})")
    else:
        print(f"  ✗ 监控 GUI 启动失败")

    # === 最终自检 ===
    print("\n" + "=" * 50)
    print("  自检结果:")
    print(f"    后端(5000): {'✓ 运行中' if is_port_up(PORT) else '✗ 未运行'}")
    print(f"    监控 GUI:   {'✓ 运行中' if monitor.poll() is None else '✗ 未运行'}")
    print("=" * 50)
    print("\n启动完成。这个窗口可以关了。")


if __name__ == "__main__":
    main()
