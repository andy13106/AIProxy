import subprocess
import sys
import time
import os
from dotenv import load_dotenv

# 加载配置
load_dotenv()

def start_services():
    print("🚀 正在启动 AI-Proxy Master...")
    
    # 1. 启动 FastAPI 后端 (uvicorn)
    backend_cmd = [
        sys.executable, "-m", "uvicorn", 
        "proxy_logic:app", 
        "--host", os.getenv("PROXY_HOST", "0.0.0.0"), 
        "--port", os.getenv("PROXY_PORT", "8000")
    ]
    print(f"📡 API 代理后端正在启动: http://{os.getenv('PROXY_HOST', '0.0.0.0')}:{os.getenv('PROXY_PORT', '8000')}")
    backend_process = subprocess.Popen(backend_cmd)

    # 2. 启动 Streamlit 管理后台
    frontend_cmd = [
        sys.executable, "-m", "streamlit", "run", 
        "admin_panel.py", 
        "--server.address", os.getenv("ADMIN_HOST", "0.0.0.0"), 
        "--server.port", os.getenv("ADMIN_PORT", "8501"),
        "--server.headless", "true"
    ]
    print(f"🎨 管理后台正在启动: http://{os.getenv('ADMIN_HOST', '0.0.0.0')}:{os.getenv('ADMIN_PORT', '8501')}")
    frontend_process = subprocess.Popen(frontend_cmd)

    print("\n✅ 所有服务已启动！按 Ctrl+C 停止服务。")
    
    try:
        # 持续运行并监控进程
        while True:
            if backend_process.poll() is not None:
                print("❌ 后端进程已意外停止，正在尝试重启...")
                backend_process = subprocess.Popen(backend_cmd)
            if frontend_process.poll() is not None:
                print("❌ 前端进程已意外停止，正在尝试重启...")
                frontend_process = subprocess.Popen(frontend_cmd)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n🛑 正在停止所有服务...")
        backend_process.terminate()
        frontend_process.terminate()
        print("👋 服务已安全关闭。")

if __name__ == "__main__":
    start_services()
