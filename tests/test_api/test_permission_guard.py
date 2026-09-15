"""401/403 权限边界测试（阶段 2 CP4 交付物）。

覆盖 spec 4.11 test_api/test_permission_guard.py + 验收项 12/13/14：
- 无 token / 伪造 token / 篡改 token / refresh 冒充 access → 401
- cashier01（无 sys:user）访问受保护路由 → 403 + code=1040
- admin 访问同一路由 → 200
- DEBUG=false 时 /_dev/authorized 必须 404
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import login_as


@pytest.mark.integration
class TestUnauthorized401:
    """401 边界：spec 验收项 12。"""

    def test_no_token_returns_401(self, auth_client: TestClient) -> None:
        """完全不带 Authorization 头 → HTTP 401。"""
        r = auth_client.get("/api/v1/_dev/authorized")
        assert r.status_code == 401
        body = r.json()
        # 401 响应体也走统一结构（阶段 0 决策）
        assert "code" in body
        assert "message" in body
        assert "request_id" in body
        assert "timestamp" in body

    def test_malformed_authorization_header_returns_401(
        self, auth_client: TestClient,
    ) -> None:
        """Authorization 头格式错（不是 Bearer）→ 401。"""
        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},  # Basic 而非 Bearer
        )
        assert r.status_code == 401

    def test_empty_bearer_returns_401(self, auth_client: TestClient) -> None:
        """Bearer 后为空 → 401。"""
        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": "Bearer "},
        )
        assert r.status_code == 401

    def test_forged_token_returns_401(self, auth_client: TestClient) -> None:
        """完全伪造的 token → 401（签名验证失败）。"""
        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert r.status_code == 401

    def test_tampered_token_returns_401(self, auth_client: TestClient) -> None:
        """spec 验收项 12：真实 token 末尾改一个字符 → 401（签名不匹配）。"""
        data = login_as(auth_client, "admin")
        token = data["access_token"]
        # 篡改最后一个字符
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        assert r.status_code == 401

    def test_refresh_token_as_access_returns_401(self, auth_client: TestClient) -> None:
        """spec 验收项 12：用 refresh_token 当 access 用 → 401。"""
        data = login_as(auth_client, "admin")
        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": f"Bearer {data['refresh_token']}"},
        )
        assert r.status_code == 401

    def test_refresh_endpoint_rejects_access_token(self, auth_client: TestClient) -> None:
        """spec 验收项 12：POST /auth/refresh 只接受 refresh_token。"""
        data = login_as(auth_client, "admin")
        r = auth_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": data["access_token"]},  # 传 access 冒充
        )
        assert r.status_code == 401


@pytest.mark.integration
class TestForbidden403:
    """403 边界：spec 验收项 13。"""

    def test_cashier_access_sys_user_route_returns_403_code_1040(
        self, auth_client: TestClient,
    ) -> None:
        """⚠️ cashier01（无 sys:user 权限）访问 /_dev/authorized → HTTP 403 + code=1040。"""
        data = login_as(auth_client, "cashier01")
        # 断言 cashier01 确实没有 sys:user 权限
        assert "sys:user" not in data["user_profile"]["permissions"]

        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert r.status_code == 403
        body = r.json()
        # ⚠️ spec 4.8 明确：body code 必须是 1040（不是 403）
        assert body["code"] == 1040
        assert body["message"] == "没有该操作的权限"
        assert body["data"] is None
        assert "request_id" in body

    def test_admin_access_sys_user_route_returns_200(self, auth_client: TestClient) -> None:
        """admin（通配符）访问同一路由 → HTTP 200 + {ok: true}。"""
        data = login_as(auth_client, "admin")
        assert data["user_profile"]["permissions"] == ["*"]

        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"] == {"ok": True}

    def test_manager_access_sys_user_route_returns_403(self, auth_client: TestClient) -> None:
        """manager01（无 sys:user 权限，只有 MANAGER 角色）访问 → 403 + code=1040。"""
        data = login_as(auth_client, "manager01")
        assert "sys:user" not in data["user_profile"]["permissions"]

        r = auth_client.get(
            "/api/v1/_dev/authorized",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == 1040


@pytest.mark.integration
class TestDisabledUser:
    """停用用户的边界行为。"""

    def test_disabled_user_login_returns_1004(self, auth_client: TestClient) -> None:
        """停用账号 + 正确密码 → HTTP 200 + code=1004。"""
        # 手工把 manager01 停用
        from sqlalchemy import text

        from app.core.database import SessionLocal
        s = SessionLocal()
        s.execute(text("UPDATE sys_user SET status=0 WHERE id=2"))
        s.commit()
        s.close()

        # 取验证码
        r = auth_client.get("/api/v1/auth/captcha")
        key = r.json()["data"]["captcha_key"]
        s = SessionLocal()
        code = s.execute(
            text("SELECT captcha_code FROM sys_captcha WHERE captcha_key=:k"), {"k": key}
        ).scalar_one()
        s.close()

        r = auth_client.post("/api/v1/auth/login", json={
            "username": "manager01", "password": "123456",
            "captcha_key": key, "captcha_code": code,
        })
        # ⛔ HTTP 状态必须 200（不是 403 或 401）
        assert r.status_code == 200
        assert r.json()["code"] == 1004

    def test_disabled_user_existing_token_returns_401(self, auth_client: TestClient) -> None:
        """已登录用户被停用后，其 access_token 立即失效 → 401。"""
        # 先正常登录 admin
        data = login_as(auth_client, "admin")
        token = data["access_token"]

        # 停用 admin
        from sqlalchemy import text

        from app.core.database import SessionLocal
        s = SessionLocal()
        s.execute(text("UPDATE sys_user SET status=0 WHERE id=1"))
        s.commit()
        s.close()

        # 用之前的 token 访问 → 401（get_current_user 会检查 status）
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
