"""配置管理模块"""

import logging
import os
import sys
import contextvars
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """应用配置"""

    # 认证配置
    master_key: str = field(default_factory=lambda: os.getenv("MASTER_KEY", ""))
    auth_enabled: bool = field(default_factory=lambda: os.getenv("AUTH_ENABLED", "true").lower() == "true")

    # 超时配置
    upstream_timeout_sec: float = field(default_factory=lambda: float(os.getenv("UPSTREAM_TIMEOUT_SEC", "90")))
    request_total_timeout_sec: float = field(
        default_factory=lambda: float(os.getenv("REQUEST_TOTAL_TIMEOUT_SEC", "180"))
    )
    allow_client_timeout_override: bool = field(
        default_factory=lambda: os.getenv("ALLOW_CLIENT_TIMEOUT_OVERRIDE", "false").lower() == "true"
    )
    stream_heartbeat_sec: float = field(default_factory=lambda: float(os.getenv("STREAM_HEARTBEAT_SEC", "15")))
    stream_max_duration_sec: float = field(
        default_factory=lambda: float(os.getenv("STREAM_MAX_DURATION_SEC", "3600"))
    )
    key_rate_limit_cooldown_sec: float = field(
        default_factory=lambda: float(os.getenv("KEY_RATE_LIMIT_COOLDOWN_SEC", "30"))
    )
    key_strategy: str = field(default_factory=lambda: os.getenv("KEY_STRATEGY", "sticky_failover").lower())

    # Fallback 模型配置
    anthropic_fallback_virtual_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_FALLBACK_VIRTUAL_MODEL", "claude-3-5-sonnet-20241022")
    )
    default_fallback_virtual_model: str = field(default_factory=lambda: os.getenv("DEFAULT_FALLBACK_VIRTUAL_MODEL", "GLM5"))

    # 服务地址配置
    proxy_host: str = field(default_factory=lambda: os.getenv("PROXY_HOST", "0.0.0.0"))
    proxy_port: str = field(default_factory=lambda: os.getenv("PROXY_PORT", "8000"))
    admin_host: str = field(default_factory=lambda: os.getenv("ADMIN_HOST", "0.0.0.0"))
    admin_port: str = field(default_factory=lambda: os.getenv("ADMIN_PORT", "8501"))

    # 速率限制（0 = 不限制）
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "0")))

    # 管理面板密码（空 = 不需要密码）
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))

    # 日志级别
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file_enabled: bool = field(default_factory=lambda: os.getenv("LOG_FILE_ENABLED", "true").lower() == "true")
    log_dir: str = field(default_factory=lambda: os.getenv("LOG_DIR", "./logs"))
    log_file_name: str = field(default_factory=lambda: os.getenv("LOG_FILE_NAME", "proxy.log"))
    log_file_max_bytes: int = field(default_factory=lambda: int(os.getenv("LOG_FILE_MAX_BYTES", "10485760")))
    log_file_backup_count: int = field(default_factory=lambda: int(os.getenv("LOG_FILE_BACKUP_COUNT", "7")))

# 每个请求的 request_id 上下文（默认 "-"）
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """向日志记录注入 request_id 字段"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("-")
        return True


def setup_logging(config: Settings) -> logging.Logger:
    """配置日志"""
    logger = logging.getLogger("ai_proxy")
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [req:%(request_id)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    request_filter = RequestIdFilter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_filter)
    logger.addHandler(stream_handler)

    if config.log_file_enabled:
        os.makedirs(config.log_dir, exist_ok=True)
        file_path = os.path.join(config.log_dir, config.log_file_name)
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=config.log_file_max_bytes,
            backupCount=config.log_file_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        logger.addHandler(file_handler)

    return logger


# 全局配置实例
settings = Settings()

# 安全检查：MASTER_KEY 不能为空
if settings.auth_enabled and not settings.master_key:
    print("[FATAL] MASTER_KEY 未设置，请在环境变量或 .env 文件中设置 MASTER_KEY")
    sys.exit(1)

# 全局日志实例
logger = setup_logging(settings)
