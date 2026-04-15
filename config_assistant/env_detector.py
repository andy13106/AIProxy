import os
import platform


def is_docker_environment() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            if "docker" in content or "kubepods" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass
    
    docker_env_vars = [
        "KUBERNETES_SERVICE_HOST",
        "DOCKER_CONTAINER",
        "CONTAINER",
    ]
    for var in docker_env_vars:
        if os.environ.get(var):
            return True
    
    return False


def get_environment_info() -> dict:
    return {
        "is_docker": is_docker_environment(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "home_dir": os.path.expanduser("~"),
    }
