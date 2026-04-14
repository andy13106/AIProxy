<#
.SYNOPSIS
AIProxy Docker 启动脚本 (Windows PowerShell)
#>

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "      AIProxy Docker 启动脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$dockerInstalled = $false
$dockerRunning = $false
$action = "start"

if ($args.Count -gt 0) {
    $action = $args[0]
}

function Test-CommandExists {
    param($command)
    $null -ne (Get-Command $command -ErrorAction SilentlyContinue)
}

Write-Host "[1/3] 检测 Docker 安装..." -ForegroundColor Yellow

if (Test-CommandExists "docker") {
    Write-Host "[1/3] ✅ Docker 已安装" -ForegroundColor Green
    $dockerInstalled = $true
} else {
    Write-Host "[1/3] ❌ 未检测到 Docker" -ForegroundColor Red
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "⚠️  Docker Desktop 未安装" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""
    
    if (Test-CommandExists "winget") {
        $install = Read-Host "❓ 是否自动安装 Docker Desktop? (Y/n)"
        if ($install -eq "" -or $install -eq "Y" -or $install -eq "y") {
            Write-Host ""
            Write-Host "🔧 正在安装 Docker Desktop..." -ForegroundColor Cyan
            Write-Host "⚠️  安装过程中需要管理员权限" -ForegroundColor Yellow
            
            winget install Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
            
            Write-Host ""
            Write-Host "✅ Docker Desktop 安装完成！" -ForegroundColor Green
            Write-Host "📌 请重启电脑后再次运行此脚本" -ForegroundColor Yellow
            Read-Host "按 Enter 退出"
            exit 0
        }
    } else {
        Write-Host "❌ 未找到 winget 命令" -ForegroundColor Red
    }
    
    Write-Host ""
    Write-Host "📌 请手动下载安装 Docker Desktop:" -ForegroundColor Yellow
    Write-Host "   https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" -ForegroundColor Cyan
    Write-Host ""
    Read-Host "按 Enter 退出"
    exit 1
}

Write-Host "[2/3] 检测 Docker 服务状态..." -ForegroundColor Yellow

try {
    $null = docker info 2>&1
    Write-Host "[2/3] ✅ Docker 服务运行正常" -ForegroundColor Green
    $dockerRunning = $true
} catch {
    Write-Host "[2/3] ❌ Docker 服务未启动" -ForegroundColor Red
    Write-Host ""
    Write-Host "🔧 正在尝试启动 Docker Desktop..." -ForegroundColor Cyan
    
    $dockerPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerPath) {
        Start-Process $dockerPath
    } else {
        Start-Process "Docker Desktop.exe" -ErrorAction SilentlyContinue
    }
    
    Write-Host ""
    Write-Host "⏳ 等待 Docker 服务启动..." -ForegroundColor Yellow
    
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $null = docker info 2>&1
            Write-Host ""
            Write-Host "[2/3] ✅ Docker 服务已启动" -ForegroundColor Green
            $dockerRunning = $true
            break
        } catch {
            Write-Host "." -NoNewline
            Start-Sleep 1
        }
    }
    
    if (-not $dockerRunning) {
        Write-Host ""
        Write-Host "❌ Docker 服务启动超时" -ForegroundColor Red
        Write-Host "📌 请手动启动 Docker Desktop" -ForegroundColor Yellow
        Read-Host "按 Enter 退出"
        exit 1
    }
}

Write-Host "[3/3] 检查配置文件..." -ForegroundColor Yellow

if (-not (Test-Path ".env")) {
    Write-Host "[3/3] ⚠️  未找到 .env 文件，正在从 .env.example 创建..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env" -Force
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "⚠️  重要提示：请编辑 .env 文件！" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "📝 请修改以下配置：" -ForegroundColor Yellow
    Write-Host "   1. MASTER_KEY=sk-admin-123456 改为你的管理密钥" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "✏️  正在打开编辑器..." -ForegroundColor Cyan
    
    notepad ".env"
    
    Write-Host "编辑完成后再次运行此脚本" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "按 Enter 退出"
    exit 0
}

Write-Host "[3/3] ✅ 配置文件已就绪" -ForegroundColor Green

switch ($action) {
    "start" {
        Write-Host ""
        Write-Host "🚀 正在启动 AIProxy 服务..." -ForegroundColor Cyan
        Write-Host ""
        
        if (-not (Test-Path "data")) {
            New-Item -ItemType Directory -Path "data" -Force | Out-Null
        }
        
        docker compose up -d --build
        
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "✅ 服务启动完成！" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "📡 代理接口: " -NoNewline
        Write-Host "http://localhost:8000" -ForegroundColor Cyan
        Write-Host "🎛️  管理面板: " -NoNewline
        Write-Host "http://localhost:8501" -ForegroundColor Cyan
        Write-Host ""
    }
    
    "logs" {
        Write-Host "📋 正在查看日志..." -ForegroundColor Cyan
        docker compose logs -f
    }
    
    "restart" {
        Write-Host "🔄 正在重启服务..." -ForegroundColor Cyan
        docker compose restart
        Write-Host "✅ 服务已重启" -ForegroundColor Green
    }
    
    "stop" {
        Write-Host "⏹️  正在停止服务..." -ForegroundColor Cyan
        docker compose down
        Write-Host "✅ 服务已停止" -ForegroundColor Green
    }
}

Write-Host "📋 常用命令:" -ForegroundColor Yellow
Write-Host "   查看日志: " -NoNewline
Write-Host ".\start-docker.ps1 logs" -ForegroundColor Cyan
Write-Host "   重启服务: " -NoNewline
Write-Host ".\start-docker.ps1 restart" -ForegroundColor Cyan
Write-Host "   停止服务: " -NoNewline
Write-Host ".\start-docker.ps1 stop" -ForegroundColor Cyan
Write-Host ""

if ($action -eq "start") {
    Read-Host "按 Enter 继续"
}
