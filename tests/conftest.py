"""测试配置 - pytest fixtures。

提供 TestClient、DB 会话（两种隔离级别）与集成测试跳过标记。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, check_db_connection, engine
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
    ⚠️ **注意**：若测试代码调用了 session.commit()，rollback 无法撤销已提交的数据；
       需要真正隔离时用 isolated_db_session（SAVEPOINT 模式）。

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


@pytest.fixture
def isolated_db_session(db_available: bool) -> Generator[Session, None, None]:
    """SAVEPOINT 隔离的测试会话（需要测试 service 内部 commit 行为的用例使用）。

    工作原理：
    - 外层 connection.begin() 启动一个真事务
    - Session 用 join_transaction_mode='create_savepoint'，内部每次 commit() 变为
      SAVEPOINT 释放，外层事务仍持有
    - fixture teardown 时外层 rollback，所有测试改动一次性回滚

    ⚠️ 已知局限：AuthService._fail_login 用独立 SessionLocal() 写 sys_login_log（为了
       避免业务事务回滚把失败日志一起带走），那部分写入无法回滚。sys_login_log 是审计表
       只增不改，测试写入可接受；需严格清理时可用 cleanup_login_log fixture。

    Yields:
        SQLAlchemy Session 实例（绑定到 SAVEPOINT）。
    """
    if not db_available:
        pytest.skip("数据库不可用")
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def cleanup_login_log(db_available: bool) -> Generator[None, None, None]:
    """清理测试期间独立 Session 写入的 sys_login_log 记录。

    记录测试开始前的最大 id，测试结束后删除 id > 该值的记录。
    与 isolated_db_session 搭配使用可保证 sys_login_log 不污染。
    """
    if not db_available:
        pytest.skip("数据库不可用")
    s = SessionLocal()
    max_id_before = s.execute(text("SELECT COALESCE(MAX(id), 0) FROM sys_login_log")).scalar_one()
    s.close()
    yield
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM sys_login_log WHERE id > :mid"), {"mid": max_id_before})
        s.commit()
    finally:
        s.close()
