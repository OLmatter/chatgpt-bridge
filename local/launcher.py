#!/usr/bin/env python3
"""One-click launcher: clean old processes, start backend, start monitor, then verify health.

Usage: python launcher.py
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


def find_port_pids(port):
    """找所有占用指定端口的 PID(可能有多个残留进程)。"""
    pids = []
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit() and pid not in pids:
                    pids.append(pid)
    except Exception:
        pass
    return pids


def find_process_pids_by_script(script_name):
    """找所有跑指定脚本的 python 进程 PID。用 PowerShell 精确匹配命令行。排除 launcher 自己。"""
    pids = []
    my_pid = str(os.getpid())
    try:
        cmd = f'Get-CimInstance Win32_Process | Where-Object {{$_.CommandLine -like "*{script_name}*" -and $_.Name -like "*python*" -and $_.ProcessId -ne {my_pid} -and $_.CommandLine -notlike "*launcher*"}} | Select-Object -ExpandProperty ProcessId'
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, timeout=10)
        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        if stdout:
            for line in stdout.split("\n"):
                line = line.strip()
                if line.isdigit() and int(line) > 1000:
                    pids.append(line)
    except Exception:
        pass
    return list(set(pids))


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
    print("  ChatGPT Bridge Launcher")
    print("=" * 50)

    # === Step 1: 杀旧后端(占端口的所有PID + 所有跑run_all.py的残留) ===
    print("\n[1/5] Preflight: stopping old backend processes...")
    port_pids = find_port_pids(PORT)
    run_pids = find_process_pids_by_script("run_all.py")
    all_backend_pids = list(set(port_pids + run_pids))
    if all_backend_pids:
        for pid in all_backend_pids:
            print(f"  Stopping backend PID {pid}")
            kill_pid(pid)
        time.sleep(3)
    else:
        print(f"  No old backend found")

    # === Step 2: 杀旧监控 ===
    print("\n[2/5] Preflight: stopping old monitor processes...")
    mon_pids = find_process_pids_by_script("monitor.py")
    for pid in mon_pids:
        print(f"  Stopping old monitor PID {pid}")
        kill_pid(pid)
    if not mon_pids:
        print("  No old monitor found")
    time.sleep(1)

    # === Step 3: 确认端口释放 ===
    print("\n[3/5] Checking port release...")
    for i in range(5):
        if not find_port_pids(PORT):
            print(f"  Port {PORT} is free")
            break
        print(f"  Port still busy, waiting...({i+1}/5)")
        time.sleep(2)
    else:
        print(f"  WARNING: port {PORT} is still busy; continuing anyway")

    # === Step 4: 启动后端 ===
    print("\n[4/5] Starting backend...")
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
        print(f"  OK: backend started (PID {backend.pid}); port {PORT} is listening")
    else:
        print(f"  ERROR: backend failed to start")
        return

    # === Step 5: 启动监控 ===
    print("\n[5/5] Starting monitor GUI...")
    monitor = subprocess.Popen(
        [sys.executable, "monitor.py"],
        cwd=DIR,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    time.sleep(2)
    if monitor.poll() is None:
        print(f"  OK: monitor GUI started (PID {monitor.pid})")
    else:
        print(f"  ERROR: monitor GUI failed to start")

    # === 最终自检 ===
    print("\n" + "=" * 50)
    print("  Health check:")

    # 后端
    backend_ok = is_port_up(PORT)
    print(f"    Backend(5000): {'OK running' if backend_ok else 'ERROR not running'}")

    # 监控
    monitor_ok = monitor.poll() is None
    print(f"    Monitor GUI:   {'OK running' if monitor_ok else 'ERROR not running'}")

    # 端口只有一个进程
    port_count = len(find_port_pids(PORT))
    print(f"    Port process count: {port_count} {'OK' if port_count == 1 else 'ERROR duplicate processes!'}")

    # API 响应 + 监督器状态
    if backend_ok:
        try:
            import urllib.request, json
            r = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=3).read())
            print(f"    Connected pages:   {r.get('pages_connected', 0)}")
            print(f"    Supervisor:     {'OK on' if r.get('supervisor_on') else 'OFF'}")
        except Exception as e:
            print(f"    API check:   ERROR {e}")

    # 残留进程检查
    leftover_mon = find_process_pids_by_script("monitor.py")
    leftover_mon = [p for p in leftover_mon if str(p) != str(monitor.pid)]
    if leftover_mon:
        print(f"    WARNING: leftover monitors: {leftover_mon}")
    else:
        print(f"    Leftover monitors:   none")

    print("=" * 50)
    if backend_ok and monitor_ok and port_count == 1:
        print("\nAll checks passed. You can close this window.")
    else:
        print("\nSome checks failed. Review the error lines above.")


if __name__ == "__main__":
    main()

