import os
import subprocess
import sys
import time

from dotenv import load_dotenv

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
    print(
        "API backend: "
        f"http://{os.getenv('PROXY_HOST', '0.0.0.0')}:{os.getenv('PROXY_PORT', '8000')}"
    )
    backend_process = subprocess.Popen(backend_cmd)

    frontend_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "admin_panel.py",
        "--server.address",
        os.getenv("ADMIN_HOST", "0.0.0.0"),
        "--server.port",
        os.getenv("ADMIN_PORT", "8501"),
        "--server.headless",
        "true",
    ]
    print(
        "Admin panel: "
        f"http://{os.getenv('ADMIN_HOST', '0.0.0.0')}:{os.getenv('ADMIN_PORT', '8501')}"
    )
    frontend_process = subprocess.Popen(frontend_cmd)

    print("\nServices started. Press Ctrl+C to stop.")

    try:
        while True:
            if backend_process.poll() is not None:
                print("Backend exited unexpectedly. Restarting...")
                backend_process = subprocess.Popen(backend_cmd)
            if frontend_process.poll() is not None:
                print("Admin panel exited unexpectedly. Restarting...")
                frontend_process = subprocess.Popen(frontend_cmd)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopping services...")
        backend_process.terminate()
        frontend_process.terminate()
        print("Services stopped.")


if __name__ == "__main__":
    start_services()
