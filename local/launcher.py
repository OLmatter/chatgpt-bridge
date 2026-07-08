#!/usr/bin/env python3
"""One-click launcher: clean old processes, start backend, start monitor, then verify health.

Usage: python launcher.py
"""
import os, sys, time, subprocess, socket, atexit

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 5000
LOCK_PATH = os.path.join(DIR, "launcher.lock")


def acquire_launcher_lock():
    """Prevent two launchers from starting/killing processes at the same time."""
    if sys.platform != "win32":
        return None
    try:
        import msvcrt
        lock_file = open(LOCK_PATH, "a+", encoding="utf-8")
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
        print("Another launcher is already running. Reuse the existing window instead of starting a second one.")
        sys.exit(2)


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


def api_ok(port):
    """Return True only when the bridge API is responding."""
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).read()
        return True
    except Exception:
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/supervisor_config", timeout=2).read()
            return True
        except Exception:
            return False


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
    launcher_lock = acquire_launcher_lock()
    print("=" * 50)
    print("  ChatGPT Bridge Launcher")
    print("=" * 50)

    # === Step 1: 后端单实例检查。健康的旧后端直接复用,不要反复杀/重启 ===
    print("\n[1/5] Backend singleton preflight...")
    port_pids = find_port_pids(PORT)
    run_pids = find_process_pids_by_script("run_all.py")
    backend = None
    reused_backend_pid = None

    if len(port_pids) == 1 and api_ok(PORT):
        reused_backend_pid = port_pids[0]
        print(f"  Reusing healthy backend PID {reused_backend_pid}")
        stale_run_pids = [pid for pid in run_pids if pid != reused_backend_pid]
        for pid in stale_run_pids:
            print(f"  Stopping stale backend PID {pid}")
            kill_pid(pid)
        time.sleep(1)
    else:
        all_backend_pids = list(set(port_pids + run_pids))
        if all_backend_pids:
            print("  Existing backend is missing, unhealthy, or duplicated; cleaning it first...")
            for proc_id in all_backend_pids:
                print(f"  Stopping backend PID {proc_id}")
                kill_pid(proc_id)
            time.sleep(3)
        else:
            print("  No old backend found")

    # === Step 2: 确认端口释放或健康复用 ===
    print("\n[2/5] Checking backend state...")
    if reused_backend_pid:
        print(f"  Port {PORT} is already owned by healthy backend PID {reused_backend_pid}")
    else:
        for i in range(5):
            if not find_port_pids(PORT):
                print(f"  Port {PORT} is free")
                break
            print(f"  Port still busy, waiting...({i+1}/5)")
            time.sleep(2)
        else:
            print(f"  ERROR: port {PORT} is still busy; not starting a second backend")
            return

    # === Step 3: 启动或复用后端 ===
    print("\n[3/5] Starting or reusing backend...")
    if not reused_backend_pid:
        backend = subprocess.Popen(
            [sys.executable, "run_all.py"],
            cwd=DIR,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        ok = False
        for i in range(10):
            time.sleep(1)
            if is_port_up(PORT) and api_ok(PORT):
                ok = True
                break
        if ok:
            print(f"  OK: backend started (PID {backend.pid}); port {PORT} is listening")
        else:
            print("  ERROR: backend failed to start")
            return
    else:
        print("  OK: backend reused; no second backend started")

    # === Step 4: 监控 GUI 单实例。已有窗口就复用,多余窗口只保留一个 ===
    print("\n[4/5] Monitor GUI singleton preflight...")
    mon_pids = find_process_pids_by_script("monitor.py")
    monitor = None
    reused_monitor_pid = mon_pids[0] if mon_pids else None
    if reused_monitor_pid:
        print(f"  Reusing monitor GUI PID {reused_monitor_pid}")
        for proc_id in mon_pids[1:]:
            print(f"  Stopping duplicate monitor PID {proc_id}")
            kill_pid(proc_id)
        time.sleep(1)
    else:
        print("  No old monitor found")

    # === Step 5: 启动或复用监控 ===
    print("\n[5/5] Starting or reusing monitor GUI...")
    if not reused_monitor_pid:
        monitor = subprocess.Popen(
            [sys.executable, "monitor.py"],
            cwd=DIR,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        time.sleep(2)
        if monitor.poll() is None:
            print(f"  OK: monitor GUI started (PID {monitor.pid})")
        else:
            print("  ERROR: monitor GUI failed to start")
    else:
        print("  OK: monitor GUI reused; no second GUI started")

    # === 最终自检 ===
    print("\n" + "=" * 50)
    print("  Health check:")

    # 后端
    backend_ok = is_port_up(PORT)
    print(f"    Backend(5000): {'OK running' if backend_ok else 'ERROR not running'}")

    # 监控
    monitor_ok = (monitor.poll() is None) if monitor else bool(reused_monitor_pid)
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
    active_monitor_pid = str(monitor.pid) if monitor else str(reused_monitor_pid)
    leftover_mon = [p for p in leftover_mon if str(p) != active_monitor_pid]
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
