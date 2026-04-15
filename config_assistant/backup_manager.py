import os
import json
import shutil
from datetime import datetime
from typing import Optional, List, Tuple
import glob


class BackupManager:
    BACKUP_SUFFIX = ".backup"

    def __init__(self, backup_dir: Optional[str] = None):
        if backup_dir:
            self.backup_dir = backup_dir
        else:
            self.backup_dir = os.path.join(os.path.expanduser("~"), ".aiproxy_backups")
        
        os.makedirs(self.backup_dir, exist_ok=True)

    def _generate_backup_filename(self, original_path: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(original_path)
        name, ext = os.path.splitext(filename)
        return f"{name}{self.BACKUP_SUFFIX}.{timestamp}{ext}"

    def create_backup(self, config_path: str) -> Tuple[bool, str]:
        if not os.path.exists(config_path):
            return False, f"配置文件不存在: {config_path}"
        
        try:
            backup_filename = self._generate_backup_filename(config_path)
            backup_path = os.path.join(self.backup_dir, backup_filename)
            
            shutil.copy2(config_path, backup_path)
            
            metadata = {
                "original_path": config_path,
                "backup_time": datetime.now().isoformat(),
                "backup_path": backup_path,
            }
            metadata_path = backup_path + ".meta"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            return True, backup_path
        except Exception as e:
            return False, f"备份失败: {str(e)}"

    def list_backups(self, original_path: Optional[str] = None) -> List[dict]:
        backups = []
        pattern = os.path.join(self.backup_dir, f"*{self.BACKUP_SUFFIX}*.json")
        
        for backup_file in glob.glob(pattern):
            if backup_file.endswith(".meta"):
                continue
            
            meta_file = backup_file + ".meta"
            backup_info = {
                "backup_path": backup_file,
                "backup_time": "",
                "original_path": "",
            }
            
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                    backup_info["backup_time"] = metadata.get("backup_time", "")
                    backup_info["original_path"] = metadata.get("original_path", "")
                except Exception:
                    pass
            
            if backup_info["backup_time"]:
                backups.append(backup_info)
        
        if original_path:
            backups = [b for b in backups if b["original_path"] == original_path]
        
        backups.sort(key=lambda x: x["backup_time"], reverse=True)
        return backups

    def restore_backup(self, backup_path: str, target_path: Optional[str] = None) -> Tuple[bool, str]:
        if not os.path.exists(backup_path):
            return False, f"备份文件不存在: {backup_path}"
        
        try:
            meta_file = backup_path + ".meta"
            if target_path is None:
                if os.path.exists(meta_file):
                    with open(meta_file, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                    target_path = metadata.get("original_path", "")
                else:
                    return False, "无法确定恢复目标路径"
            
            if not target_path:
                return False, "目标路径为空"
            
            target_dir = os.path.dirname(target_path)
            if target_dir and not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)
            
            shutil.copy2(backup_path, target_path)
            
            return True, f"已恢复到: {target_path}"
        except Exception as e:
            return False, f"恢复失败: {str(e)}"

    def delete_backup(self, backup_path: str) -> Tuple[bool, str]:
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
            
            meta_file = backup_path + ".meta"
            if os.path.exists(meta_file):
                os.remove(meta_file)
            
            return True, f"已删除备份: {backup_path}"
        except Exception as e:
            return False, f"删除失败: {str(e)}"

    def cleanup_old_backups(self, keep_count: int = 10) -> Tuple[int, str]:
        backups = self.list_backups()
        
        backups_by_original = {}
        for backup in backups:
            orig_path = backup["original_path"]
            if orig_path not in backups_by_original:
                backups_by_original[orig_path] = []
            backups_by_original[orig_path].append(backup)
        
        deleted_count = 0
        for orig_path, backup_list in backups_by_original.items():
            if len(backup_list) > keep_count:
                for backup in backup_list[keep_count:]:
                    success, _ = self.delete_backup(backup["backup_path"])
                    if success:
                        deleted_count += 1
        
        return deleted_count, f"已清理 {deleted_count} 个旧备份"
