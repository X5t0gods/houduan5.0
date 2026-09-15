"""6 个 auth 接口的完整链路集成测试（阶段 2 CP4 交付物）。

覆盖 spec 4.11 test_api/test_auth.py 要求：
- 完整链路：GET /auth/captcha → 从 sys_captcha 读明文 → POST /auth/login → GET /auth/me
- 5 个账号权限断言（含 admin 通配符、buyer01 一人多岗）
- token_type="Bearer"、expires_in=7200、store_name 中文
- POST /auth/refresh 换新 token
- POST /auth/password 改密后能用新密码登录、旧密码失败
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from tests.test_api.conftest import login_as


@pytest.mark.integration
class TestCaptchaEndpoint:
    """GET /auth/captcha 接口测试。"""

    def test_captcha_returns_key_and_image(self, auth_client: TestClient) -> None:
        """验证码接口返回 captcha_key（UUID hex）与 Base64 Data URL。"""
        r = auth_client.get("/api/v1/auth/captcha")
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        data = body["data"]
        assert len(data["captcha_key"]) == 32  # uuid4().hex
        assert data["captcha_image"].startswith("data:image/png;base64,")
        # PNG base64 应至少几百字节
        assert len(data["captcha_image"]) > 500

    def test_captcha_persists_to_db(self, auth_client: TestClient) -> None:
        """验证码写入 sys_captcha 表，expire_at 在 2 分钟内。"""
        r = auth_client.get("/api/v1/auth/captcha")
        key = r.json()["data"]["captcha_key"]

        s = SessionLocal()
        row = s.execute(
            text("SELECT captcha_code, used, expire_at FROM sys_captcha WHERE captcha_key=:k"),
            {"k": key},
        ).fetchone()
        s.close()

        assert row is not None
        captcha_code, used, expire_at = row
        assert len(captcha_code) == 4
        assert used == 0
        # expire_at 应在未来 1-3 分钟内
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from app.core.config import settings
        now = datetime.now(ZoneInfo(settings.TZ)).replace(tzinfo=None)
        assert now < expire_at < now + timedelta(minutes=3)


@pytest.mark.integration
class TestLoginEndpoint:
    """POST /auth/login 完整链路测试。"""

    def test_admin_login_full_flow(self, auth_client: TestClient) -> None:
        """admin 完整登录链路：captcha → login → me。"""
        data = login_as(auth_client, "admin")

        # LoginResult 5 字段
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 7200  # 120 分钟
        assert data["access_token"].count(".") == 2  # JWT 三段式
        assert data["refresh_token"].count(".") == 2
        assert data["access_token"] != data["refresh_token"]

        # UserProfile
        profile = data["user_profile"]
        assert profile["id"] == 1
        assert profile["username"] == "admin"
        assert profile["real_name"] == "徐炜城"
        assert profile["store_name"] == "朝阳路店"
        assert profile["roles"] == ["系统管理员"]
        # ⚠️ ADMIN 通配符（spec 1.2 冲突 ①）
        assert profile["permissions"] == ["*"]

        # 用返回的 token 调 /auth/me，字段应完全一致
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert r.status_code == 200
        me_body = r.json()
        assert me_body["code"] == 0
        assert me_body["data"] == profile

    def test_5_accounts_all_login_successfully(self, auth_client: TestClient) -> None:
        """spec 验收项 3：5 个演示账号用 123456 全部登录成功。"""
        for username in ("admin", "manager01", "cashier01", "stocker01", "buyer01"):
            data = login_as(auth_client, username)
            assert data["token_type"] == "Bearer"
            assert data["expires_in"] == 7200
            assert data["user_profile"]["username"] == username

    def test_login_wrong_password_returns_200_code_1002(
        self, auth_client: TestClient,
    ) -> None:
        """⚠️ spec 验收项 8：错误密码必须 HTTP 200 + code=1002，**不能返回 401**。"""
        # 先取验证码
        r = auth_client.get("/api/v1/auth/captcha")
        key = r.json()["data"]["captcha_key"]
        s = SessionLocal()
        code = s.execute(
            text("SELECT captcha_code FROM sys_captcha WHERE captcha_key=:k"), {"k": key}
        ).scalar_one()
        s.close()

        r = auth_client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "wrong_password",
            "captcha_key": key, "captcha_code": code,
        })
        # ⛔ HTTP 状态必须 200，不能是 401（否则前端会踢用户）
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 1002
        assert body["message"] == "用户名或密码错误"
        assert body["data"] is None

    def test_login_captcha_one_time_use(self, auth_client: TestClient) -> None:
        """spec 验收项 2：同一 captcha_key 登录两次，第二次返回 1001（HTTP 200）。"""
        # 第一次登录成功
        data = login_as(auth_client, "admin")
        assert data["access_token"]

        # 用同一 captcha_key 再登录 —— 但 login_as 内部会生成新验证码
        # 所以要手工复现：先取一个新 key，成功登录后再用同 key 尝试
        r = auth_client.get("/api/v1/auth/captcha")
        key = r.json()["data"]["captcha_key"]
        s = SessionLocal()
        code = s.execute(
            text("SELECT captcha_code FROM sys_captcha WHERE captcha_key=:k"), {"k": key}
        ).scalar_one()
        s.close()

        # 第一次成功
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "manager01", "password": "123456",
            "captcha_key": key, "captcha_code": code,
        })
        assert r.json()["code"] == 0

        # 第二次用同 key（应报 1001，HTTP 200）
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "manager01", "password": "123456",
            "captcha_key": key, "captcha_code": code,
        })
        assert r.status_code == 200
        assert r.json()["code"] == 1001


@pytest.mark.integration
class TestMeEndpoint:
    """GET /auth/me 权限装配测试（spec 验收项 4/5/6/7）。"""

    def test_admin_permissions_wildcard(self, auth_client: TestClient) -> None:
        """spec 验收项 4：admin.permissions == ['*']（**不是** 32 个码）。"""
        data = login_as(auth_client, "admin")
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert r.json()["data"]["permissions"] == ["*"]

    def test_manager_permissions_26(self, auth_client: TestClient) -> None:
        """spec 验收项 5：manager01.permissions 数量 = 26。"""
        data = login_as(auth_client, "manager01")
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        perms = r.json()["data"]["permissions"]
        assert len(perms) == 26
        assert "sys:user" not in perms  # MANAGER 不含 sys:*

    def test_cashier_permissions_6(self, auth_client: TestClient) -> None:
        """spec 验收项 5：cashier01.permissions 数量 = 6。"""
        data = login_as(auth_client, "cashier01")
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        perms = r.json()["data"]["permissions"]
        assert len(perms) == 6
        assert set(perms) == {
            "dashboard:view", "pos:use", "pos:session",
            "member:list", "member:detail", "promo:list",
        }

    def test_stocker_permissions_9(self, auth_client: TestClient) -> None:
        """spec 验收项 5：stocker01.permissions 数量 = 9。"""
        data = login_as(auth_client, "stocker01")
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        perms = r.json()["data"]["permissions"]
        assert len(perms) == 9
        assert set(perms) == {
            "dashboard:view", "goods:product:list",
            "pur:order:list", "pur:receipt",
            "inv:stock:list", "inv:flow", "inv:check", "inv:loss", "inv:transfer",
        }

    def test_buyer_multi_role_26(self, auth_client: TestClient) -> None:
        """spec 验收项 5：buyer01（PURCHASER+MANAGER 一人多岗）permissions 数量 = 26。"""
        data = login_as(auth_client, "buyer01")
        r = auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        body = r.json()["data"]
        assert len(body["permissions"]) == 26
        # ⚠️ spec 验收项 6：roles 是**中文角色名**，按 sort 升序
        assert body["roles"] == ["店长", "采购员"]

    def test_no_directory_codes_in_permissions(self, auth_client: TestClient) -> None:
        """spec 验收项 7：目录码 menu:* 绝不进入 permissions。"""
        directory_codes = {"menu:daily", "menu:stock", "menu:analysis"}
        for username in ("manager01", "cashier01", "stocker01", "buyer01"):
            data = login_as(auth_client, username)
            r = auth_client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {data['access_token']}"},
            )
            perms = set(r.json()["data"]["permissions"])
            leaked = directory_codes & perms
            assert not leaked, f"{username} 泄漏目录码：{leaked}"


@pytest.mark.integration
class TestRefreshEndpoint:
    """POST /auth/refresh 测试。"""

    def test_refresh_returns_new_access_token(self, auth_client: TestClient) -> None:
        """刷新成功：返回新 access_token 与 expires_in=7200，只有两个字段。"""
        data = login_as(auth_client, "admin")
        r = auth_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": data["refresh_token"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        # ⚠️ spec 4.5：只有两个字段
        assert set(body["data"].keys()) == {"access_token", "expires_in"}
        assert body["data"]["expires_in"] == 7200
        # 新 access_token 与旧的不同（jti 不同）
        assert body["data"]["access_token"] != data["access_token"]

    def test_refresh_with_access_token_rejected(self, auth_client: TestClient) -> None:
        """⛔ 用 access_token 冒充 refresh_token 必须 401。"""
        data = login_as(auth_client, "admin")
        r = auth_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": data["access_token"]},
        )
        assert r.status_code == 401


@pytest.mark.integration
class TestLogoutEndpoint:
    """POST /auth/logout 测试（一期为空操作）。"""

    def test_logout_returns_success(self, auth_client: TestClient) -> None:
        """登出返回 code=0，data=null。"""
        data = login_as(auth_client, "admin")
        r = auth_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"] is None

    def test_logout_without_token_returns_401(self, auth_client: TestClient) -> None:
        """未登录时登出返回 401。"""
        r = auth_client.post("/api/v1/auth/logout")
        assert r.status_code == 401


@pytest.mark.integration
class TestChangePasswordEndpoint:
    """POST /auth/password 测试。"""

    def test_change_password_full_cycle(self, auth_client: TestClient) -> None:
        """改密闭环：改密成功 → 新密码可登录 → 旧密码 1002。"""
        # 1. admin 登录
        data = login_as(auth_client, "admin")
        token = data["access_token"]

        # 2. 改密
        r = auth_client.post(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"old_password": "123456", "new_password": "newpassword123"},
        )
        assert r.status_code == 200
        assert r.json()["code"] == 0

        # 3. 用新密码登录成功
        data2 = login_as(auth_client, "admin", password="newpassword123")
        assert data2["access_token"]

        # 4. 用旧密码登录失败（1002）
        r = auth_client.get("/api/v1/auth/captcha")
        key = r.json()["data"]["captcha_key"]
        s = SessionLocal()
        code = s.execute(
            text("SELECT captcha_code FROM sys_captcha WHERE captcha_key=:k"), {"k": key}
        ).scalar_one()
        s.close()
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "123456",
            "captcha_key": key, "captcha_code": code,
        })
        assert r.status_code == 200
        assert r.json()["code"] == 1002

    def test_change_password_short_new_rejected(self, auth_client: TestClient) -> None:
        """新密码 <8 位 → Pydantic 校验拦截（HTTP 200 + code=2001）。"""
        data = login_as(auth_client, "admin")
        r = auth_client.post(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {data['access_token']}"},
            json={"old_password": "123456", "new_password": "short"},
        )
        assert r.status_code == 200
        assert r.json()["code"] == 2001  # RequestValidationError 转 2001

    def test_change_password_wrong_old_returns_1002(self, auth_client: TestClient) -> None:
        """原密码错误 → 1002（spec 4.7 补充约定）。"""
        data = login_as(auth_client, "admin")
        r = auth_client.post(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {data['access_token']}"},
            json={"old_password": "wrong_old", "new_password": "newpassword123"},
        )
        assert r.status_code == 200
        assert r.json()["code"] == 1002
