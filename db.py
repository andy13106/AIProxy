from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    text,
    Index,
    UniqueConstraint,
)
import datetime
import os
import sqlite3

# 确保数据目录存在 (使用相对路径)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

DB_FILE_PATH = os.path.join(DATA_DIR, "proxy.db")


def ensure_sqlite_schema() -> None:
    """轻量 SQLite 迁移：补齐新增列/表，避免旧库启动时报错。"""
    conn = sqlite3.connect(DB_FILE_PATH)
    try:
        cur = conn.cursor()

        # model_mappings.order 列
        cur.execute("PRAGMA table_info(model_mappings)")
        model_mapping_cols = {row[1] for row in cur.fetchall()}
        if model_mapping_cols and "order" not in model_mapping_cols:
            cur.execute('ALTER TABLE model_mappings ADD COLUMN "order" INTEGER DEFAULT 0')

        # providers.provider_type 列
        cur.execute("PRAGMA table_info(providers)")
        provider_cols = {row[1] for row in cur.fetchall()}
        if provider_cols and "provider_type" not in provider_cols:
            cur.execute("ALTER TABLE providers ADD COLUMN provider_type VARCHAR(30) DEFAULT 'openai'")

        # tool_default_models 表
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_default_models (
                id INTEGER PRIMARY KEY,
                tool_id VARCHAR(50) UNIQUE NOT NULL,
                default_model VARCHAR(100) NOT NULL,
                updated_at DATETIME
            )
            """
        )

        # playground_chat_sessions 表
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS playground_chat_sessions (
                id INTEGER PRIMARY KEY,
                session_uid VARCHAR(80) UNIQUE NOT NULL,
                title VARCHAR(200) NOT NULL,
                provider_name VARCHAR(100),
                model_name VARCHAR(200),
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )

        # playground_chat_messages 表
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS playground_chat_messages (
                id INTEGER PRIMARY KEY,
                session_uid VARCHAR(80) NOT NULL,
                seq INTEGER NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT,
                created_at DATETIME
            )
            """
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_playground_chat_messages_session_seq "
            "ON playground_chat_messages(session_uid, seq)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_playground_chat_messages_session_uid "
            "ON playground_chat_messages(session_uid)"
        )

        # playground_chat_attachments 表
        # 检查现有表结构，处理 session_uid 的 NOT NULL 约束
        cur.execute("PRAGMA table_info(playground_chat_attachments)")
        existing_cols = {row[1] for row in cur.fetchall()}

        if not existing_cols:
            # 表不存在，直接创建新表（session_uid 允许 NULL）
            cur.execute(
                """
                CREATE TABLE playground_chat_attachments (
                    id INTEGER PRIMARY KEY,
                    attachment_uid VARCHAR(80) UNIQUE NOT NULL,
                    message_id INTEGER,
                    session_uid VARCHAR(80),
                    filename VARCHAR(255) NOT NULL,
                    file_path VARCHAR(500) NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    mime_type VARCHAR(100),
                    attachment_type VARCHAR(20) NOT NULL DEFAULT 'unknown',
                    created_at DATETIME
                )
                """
            )
        else:
            # 表已存在，检查 session_uid 是否允许 NULL
            # SQLite 不支持直接修改列，需要创建新表迁移
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='playground_chat_attachments'")
            table_sql = cur.fetchone()
            if table_sql and "session_uid VARCHAR(80) NOT NULL" in table_sql[0]:
                # 需要迁移，session_uid 是 NOT NULL
                cur.execute(
                    """
                    CREATE TABLE playground_chat_attachments_new (
                        id INTEGER PRIMARY KEY,
                        attachment_uid VARCHAR(80) UNIQUE NOT NULL,
                        message_id INTEGER,
                        session_uid VARCHAR(80),
                        filename VARCHAR(255) NOT NULL,
                        file_path VARCHAR(500) NOT NULL,
                        file_size INTEGER NOT NULL DEFAULT 0,
                        mime_type VARCHAR(100),
                        attachment_type VARCHAR(20) NOT NULL DEFAULT 'unknown',
                        created_at DATETIME
                    )
                    """
                )
                # 复制数据
                cur.execute(
                    """
                    INSERT INTO playground_chat_attachments_new 
                    (id, attachment_uid, message_id, session_uid, filename, file_path, 
                     file_size, mime_type, attachment_type, created_at)
                    SELECT id, attachment_uid, message_id, session_uid, filename, file_path,
                           file_size, mime_type, attachment_type, created_at
                    FROM playground_chat_attachments
                    """
                )
                # 删除旧表，重命名新表
                cur.execute("DROP TABLE playground_chat_attachments")
                cur.execute("ALTER TABLE playground_chat_attachments_new RENAME TO playground_chat_attachments")

        # 创建索引
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_playground_chat_attachments_attachment_uid "
            "ON playground_chat_attachments(attachment_uid)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_playground_chat_attachments_session_uid "
            "ON playground_chat_attachments(session_uid)"
        )

        # api_keys.retry_after_seconds 列
        # SQLAlchemy create_all 不会修改旧表结构，补齐这个列避免旧 SQLite 库启动后查询直接 500。
        cur.execute("PRAGMA table_info(api_keys)")
        api_key_cols = {row[1] for row in cur.fetchall()}
        if api_key_cols and "retry_after_seconds" not in api_key_cols:
            cur.execute("ALTER TABLE api_keys ADD COLUMN retry_after_seconds INTEGER")

        conn.commit()
    finally:
        conn.close()


try:
    ensure_sqlite_schema()
except Exception as e:
    import sys
    print(f"[WARN] SQLite schema migration failed (non-fatal): {e}", file=sys.stderr)

from sqlalchemy.pool import NullPool

# SQLite 异步连接 URL（绝对路径，避免不同启动目录导致连接到不同 DB 文件）
DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE_PATH}"
# 同步连接用于 Streamlit（同样使用绝对路径）
SYNC_DATABASE_URL = f"sqlite:///{DB_FILE_PATH}"

# SQLite 不支持连接池，使用 NullPool
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

sync_engine = create_engine(
    SYNC_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)
SessionLocal = sessionmaker(sync_engine, expire_on_commit=False)

Base = declarative_base()

# 支持的上游供应商类型（litellm custom_llm_provider 值）
SUPPORTED_PROVIDER_TYPES = {
    "openai": "OpenAI 兼容（NVIDIA LLM, DeepSeek, vLLM, OneAPI 等）",
    "anthropic": "Anthropic 原生",
    "gemini": "Google Gemini",
    "bedrock": "AWS Bedrock",
    "vertex_ai": "Google Vertex AI",
    "azure": "Azure OpenAI",
    "ollama": "Ollama",
    "cohere": "Cohere",
    "mistral": "Mistral AI",
    "nvidia_image": "NVIDIA 文生图（SD3, FLUX 等）",
}


class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # 例如: Nvidia, DeepSeek, OpenAI
    api_base = Column(String(255), nullable=False) # API 基础地址
    provider_type = Column(String(30), nullable=False, default="openai")  # 上游协议类型

class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("providers.id"))
    key = Column(String(255), unique=True, nullable=False) # Key 设为唯一，防止重复添加
    is_active = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)
    last_failure = Column(DateTime, nullable=True) # 记录上次失败时间
    retry_after_seconds = Column(Integer, nullable=True) # 上游返回的 Retry-After，用于动态冷却

class ModelMapping(Base):
    __tablename__ = "model_mappings"
    id = Column(Integer, primary_key=True)
    virtual_name = Column(String(100), unique=True, nullable=False)  # 工具看到的模型名，如 "gpt-4"
    real_name = Column(String(100), nullable=False)  # 真实的模型名，如 "meta/llama-3.1-405b-instruct"
    provider_id = Column(Integer, ForeignKey("providers.id"))
    order = Column(Integer, default=0)  # 排序序号，数值越小越靠前

class UsageLog(Base):
    __tablename__ = "usage_logs"
    id = Column(Integer, primary_key=True)
    key_id = Column(Integer, ForeignKey("api_keys.id"))
    model_name = Column(String(100))
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    images_count = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class ToolDefaultModel(Base):
    """存储各个 AI 工具的默认模型配置"""
    __tablename__ = "tool_default_models"
    id = Column(Integer, primary_key=True)
    tool_id = Column(String(50), unique=True, nullable=False)  # 工具 ID，如 "claude_code", "opencode" 等
    default_model = Column(String(100), nullable=False)  # 默认模型名称
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class PlaygroundChatSession(Base):
    __tablename__ = "playground_chat_sessions"
    id = Column(Integer, primary_key=True)
    session_uid = Column(String(80), unique=True, nullable=False, index=True)
    title = Column(String(200), nullable=False, default="新对话")
    provider_name = Column(String(100), nullable=True)
    model_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class PlaygroundChatMessage(Base):
    __tablename__ = "playground_chat_messages"
    __table_args__ = (
        UniqueConstraint("session_uid", "seq", name="ux_playground_chat_messages_session_seq"),
        Index("ix_playground_chat_messages_session_uid", "session_uid"),
    )
    id = Column(Integer, primary_key=True)
    session_uid = Column(String(80), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=True, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class PlaygroundChatAttachment(Base):
    __tablename__ = "playground_chat_attachments"
    id = Column(Integer, primary_key=True)
    attachment_uid = Column(String(80), unique=True, nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("playground_chat_messages.id"), nullable=True)
    session_uid = Column(String(80), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    mime_type = Column(String(100), nullable=True)
    attachment_type = Column(String(20), nullable=False, default="unknown")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 启用 WAL 模式，提升并发读写性能
        await conn.execute(text("PRAGMA journal_mode=WAL"))
