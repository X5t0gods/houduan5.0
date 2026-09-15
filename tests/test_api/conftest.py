"""test_api/ 专用 conftest —— 认证集成测试的公共 fixture。

⚠️ 与 test_services/test_auth_service.py 的 fixture 逻辑一致（module 快照 + 每 test 重置），
   但**作用域限定在 tests/test_api/**，不影响 test_services 与其他测试。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from app.main import app


@pytest.fixture(scope="module")
def user_snapshot() -> dict[int, dict[str, Any]]:
    """模块开始时快照 5 个演示账号的关键字段，模块结束时恢复。"""
    s = SessionLocal()
    rows = s.execute(
        text("SELECT id, password_hash, login_fail_count, locked_until, status, "
             "last_login_at, last_login_ip FROM sys_user WHERE id IN (1,2,3,4,5)")
    ).fetchall()
    snapshot = {
        row[0]: {
            "password_hash": row[1], "login_fail_count": row[2],
            "locked_until": row[3], "status": row[4],
            "last_login_at": row[5], "last_login_ip": row[6],
        }
        for row in rows
    }
    s.close()
    yield snapshot
    # 模块结束恢复
    s = SessionLocal()
    for uid, data in snapshot.items():
        s.execute(
            text("UPDATE sys_user SET password_hash=:p, login_fail_count=:c, "
                 "locked_until=:l, status=:st, last_login_at=:la, last_login_ip=:li "
                 "WHERE id=:id"),
            {
                "p": data["password_hash"], "c": data["login_fail_count"],
                "l": data["locked_until"], "st": data["status"],
                "la": data["last_login_at"], "li": data["last_login_ip"],
                "id": uid,
            },
        )
    # 清理测试期间产生的验证码
    s.execute(text("DELETE FROM sys_captcha"))
    s.commit()
    s.close()


@pytest.fixture(autouse=True)
def reset_state_before_test(user_snapshot: dict[int, dict[str, Any]]) -> Any:
    """每个测试前重置演示账号到初始状态 + 清理验证码。"""
    s = SessionLocal()
    for uid, data in user_snapshot.items():
        s.execute(
            text("UPDATE sys_user SET login_fail_count=0, locked_until=NULL, "
                 "status=:st, password_hash=:p WHERE id=:id"),
            {"st": data["status"], "p": data["password_hash"], "id": uid},
        )
    s.execute(text("DELETE FROM sys_captcha"))
    s.commit()
    s.close()
    yield


@pytest.fixture
def auth_client(user_snapshot: dict[int, dict[str, Any]]) -> TestClient:
    """带登录辅助方法的 TestClient。

    用法：
        client = auth_client
        token = login_as(client, "admin", "123456")
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    """
    return TestClient(app)


def login_as(client: TestClient, username: str, password: str = "123456") -> dict[str, Any]:
    """测试辅助函数：完整登录流程（取验证码 → 从 DB 读明文 → 登录）。

    Args:
        client: FastAPI TestClient。
        username: 账号名。
        password: 密码（默认 123456，与演示账号一致）。

    Returns:
        登录响应的 data 字段（含 access_token / refresh_token / user_profile 等）。

    Raises:
        AssertionError: 登录失败时。
    """
    # 1. 取验证码
    r = client.get("/api/v1/auth/captcha")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0, f"验证码接口失败：{body}"
    captcha_key = body["data"]["captcha_key"]

    # 2. 从 DB 读明文验证码（仅测试用；生产环境永远拿不到）
    s = SessionLocal()
    row = s.execute(
        text("SELECT captcha_code FROM sys_captcha WHERE captcha_key=:k"),
        {"k": captcha_key},
    ).fetchone()
    s.close()
    assert row is not None, "验证码未写入 DB"
    captcha_code = row[0]

    # 3. 登录
    r = client.post("/api/v1/auth/login", json={
        "username": username,
        "password": password,
        "captcha_key": captcha_key,
        "captcha_code": captcha_code,
    })
    assert r.status_code == 200, f"HTTP 状态应 200，实际 {r.status_code}"
    body = r.json()
    assert body["code"] == 0, f"登录失败：{body}"
    return body["data"]
