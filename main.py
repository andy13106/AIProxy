import os
import socket
import subprocess
import sys
import time
import atexit
import signal
import threading
import platform

from dotenv import load_dotenv

IS_WINDOWS = platform.system() == "Windows"

try:
    from playground_page import cleanup_threads
except ImportError:
    cleanup_threads = None

_shutting_down = False
_shutdown_lock = threading.Lock()

def _set_shutting_down():
    with _shutdown_lock:
        global _shutting_down
        _shutting_down = True

def _is_shutting_down():
    with _shutdown_lock:
        return _shutting_down

def _cleanup_thread_pools():
    try:
        import concurrent.futures
        import concurrent.futures.thread
        pool = getattr(concurrent.futures.ThreadPoolExecutor, '_default_executor', None)
        if pool is not None:
            pool.shutdown(wait=False)
            concurrent.futures.ThreadPoolExecutor._default_executor = None
        
        if hasattr(concurrent.futures.thread, '_threads_queues'):
            concurrent.futures.thread._threads_queues.clear()
        
        if hasattr(concurrent.futures.thread, '_python_exit'):
            concurrent.futures.thread._python_exit = lambda: None
    except Exception:
        pass

atexit.register(_cleanup_thread_pools)

load_dotenv()


def configure_console() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def start_services() -> None:
    configure_console()

    print("Starting AI-Proxy Master...")

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "proxy_logic:app",
        "--host",
        os.getenv("PROXY_HOST", "0.0.0.0"),
        "--port",
        os.getenv("PROXY_PORT", "8000"),
        "--no-use-colors",
    ]
    proxy_workers = int(os.getenv("PROXY_WORKERS", "1") or "1")
    if proxy_workers > 1:
        backend_cmd.extend(["--workers", str(proxy_workers)])
    print(
        "API backend: "
        f"http://{os.getenv('PROXY_HOST', '0.0.0.0')}:{os.getenv('PROXY_PORT', '8000')}"
    )
    if proxy_workers > 1:
        print(f"API backend workers: {proxy_workers}")
    backend_process = subprocess.Popen(backend_cmd)

    admin_host = os.getenv("ADMIN_HOST", "0.0.0.0")
    admin_port = int(os.getenv("ADMIN_PORT", "8501"))

    def _find_available_port(start_port: int, max_tries: int = 20) -> int | None:
        """通过 bind() 测试端口可用性，支持 IPv4/IPv6 双栈"""
        for port in range(start_port, start_port + max_tries):
            # 尝试 IPv4
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("0.0.0.0", port))
                    # 端口可用，立即释放
                    pass
            except OSError:
                continue

            # 如果 ADMIN_HOST 是 IPv6 地址，也测试 IPv6
            admin_host = os.getenv("ADMIN_HOST", "0.0.0.0")
            if admin_host in ("::", "::1", "0.0.0.0") or ":" in admin_host:
                try:
                    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind(("::", port))
                except OSError:
                    continue

            return port
        return None

    selected_admin_port = _find_available_port(admin_port)
    frontend_process = None
    frontend_fail_count = 0

    if selected_admin_port is None:
        print(
            "Admin panel: no available port found. "
            f"Checked from {admin_port} to {admin_port + 19}. Skipping admin panel startup."
        )
    else:
        if selected_admin_port != admin_port:
            print(
                f"Admin panel port {admin_port} is busy, auto-switched to {selected_admin_port}."
            )

    frontend_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "admin_panel.py",
        "--server.address",
        admin_host,
        "--server.port",
        str(selected_admin_port or admin_port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    if selected_admin_port is not None:
        print("Admin panel: " f"http://{admin_host}:{selected_admin_port}")
        
        if IS_WINDOWS:
            frontend_process = subprocess.Popen(
                frontend_cmd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            def set_new_process_group():
                os.setpgrp()
            frontend_process = subprocess.Popen(frontend_cmd, preexec_fn=set_new_process_group)

    print("\nServices started. Press Ctrl+C to stop.")

    def stop_services():
        print("\nStopping services...")
        
        if cleanup_threads:
            try:
                cleanup_threads()
            except Exception:
                pass
        
        if frontend_process is not None and frontend_process.poll() is None:
            try:
                if IS_WINDOWS:
                    frontend_process.send_signal(signal.CTRL_BREAK_EVENT)
                    try:
                        frontend_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        frontend_process.kill()
                        frontend_process.wait()
                else:
                    os.killpg(os.getpgid(frontend_process.pid), signal.SIGTERM)
                    try:
                        frontend_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(frontend_process.pid), signal.SIGKILL)
                        frontend_process.wait()
            except Exception:
                try:
                    frontend_process.terminate()
                    frontend_process.wait(timeout=5)
                except Exception:
                    pass
        
        if backend_process.poll() is None:
            backend_process.terminate()
            try:
                backend_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend_process.kill()
                backend_process.wait()
        
        print("Services stopped.")
        sys.exit(0)

    def handle_signal(signum, frame):
        if _is_shutting_down():
            return
        _set_shutting_down()
        stop_services()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while True:
            if _is_shutting_down():
                break
            if backend_process.poll() is not None:
                print("Backend exited unexpectedly. Restarting...")
                backend_process = subprocess.Popen(backend_cmd)
            if frontend_process is not None and frontend_process.poll() is not None:
                frontend_fail_count += 1
                if frontend_fail_count >= 5:
                    print(
                        "Admin panel failed repeatedly (>=5 times). "
                        "Stopping auto-restart to avoid log spam. "
                        "Set a free ADMIN_PORT and restart when needed."
                    )
                    frontend_process = None
                else:
                    delay = min(30, 2 * frontend_fail_count)
                    print(f"Admin panel exited unexpectedly. Restarting in {delay}s...")
                    time.sleep(delay)
                    frontend_process = subprocess.Popen(frontend_cmd)
            time.sleep(1)
    except KeyboardInterrupt:
        if not _is_shutting_down():
            _set_shutting_down()
            stop_services()


if __name__ == "__main__":
    start_services()
