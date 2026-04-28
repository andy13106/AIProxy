import os
import socket
import subprocess
import sys
import time

from dotenv import load_dotenv

# 导入线程清理函数
try:
    from playground_page import cleanup_threads
except ImportError:
    cleanup_threads = None

load_dotenv()


def configure_console() -> None:
    """Best-effort UTF-8 console output for Windows/macOS/Linux."""
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
        for port in range(start_port, start_port + max_tries):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if sock.connect_ex(("127.0.0.1", port)) != 0:
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
        frontend_process = subprocess.Popen(frontend_cmd)

    print("\nServices started. Press Ctrl+C to stop.")

    try:
        while True:
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
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopping services...")
        
        # 清理工作线程
        if cleanup_threads:
            try:
                cleanup_threads()
            except Exception:
                pass
        
        backend_process.terminate()
        try:
            backend_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend_process.kill()
            backend_process.wait()
        if frontend_process is not None:
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                frontend_process.kill()
                frontend_process.wait()
        print("Services stopped.")


if __name__ == "__main__":
    start_services()
