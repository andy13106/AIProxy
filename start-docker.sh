#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DOCKER_INSTALLED=0
DOCKER_SERVICE_RUNNING=0

echo ""
echo "========================================"
echo "      AIProxy Docker 启动脚本"
echo "========================================"
echo ""

detect_os() {
    case "$(uname -s)" in
        Darwin*)
            echo "macos"
            ;;
        Linux*)
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID" in
                    ubuntu|debian)
                        echo "debian"
                        ;;
                    centos|rhel|fedora|rocky)
                        echo "rhel"
                        ;;
                    *)
                        echo "linux"
                        ;;
                esac
            else
                echo "linux"
            fi
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

install_docker_macos() {
    echo "🍎 检测到 macOS 系统"
    echo ""
    
    if ! command -v brew &> /dev/null; then
        echo "📦 正在安装 Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        echo ""
    fi
    
    echo "🐳 正在安装 Docker Desktop..."
    echo "⚠️  安装过程中可能需要输入密码"
    brew install --cask docker
    echo ""
    echo "✅ Docker Desktop 安装完成！"
    echo "📌 请手动启动 Docker Desktop 应用后再次运行此脚本"
    echo "   (在 Launchpad 中找到 Docker 图标并点击)"
    exit 0
}

install_docker_debian() {
    echo "🐧 检测到 Ubuntu/Debian 系统"
    echo ""
    echo "🔧 正在更新软件源..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq ca-certificates curl gnupg
    echo ""
    
    echo "🔑 正在添加 Docker GPG 密钥..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo ""
    
    echo "📦 正在添加 Docker 软件源..."
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    echo ""
    
    echo "🐳 正在安装 Docker Engine..."
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo ""
    
    echo "🔧 配置 Docker 权限..."
    sudo usermod -aG docker $USER || true
    echo ""
    
    echo "✅ Docker Engine 安装完成！"
    echo "📌 为了让用户组权限生效，请执行以下任一操作："
    echo "   1. 注销并重新登录"
    echo "   2. 执行: newgrp docker"
    echo "   3. 或者使用 sudo 运行此脚本"
    exit 0
}

install_docker_rhel() {
    echo "🐧 检测到 CentOS/RHEL/Fedora 系统"
    echo ""
    echo "📦 正在添加 Docker 软件源..."
    sudo dnf -y -q install dnf-plugins-core
    sudo dnf config-manager -y --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    echo ""
    
    echo "🐳 正在安装 Docker Engine..."
    sudo dnf -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo ""
    
    echo "🔧 正在启动 Docker 服务..."
    sudo systemctl start docker
    sudo systemctl enable docker
    echo ""
    
    echo "🔧 配置 Docker 权限..."
    sudo usermod -aG docker $USER || true
    echo ""
    
    echo "✅ Docker Engine 安装完成！"
    echo "📌 为了让用户组权限生效，请执行以下任一操作："
    echo "   1. 注销并重新登录"
    echo "   2. 执行: newgrp docker"
    echo "   3. 或者使用 sudo 运行此脚本"
    exit 0
}

install_docker() {
    OS=$(detect_os)
    
    echo "❌ 未检测到 Docker"
    echo ""
    read -p "❓ 是否自动安装 Docker? (y/N) " -n 1 -r
    echo ""
    echo ""
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "🚫 取消安装"
        exit 1
    fi
    
    echo ""
    
    case "$OS" in
        macos)
            install_docker_macos
            ;;
        debian)
            install_docker_debian
            ;;
        rhel)
            install_docker_rhel
            ;;
        *)
            echo "❌ 不支持自动安装 Docker 的操作系统"
            echo "📌 请手动安装 Docker: https://docs.docker.com/get-docker/"
            exit 1
            ;;
    esac
}

check_docker_running() {
    if ! docker info &> /dev/null; then
        echo "❌ Docker 服务未启动"
        echo ""
        
        OS=$(detect_os)
        if [ "$OS" = "macos" ]; then
            echo "📌 请在 Launchpad 中启动 Docker Desktop 应用"
            open -a Docker 2>/dev/null || true
        else
            echo "🔧 正在尝试启动 Docker 服务..."
            sudo systemctl start docker
        fi
        
        echo ""
        echo "⏳ 等待 Docker 服务启动..."
        for i in {1..30}; do
            if docker info &> /dev/null; then
                echo "✅ Docker 服务已启动"
                DOCKER_SERVICE_RUNNING=1
                return
            fi
            sleep 1
            echo -n "."
        done
        echo ""
        echo "❌ Docker 服务启动超时，请手动检查"
        exit 1
    fi
    DOCKER_SERVICE_RUNNING=1
}

if ! command -v docker &> /dev/null; then
    install_docker
fi
DOCKER_INSTALLED=1
echo "✅ Docker 已安装"

check_docker_running
echo "✅ Docker 服务运行正常"

if [ ! -f .env ]; then
    echo ""
    echo "⚠️  未找到 .env 文件，正在从 .env.example 创建..."
    cp .env.example .env
    echo ""
    echo "========================================"
    echo "⚠️  重要提示：请编辑 .env 文件！"
    echo "========================================"
    echo ""
    echo "📝 请修改以下配置："
    echo "   1. MASTER_KEY=sk-admin-123456 改为你的管理密钥"
    echo ""
    echo "✏️  编辑完成后再次运行此脚本"
    exit 0
fi

echo ""
echo "🌐 检测网络环境，选择最佳构建源..."
COMPOSE_FILE="docker-compose.yml"
if curl -s --connect-timeout 2 mirrors.aliyun.com > /dev/null 2>&1 && [ ! "$USE_GLOBAL_SOURCE" = "1" ]; then
    echo "✅ 检测到国内网络，使用阿里云镜像源加速构建"
    COMPOSE_FILE="docker-compose-cn.yml"
else
    echo "✅ 使用官方源构建"
fi

ACTION="${1:-up}"

if [ "$ACTION" = "logs" ]; then
    docker compose -f "$COMPOSE_FILE" logs -f
    exit 0
elif [ "$ACTION" = "restart" ]; then
    docker compose -f "$COMPOSE_FILE" restart
    exit 0
elif [ "$ACTION" = "stop" ]; then
    docker compose -f "$COMPOSE_FILE" down
    exit 0
elif [ "$ACTION" != "up" ]; then
    echo "❌ 不支持的参数: $ACTION"
    echo "用法: ./start-docker.sh [up|logs|restart|stop]"
    exit 1
fi

echo ""
echo "🚀 正在启动 AIProxy 服务..."
echo ""

mkdir -p data

docker compose -f "$COMPOSE_FILE" up -d --build

echo ""
echo "========================================"
echo "✅ 服务启动完成！"
echo "========================================"
echo ""
echo "📡 代理接口: http://localhost:8000"
echo "🎛️  管理面板: http://localhost:8501"
echo ""
echo "📋 常用命令:"
echo "  查看日志: ./start-docker.sh logs"
echo "  重启服务: ./start-docker.sh restart"
echo "  停止服务: ./start-docker.sh stop"
echo ""
