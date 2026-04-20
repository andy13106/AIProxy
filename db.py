from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
import datetime
import os

# 确保数据目录存在 (使用相对路径)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

from sqlalchemy.pool import NullPool

# SQLite 异步连接 URL (相对路径)
DATABASE_URL = f"sqlite+aiosqlite:///./data/proxy.db"
# 同步连接用于 Streamlit
SYNC_DATABASE_URL = f"sqlite:///./data/proxy.db"

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

class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # 例如: Nvidia, DeepSeek, OpenAI
    api_base = Column(String(255), nullable=False) # API 基础地址

class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("providers.id"))
    key = Column(String(255), unique=True, nullable=False) # Key 设为唯一，防止重复添加
    is_active = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)
    last_failure = Column(DateTime, nullable=True) # 记录上次失败时间

class ModelMapping(Base):
    __tablename__ = "model_mappings"
    id = Column(Integer, primary_key=True)
    virtual_name = Column(String(100), unique=True, nullable=False)  # 工具看到的模型名，如 "gpt-4"
    real_name = Column(String(100), nullable=False)     # 真实的模型名，如 "meta/llama-3.1-405b-instruct"
    provider_id = Column(Integer, ForeignKey("providers.id"))

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

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
