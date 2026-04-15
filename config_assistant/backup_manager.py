import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class BackupManager:
    """配置文件备份与恢复管理器"""

    def __init__(self, backup_dir: Optional[str] = None):
        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            import os
            project_root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.backup_dir = project_root / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _get_backup_filename(self, original_path: str) -> str:
        """生成备份文件名: 文件名.backup.时间戳.json"""
        orig_path = Path(original_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = orig_path.stem
        suffix = orig_path.suffix
        return f"{stem}.backup.{timestamp}{suffix}"

    def create_backup(self, config_path: str) -> Optional[str]:
        """创建配置文件备份
        
        Returns:
            备份文件路径，如果失败返回None
        """
        try:
            src_path = Path(config_path)
            if not src_path.exists():
                return None

            backup_filename = self._get_backup_filename(config_path)
            backup_path = self.backup_dir / backup_filename
            
            shutil.copy2(src_path, backup_path)
            return str(backup_path.absolute())
        except Exception as e:
            print(f"备份失败: {e}")
            return None

    def list_backups(self, tool_id: Optional[str] = None) -> List[Dict]:
        """列出所有备份文件"""
        backups = []
        for f in self.backup_dir.glob("*.backup.*.json"):
            try:
                stat = f.stat()
                backups.append({
                    "filename": f.name,
                    "path": str(f.absolute()),
                    "created": datetime.fromtimestamp(stat.st_mtime),
                    "size": stat.st_size,
                })
            except Exception:
                continue
        
        backups.sort(key=lambda x: x["created"], reverse=True)
        return backups

    def restore_backup(self, backup_path: str, target_path: str) -> bool:
        """从备份文件恢复"""
        try:
            backup = Path(backup_path)
            target = Path(target_path)
            
            if not backup.exists():
                return False
            
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            return True
        except Exception as e:
            print(f"恢复失败: {e}")
            return False

    def restore_latest(self, tool_id: str, target_path: str) -> bool:
        """恢复某个工具的最新备份"""
        backups = self.list_backups(tool_id)
        if not backups:
            return False
        return self.restore_backup(backups[0]["path"], target_path)

    def validate_json(self, file_path: str) -> bool:
        """验证JSON文件格式合法性"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, FileNotFoundError, UnicodeDecodeError):
            return False

    def clean_old_backups(self, keep_count: int = 10) -> int:
        """清理旧的备份文件，保留最新的N个"""
        backups = self.list_backups()
        removed = 0
        for backup in backups[keep_count:]:
            try:
                Path(backup["path"]).unlink()
                removed += 1
            except Exception:
                pass
        return removed
