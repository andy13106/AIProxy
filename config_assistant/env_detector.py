import os


def is_running_in_docker() -> bool:
    """检测是否运行在Docker容器中"""
    if os.path.exists("/.dockerenv"):
        return True
    
    cgroup_path = "/proc/1/cgroup"
    if os.path.exists(cgroup_path):
        try:
            with open(cgroup_path, "r") as f:
                content = f.read()
                if "docker" in content or "containerd" in content:
                    return True
        except Exception:
            pass
    
    return False


def get_runtime_env() -> str:
    """返回运行环境描述"""
    if is_running_in_docker():
        return "docker"
    return "native"
