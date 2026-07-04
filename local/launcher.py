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
    print("  ChatGPT Bridge 一键启动器")
    print("=" * 50)

    # === Step 1: 杀旧后端(占端口的所有PID + 所有跑run_all.py的残留) ===
    print("\n[1/5] 自检:杀旧后端进程...")
    port_pids = find_port_pids(PORT)
    run_pids = find_process_pids_by_script("run_all.py")
    all_backend_pids = list(set(port_pids + run_pids))
    if all_backend_pids:
        for pid in all_backend_pids:
            print(f"  杀后端 PID {pid}")
            kill_pid(pid)
        time.sleep(3)
    else:
        print(f"  无旧后端")

    # === Step 2: 杀旧监控 ===
    print("\n[2/5] 自检:杀旧监控进程...")
    mon_pids = find_process_pids_by_script("monitor.py")
    for pid in mon_pids:
        print(f"  杀旧监控 PID {pid}")
        kill_pid(pid)
    if not mon_pids:
        print("  无旧监控")
    time.sleep(1)

    # === Step 3: 确认端口释放 ===
    print("\n[3/5] 确认端口释放...")
    for i in range(5):
        if not find_port_pids(PORT):
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

    # 后端
    backend_ok = is_port_up(PORT)
    print(f"    后端(5000): {'✓ 运行中' if backend_ok else '✗ 未运行'}")

    # 监控
    monitor_ok = monitor.poll() is None
    print(f"    监控 GUI:   {'✓ 运行中' if monitor_ok else '✗ 未运行'}")

    # 端口只有一个进程
    port_count = len(find_port_pids(PORT))
    print(f"    端口进程数: {port_count} {'✓' if port_count == 1 else '✗ 有重复!'}")

    # API 响应 + 监督器状态
    if backend_ok:
        try:
            import urllib.request, json
            r = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=3).read())
            print(f"    连接窗口:   {r.get('pages_connected', 0)}")
            print(f"    监督器:     {'✓ 开' if r.get('supervisor_on') else '✗ 关'}")
        except Exception as e:
            print(f"    API 检查:   ✗ {e}")

    # 残留进程检查
    leftover_mon = find_process_pids_by_script("monitor.py")
    leftover_mon = [p for p in leftover_mon if str(p) != str(monitor.pid)]
    if leftover_mon:
        print(f"    ⚠️ 残留监控: {leftover_mon}")
    else:
        print(f"    残留监控:   ✓ 无")

    print("=" * 50)
    if backend_ok and monitor_ok and port_count == 1:
        print("\n✅ 全部正常。这个窗口可以关了。")
    else:
        print("\n⚠️ 有异常,请检查上面的 ✗ 项。")


if __name__ == "__main__":
    main()
