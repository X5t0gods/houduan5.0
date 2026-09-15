"""测试配置 - pytest fixtures。

提供 TestClient、DB 会话与集成测试跳过标记。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, check_db_connection
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """创建测试客户端。

    Returns:
        FastAPI TestClient 实例，用于发送测试请求。
    """
    return TestClient(app)


@pytest.fixture
def app_instance():
    """获取 FastAPI 应用实例（用于直接测试应用配置）。

    Returns:
        FastAPI app 实例。
    """
    return app


@pytest.fixture(scope="session")
def db_available() -> bool:
    """检查真实数据库是否可用（会话级缓存，避免每个用例都 ping）。

    Returns:
        True 表示可连通；False 表示不可用（集成测试将 skip）。
    """
    ok, _latency = check_db_connection()
    return ok


@pytest.fixture
def db_session(db_available: bool) -> Generator[Session, None, None]:
    """提供一个数据库会话（集成测试专用）。

    ⚠️ 若数据库不可用，pytest.skip 而不是失败（避免 CI 无 DB 时误报）。
    ⚠️ 每个用例结束后 rollback，保证测试之间无污染。

    Yields:
        SQLAlchemy Session 实例。
    """
    if not db_available:
        pytest.skip("数据库不可用（.env 的 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD 检查）")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
