@echo off
setlocal enabledelayedexpansion

:: ========================================
:: AIProxy Docker 启动脚本 (Windows)
:: 兼容 CMD 和 PowerShell
:: ========================================

echo.
echo ========================================
echo      AIProxy Docker 启动脚本
echo ========================================
echo.

set "DOCKER_INSTALLED=0"
set "DOCKER_RUNNING=0"
set "ACTION=start"

if "%1"=="logs" set "ACTION=logs"
if "%1"=="restart" set "ACTION=restart"
if "%1"=="stop" set "ACTION=stop"

:: 检测 Docker 是否已安装
where docker >nul 2>&1
if %errorlevel% equ 0 (
    echo [1/3] ✅ Docker 已安装
    set "DOCKER_INSTALLED=1"
) else (
    echo [1/3] ❌ 未检测到 Docker
    echo.
    echo ========================================
    echo ⚠️  Docker Desktop 未安装
    echo ========================================
    echo.
    echo 📦 正在尝试使用 winget 自动安装 Docker Desktop...
    echo.
    
    where winget >nul 2>&1
    if %errorlevel% equ 0 (
        echo 🔧 正在安装 Docker Desktop...
        echo ⚠️  安装过程中需要管理员权限
        winget install Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
        echo.
        echo ✅ Docker Desktop 安装完成！
        echo 📌 请重启电脑后再次运行此脚本
        pause
        exit /b 0
    ) else (
        echo ❌ 未找到 winget 命令
        echo.
        echo 📌 请手动下载安装 Docker Desktop:
        echo    https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe
        echo.
        pause
        exit /b 1
    )
)

:: 检测 Docker 服务是否运行
docker info >nul 2>&1
if %errorlevel% equ 0 (
    echo [2/3] ✅ Docker 服务运行正常
    set "DOCKER_RUNNING=1"
) else (
    echo [2/3] ❌ Docker 服务未启动
    echo.
    echo 🔧 正在尝试启动 Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    
    echo.
    echo ⏳ 等待 Docker 服务启动...
    set "count=0"
    :waitloop
    timeout /t 1 /nobreak >nul
    set /a count+=1
    docker info >nul 2>&1
    if %errorlevel% neq 0 (
        if !count! lss 60 (
            set /a dots=count %% 5
            if !dots! equ 0 (
                <nul set /p "=."
            )
            goto waitloop
        )
        echo.
        echo ❌ Docker 服务启动超时
        echo 📌 请手动启动 Docker Desktop
        pause
        exit /b 1
    )
    echo.
    echo [2/3] ✅ Docker 服务已启动
)

:: 检查 .env 配置文件
if not exist ".env" (
    echo [3/3] ⚠️  未找到 .env 文件，正在从 .env.example 创建...
    copy ".env.example" ".env" >nul
    echo.
    echo ========================================
    echo ⚠️  重要提示：请编辑 .env 文件！
    echo ========================================
    echo.
    echo 📝 请修改以下配置：
    echo    1. MASTER_KEY=sk-admin-123456 改为你的管理密钥
    echo.
    echo ✏️  编辑完成后再次运行此脚本
    echo.
    notepad ".env"
    pause
    exit /b 0
)

echo [3/3] ✅ 配置文件已就绪

:: 执行操作
if "%ACTION%"=="start" (
    echo.
    echo 🚀 正在启动 AIProxy 服务...
    echo.
    
    if not exist "data" (
        mkdir data
    )
    
    docker compose up -d --build
    
    echo.
    echo ========================================
    echo ✅ 服务启动完成！
    echo ========================================
    echo.
    echo 📡 代理接口: http://localhost:8000
    echo 🎛️  管理面板: http://localhost:8501
    echo.
)

if "%ACTION%"=="logs" (
    echo 📋 正在查看日志...
    docker compose logs -f
)

if "%ACTION%"=="restart" (
    echo 🔄 正在重启服务...
    docker compose restart
    echo ✅ 服务已重启
)

if "%ACTION%"=="stop" (
    echo ⏹️  正在停止服务...
    docker compose down
    echo ✅ 服务已停止
)

echo.
echo 📋 常用命令:
echo    查看日志: start-docker.bat logs
echo    重启服务: start-docker.bat restart
echo    停止服务: start-docker.bat stop
echo.

if "%ACTION%"=="start" (
    pause
)
