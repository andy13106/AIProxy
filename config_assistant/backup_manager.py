"""备份与恢复管理模块

提供配置文件的自动备份和一键恢复功能。
命名格式：文件名.backup.时间戳.json
"""

import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass


@dataclass
class BackupInfo:
    """备份文件信息"""
    original_path: str
    backup_path: str
    timestamp: str
    size: int


class BackupManager:
    """配置文件备份管理器"""

    BACKUP_SUFFIX = ".backup"
    BACKUP_PATTERN = "{filename}.backup.{timestamp}{ext}"

    def __init__(self, backup_dir: Optional[str] = None):
        """
        Args:
            backup_dir: 备份文件存储目录，默认为原文件同目录
        """
        self.backup_dir = backup_dir

    def _generate_backup_path(self, original_path: str) -> str:
        """生成备份文件路径"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original = Path(original_path)

        # 构建备份文件名
        backup_filename = self.BACKUP_PATTERN.format(
            filename=original.stem,
            timestamp=timestamp,
            ext=original.suffix
        )

        # 确定备份目录
        if self.backup_dir:
            backup_dir_path = Path(self.backup_dir)
            backup_dir_path.mkdir(parents=True, exist_ok=True)
            return str(backup_dir_path / backup_filename)
        else:
            return str(original.parent / backup_filename)

    def create_backup(self, file_path: str) -> Tuple[bool, str, Optional[BackupInfo]]:
        """创建配置文件备份

        Args:
            file_path: 原配置文件路径

        Returns:
            (success, message, backup_info)
        """
        if not os.path.exists(file_path):
            # 文件不存在是正常情况（首次配置），不是错误
            return True, f"文件不存在，无需备份: {file_path}", None

        try:
            backup_path = self._generate_backup_path(file_path)
            shutil.copy2(file_path, backup_path)

            backup_info = BackupInfo(
                original_path=file_path,
                backup_path=backup_path,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                size=os.path.getsize(backup_path)
            )

            return True, f"备份成功: {backup_path}", backup_info

        except PermissionError:
            return False, f"权限不足，无法备份: {file_path}", None
        except Exception as e:
            return False, f"备份失败: {str(e)}", None

    def restore_from_backup(self, backup_path: str, target_path: Optional[str] = None) -> Tuple[bool, str]:
        """从备份文件恢复配置

        Args:
            backup_path: 备份文件路径
            target_path: 恢复目标路径，默认为原文件路径

        Returns:
            (success, message)
        """
        if not os.path.exists(backup_path):
            return False, f"备份文件不存在: {backup_path}"

        try:
            # 如果没有指定目标路径，尝试从备份文件名解析
            if target_path is None:
                target_path = self._parse_original_path_from_backup(backup_path)
                if target_path is None:
                    return False, "无法从备份文件名解析原文件路径，请手动指定"

            # 确保目标目录存在
            target_dir = os.path.dirname(target_path)
            if target_dir and not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            shutil.copy2(backup_path, target_path)
            return True, f"恢复成功: {backup_path} -> {target_path}"

        except PermissionError:
            return False, f"权限不足，无法恢复: {target_path}"
        except Exception as e:
            return False, f"恢复失败: {str(e)}"

    def _parse_original_path_from_backup(self, backup_path: str) -> Optional[str]:
        """从备份文件路径解析原文件路径"""
        backup = Path(backup_path)
        filename = backup.name

        # 解析备份文件名格式: filename.backup.timestamp.ext
        if self.BACKUP_SUFFIX not in filename:
            return None

        # 移除备份标记和时间戳
        parts = filename.split(self.BACKUP_SUFFIX)
        if len(parts) < 2:
            return None

        original_name = parts[0] + backup.suffix

        # 如果备份在特定目录，尝试还原到原位置
        if self.backup_dir and str(backup.parent) == self.backup_dir:
            # 尝试从常见位置还原
            home = str(Path.home())
            common_paths = [
                f"{home}/.claude/{original_name}",
                f"{home}/.opencode/{original_name}",
                f"{home}/.openclaw/{original_name}",
            ]
            for path in common_paths:
                if os.path.exists(os.path.dirname(path)):
                    return path

        # 默认还原到同目录
        return str(backup.parent / original_name)

    def list_backups(self, directory: Optional[str] = None) -> List[Dict]:
        """列出所有备份文件

        Args:
            directory: 要扫描的目录，默认为backup_dir或当前目录

        Returns:
            备份文件信息列表
        """
        search_dir = directory or self.backup_dir or "."

        if not os.path.exists(search_dir):
            return []

        backups = []
        for filename in os.listdir(search_dir):
            if self.BACKUP_SUFFIX in filename:
                filepath = os.path.join(search_dir, filename)
                if os.path.isfile(filepath):
                    stat = os.stat(filepath)
                    backups.append({
                        "filename": filename,
                        "path": filepath,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    })

        # 按修改时间倒序排列
        backups.sort(key=lambda x: x["modified"], reverse=True)
        return backups

    def cleanup_old_backups(self, max_backups: int = 10, directory: Optional[str] = None) -> Tuple[int, str]:
        """清理旧备份，只保留最近N个

        Args:
            max_backups: 保留的最大备份数
            directory: 要清理的目录

        Returns:
            (删除数量, 消息)
        """
        backups = self.list_backups(directory)

        if len(backups) <= max_backups:
            return 0, f"备份数量 ({len(backups)}) 未超过限制 ({max_backups})"

        to_delete = backups[max_backups:]
        deleted_count = 0

        for backup in to_delete:
            try:
                os.remove(backup["path"])
                deleted_count += 1
            except Exception:
                pass

        return deleted_count, f"已清理 {deleted_count} 个旧备份，保留 {max_backups} 个最新备份"

    def validate_json_backup(self, backup_path: str) -> Tuple[bool, str]:
        """验证备份文件是否为有效的JSON

        Args:
            backup_path: 备份文件路径

        Returns:
            (是否有效, 消息)
        """
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                content = f.read()
                json.loads(content)
            return True, "JSON格式有效"
        except json.JSONDecodeError as e:
            return False, f"JSON格式错误: {str(e)}"
        except Exception as e:
            return False, f"验证失败: {str(e)}"
