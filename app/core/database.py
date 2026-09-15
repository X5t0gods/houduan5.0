"""数据库连接模块 - SQLAlchemy 2.0 同步引擎与会话管理。

技术决策：同步 SQLAlchemy 2.0 + PyMySQL，路由函数用 def（FastAPI 自动丢进线程池）。
⛔ 不使用 asyncmy / aiomysql / AsyncSession，不同步异步混用。

注意：
- 数据库表结构不由 SQLAlchemy 生成，db/01_schema.sql 是唯一真相源
- 本阶段不定义任何 ORM 模型（属阶段 1）
- get_db 依赖中不 commit（事务边界归 service 层）
"""

import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 创建同步引擎
# pool_pre_ping=True: 每次取连接前先 ping，避免使用已断开的连接
engine = create_engine(
    settings.database_url,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    echo=settings.DB_ECHO,
    future=True,  # 使用 SQLAlchemy 2.0 风格 API
)

# 会话工厂
# expire_on_commit=False: commit 后对象属性不自动过期，避免额外查询
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """ORM 模型基类。

    阶段 1 的 61 张表模型将继承此类。
    本阶段不定义任何模型，仅占位。
    """


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话的依赖注入生成器。

    使用 try/yield/finally 确保会话正确关闭。
    ⛔ 不在此处 commit —— 事务边界归 service 层控制。

    Yields:
        Session: SQLAlchemy 会话实例。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> tuple[bool, int]:
    """检查数据库连通性（供健康检查接口使用）。

    执行 SELECT 1 验证连接，任何异常都被捕获（不允许抛出）。

    Returns:
        tuple[bool, int]: (是否连通, 耗时毫秒)
    """
    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return True, elapsed_ms
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.warning(f"数据库连接检查失败: {type(e).__name__}")
        return False, elapsed_ms
