"""AuthService 单元/集成测试（阶段 2 CP2 交付物）。

覆盖 spec 4.11 全部要点：
① 验证码：过期 / 已使用 / 大小写不敏感 / 错误码 1001
② 锁定策略：连续 4 次失败 1002、第 5 次 1003 + locked_until 已写入、
   锁定期间直接 1003、成功后计数清零
③ 校验顺序：停用账号 + 错误密码 → 1002（不泄漏停用状态）、
   停用账号 + 正确密码 → 1004
④ 权限装配：5 个账号的 permissions 与 roles（见 spec 1.1）逐条断言
⑤ 目录码不进入 permissions

⚠️ 测试策略：不使用 SAVEPOINT 隔离（会与 AuthService._fail_login 的独立 Session
   产生锁竞争），改用**module 级快照恢复**：
   - 模块开始时快照 5 个演示账号的 password_hash / login_fail_count / locked_until / status
   - 每个测试开始前重置为初始状态（autouse fixture）
   - 模块结束时恢复 password_hash（防止改密测试污染）
   - sys_captcha 与 sys_login_log 允许测试期间追加（前者每次清理，后者是审计表只增不改）
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.auth import ChangePasswordParams, LoginParams
from app.services.auth_service import AuthService


def _tz_now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ))


# ---------- Module 级快照与恢复 ----------


@pytest.fixture(scope="module")
def user_snapshot() -> dict[int, dict[str, Any]]:
    """模块开始时快照 5 个演示账号的关键字段，模块结束时恢复。

    Returns:
        {user_id: {password_hash, login_fail_count, locked_until,
                    status, last_login_at, last_login_ip}}
    """
    s = SessionLocal()
    rows = s.execute(
        text("SELECT id, password_hash, login_fail_count, locked_until, status, "
             "last_login_at, last_login_ip FROM sys_user WHERE id IN (1,2,3,4,5)")
    ).fetchall()
    snapshot = {
        row[0]: {
            "password_hash": row[1],
            "login_fail_count": row[2],
            "locked_until": row[3],
            "status": row[4],
            "last_login_at": row[5],
            "last_login_ip": row[6],
        }
        for row in rows
    }
    s.close()

    yield snapshot

    # 模块结束时恢复（防止改密测试污染演示账号）
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
    s.commit()
    s.close()


@pytest.fixture(autouse=True)
def reset_state_before_test(user_snapshot: dict[int, dict[str, Any]]) -> Any:
    """每个测试前重置演示账号到初始状态 + 清理测试期间的验证码。

    ⚠️ 包含 password_hash 重置：避免改密测试之间互相污染
       （test_change_password_same_as_old 会把密码改成 newpassword123，
       后续测试若仍用 123456 会失败）。
    """
    s = SessionLocal()
    # 重置 5 个账号的登录状态 + 密码哈希（恢复到模块开始时的快照）
    for uid, data in user_snapshot.items():
        s.execute(
            text("UPDATE sys_user SET login_fail_count=0, locked_until=NULL, "
                 "status=:st, password_hash=:p WHERE id=:id"),
            {"st": data["status"], "p": data["password_hash"], "id": uid},
        )
    # 清空验证码表（每次测试从干净状态开始）
    s.execute(text("DELETE FROM sys_captcha"))
    s.commit()
    s.close()
    yield


# ---------- 辅助工具 ----------


def _seed_captcha(session: Session, code: str = "ABCD", used: int = 0,
                  expire_offset_minutes: int = 2) -> str:
    """直接插入一条验证码记录，返回 captcha_key（不走 Pillow，测试更快）。"""
    key = uuid.uuid4().hex
    expire_at = (
        _tz_now().replace(microsecond=0, tzinfo=None)
        + timedelta(minutes=expire_offset_minutes)
    )
    session.execute(
        text("INSERT INTO sys_captcha (captcha_key, captcha_code, ip, used, expire_at) "
             "VALUES (:k, :c, '127.0.0.1', :u, :e)"),
        {"k": key, "c": code, "u": used, "e": expire_at},
    )
    session.commit()
    return key


def _set_user_status(session: Session, user_id: int, status: int) -> None:
    session.execute(
        text("UPDATE sys_user SET status=:s WHERE id=:id"),
        {"s": status, "id": user_id},
    )
    session.commit()


# ---------- 测试类 ----------


@pytest.mark.integration
class TestCaptchaValidation:
    """① 验证码校验：4 种失败场景 + 大小写不敏感成功。"""

    def test_captcha_key_not_found(self, db_session: Session) -> None:
        """captcha_key 不存在 → 1001。"""
        service = AuthService(db_session)
        params = LoginParams(
            username="admin", password="123456",
            captcha_key="nonexistent_key", captcha_code="ABCD",
        )
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.CAPTCHA_INVALID

    def test_captcha_already_used(self, db_session: Session) -> None:
        """captcha.used=1 → 1001（一 key 只能用一次）。"""
        key = _seed_captcha(db_session, code="ABCD", used=1)
        service = AuthService(db_session)
        params = LoginParams(
            username="admin", password="123456",
            captcha_key=key, captcha_code="ABCD",
        )
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.CAPTCHA_INVALID

    def test_captcha_expired(self, db_session: Session) -> None:
        """captcha 已过期 → 1001。"""
        key = _seed_captcha(db_session, code="ABCD", expire_offset_minutes=-1)
        service = AuthService(db_session)
        params = LoginParams(
            username="admin", password="123456",
            captcha_key=key, captcha_code="ABCD",
        )
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.CAPTCHA_INVALID

    def test_captcha_code_mismatch(self, db_session: Session) -> None:
        """captcha_code 不匹配 → 1001。"""
        key = _seed_captcha(db_session, code="ABCD")
        service = AuthService(db_session)
        params = LoginParams(
            username="admin", password="123456",
            captcha_key=key, captcha_code="WXYZ",
        )
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.CAPTCHA_INVALID

    def test_captcha_case_insensitive(self, db_session: Session) -> None:
        """⚠️ 验证码大小写不敏感：DB 存 ABCD，用户输入 abcd 也应通过。"""
        key = _seed_captcha(db_session, code="ABCD")
        service = AuthService(db_session)
        params = LoginParams(
            username="admin", password="123456",
            captcha_key=key, captcha_code="abcd",
        )
        result = service.login(params, "127.0.0.1", "pytest")
        assert result.token_type == "Bearer"
        assert result.expires_in == 7200
        assert result.user_profile.username == "admin"


@pytest.mark.integration
class TestLoginLockout:
    """② 锁定策略：spec 4.3 步骤 3-4 的完整行为。"""

    def test_first_4_failures_return_1002(self, db_session: Session) -> None:
        """连续 4 次错误密码：每次都返回 1002，计数递增但未锁定。"""
        service = AuthService(db_session)

        for i in range(4):
            key = _seed_captcha(db_session, code="ABCD")
            params = LoginParams(
                username="admin", password="wrong_password",
                captcha_key=key, captcha_code="ABCD",
            )
            with pytest.raises(BusinessError) as exc:
                service.login(params, "127.0.0.1", "pytest")
            assert exc.value.code == ErrorCode.LOGIN_FAILED, f"第 {i+1} 次应为 1002"

            cnt = db_session.execute(
                text("SELECT login_fail_count FROM sys_user WHERE id=1")
            ).scalar_one()
            assert cnt == i + 1

        locked_until = db_session.execute(
            text("SELECT locked_until FROM sys_user WHERE id=1")
        ).scalar_one()
        assert locked_until is None

    def test_5th_failure_triggers_lockout(self, db_session: Session) -> None:
        """第 5 次错误密码：返回 1003 且 locked_until 已写入。"""
        service = AuthService(db_session)

        for _ in range(4):
            key = _seed_captcha(db_session, code="ABCD")
            params = LoginParams(
                username="admin", password="wrong", captcha_key=key, captcha_code="ABCD",
            )
            with pytest.raises(BusinessError):
                service.login(params, "127.0.0.1", "pytest")

        key = _seed_captcha(db_session, code="ABCD")
        params = LoginParams(
            username="admin", password="wrong", captcha_key=key, captcha_code="ABCD",
        )
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.ACCOUNT_LOCKED

        row = db_session.execute(
            text("SELECT login_fail_count, locked_until FROM sys_user WHERE id=1")
        ).fetchone()
        assert row[0] == 5
        assert row[1] is not None
        locked_aware = row[1].replace(tzinfo=ZoneInfo(settings.TZ))
        assert locked_aware > _tz_now()
        assert locked_aware < _tz_now() + timedelta(minutes=16)

    def test_login_during_lockout_returns_1003(self, db_session: Session) -> None:
        """锁定期间即使密码正确也返回 1003（不再累加计数）。"""
        # 手工设置锁定状态
        future = (_tz_now() + timedelta(minutes=10)).replace(tzinfo=None, microsecond=0)
        db_session.execute(
            text("UPDATE sys_user SET locked_until=:t, login_fail_count=5 WHERE id=1"),
            {"t": future},
        )
        db_session.commit()

        key = _seed_captcha(db_session, code="ABCD")
        # ⚠️ 用**正确密码**登录，仍应被锁定拦截
        params = LoginParams(
            username="admin", password="123456", captcha_key=key, captcha_code="ABCD",
        )
        service = AuthService(db_session)
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.ACCOUNT_LOCKED

        cnt = db_session.execute(
            text("SELECT login_fail_count FROM sys_user WHERE id=1")
        ).scalar_one()
        assert cnt == 5  # 锁定期间不再累加

    def test_successful_login_resets_counter(self, db_session: Session) -> None:
        """成功登录后 login_fail_count 归零、locked_until 清空。"""
        service = AuthService(db_session)
        for _ in range(3):
            key = _seed_captcha(db_session, code="ABCD")
            params = LoginParams(
                username="admin", password="wrong", captcha_key=key, captcha_code="ABCD",
            )
            with pytest.raises(BusinessError):
                service.login(params, "127.0.0.1", "pytest")

        cnt = db_session.execute(
            text("SELECT login_fail_count FROM sys_user WHERE id=1")
        ).scalar_one()
        assert cnt == 3

        key = _seed_captcha(db_session, code="ABCD")
        params = LoginParams(
            username="admin", password="123456", captcha_key=key, captcha_code="ABCD",
        )
        result = service.login(params, "127.0.0.1", "pytest")
        assert result.access_token

        row = db_session.execute(
            text("SELECT login_fail_count, locked_until, last_login_at FROM sys_user WHERE id=1")
        ).fetchone()
        assert row[0] == 0
        assert row[1] is None
        assert row[2] is not None


@pytest.mark.integration
class TestValidationOrder:
    """③ 校验顺序：spec 4.3 强调的两个反枚举设计。"""

    def test_disabled_user_wrong_password_returns_1002(self, db_session: Session) -> None:
        """⚠️ 停用账号 + 错误密码 → 1002（**不是 1004**，防止通过错误码枚举停用状态）。"""
        _set_user_status(db_session, 2, status=0)  # manager01 停用
        key = _seed_captcha(db_session, code="ABCD")
        params = LoginParams(
            username="manager01", password="wrong", captcha_key=key, captcha_code="ABCD",
        )
        service = AuthService(db_session)
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        # 密码校验先执行 → 1002
        assert exc.value.code == ErrorCode.LOGIN_FAILED

    def test_disabled_user_correct_password_returns_1004(self, db_session: Session) -> None:
        """停用账号 + 正确密码 → 1004（密码校验通过后才判断停用）。"""
        _set_user_status(db_session, 2, status=0)
        key = _seed_captcha(db_session, code="ABCD")
        params = LoginParams(
            username="manager01", password="123456", captcha_key=key, captcha_code="ABCD",
        )
        service = AuthService(db_session)
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.ACCOUNT_DISABLED

    def test_nonexistent_user_returns_1002_not_1004(self, db_session: Session) -> None:
        """用户不存在 → 1002（与密码错误共用文案，防账号枚举）。"""
        key = _seed_captcha(db_session, code="ABCD")
        params = LoginParams(
            username="user_that_does_not_exist", password="anything",
            captcha_key=key, captcha_code="ABCD",
        )
        service = AuthService(db_session)
        with pytest.raises(BusinessError) as exc:
            service.login(params, "127.0.0.1", "pytest")
        # ⚠️ 不能返回 1004（会泄漏"此账号存在但停用"vs"此账号不存在"的差异）
        assert exc.value.code == ErrorCode.LOGIN_FAILED


@pytest.mark.integration
class TestPermissionAssembly:
    """④⑤ 权限装配：5 个账号的 permissions 与 roles 逐条断言（spec 1.1 权威表）。"""

    def test_admin_permissions_is_wildcard(self, db_session: Session) -> None:
        """admin: permissions = ["*"] 通配符（**不是** 32 个码），roles = ["系统管理员"]。"""
        service = AuthService(db_session)
        profile = service.get_user_profile(user_id=1)
        assert profile.username == "admin"
        assert profile.real_name == "徐炜城"
        assert profile.permissions == ["*"]
        assert profile.roles == ["系统管理员"]
        assert profile.store_name is not None

    def test_manager_permissions_count(self, db_session: Session) -> None:
        """manager01 (MANAGER): 26 个功能权限码（29 - 3 sys:*），roles = ["店长"]。"""
        service = AuthService(db_session)
        profile = service.get_user_profile(user_id=2)
        assert profile.username == "manager01"
        assert profile.roles == ["店长"]
        assert len(profile.permissions) == 26
        assert "sys:user" not in profile.permissions
        assert "sys:role" not in profile.permissions
        assert "sys:config" not in profile.permissions
        assert "goods:product:edit" in profile.permissions
        assert "pos:use" in profile.permissions

    def test_cashier_permissions_count(self, db_session: Session) -> None:
        """cashier01 (CASHIER): 6 个功能权限码，roles = ["收银员"]。"""
        service = AuthService(db_session)
        profile = service.get_user_profile(user_id=3)
        assert profile.username == "cashier01"
        assert profile.roles == ["收银员"]
        assert len(profile.permissions) == 6
        expected = {
            "dashboard:view", "pos:use", "pos:session",
            "member:list", "member:detail", "promo:list",
        }
        assert set(profile.permissions) == expected

    def test_stocker_permissions_count(self, db_session: Session) -> None:
        """stocker01 (STOCKER): 9 个功能权限码，roles = ["库管员"]。"""
        service = AuthService(db_session)
        profile = service.get_user_profile(user_id=4)
        assert profile.username == "stocker01"
        assert profile.roles == ["库管员"]
        assert len(profile.permissions) == 9
        expected = {
            "dashboard:view", "goods:product:list",
            "pur:order:list", "pur:receipt",
            "inv:stock:list", "inv:flow", "inv:check", "inv:loss", "inv:transfer",
        }
        assert set(profile.permissions) == expected

    def test_buyer_multi_role_union(self, db_session: Session) -> None:
        """⚠️ buyer01 (PURCHASER + MANAGER 一人多岗)：26 个权限码（并集），
        roles = ["店长", "采购员"]（按 sys_role.sort 升序：MANAGER sort=2, PURCHASER sort=5）。
        """
        service = AuthService(db_session)
        profile = service.get_user_profile(user_id=5)
        assert profile.username == "buyer01"
        assert profile.real_name == "张小美"
        # ⚠️ roles 按 sort 升序 → MANAGER(sort=2) 在前，PURCHASER(sort=5) 在后
        assert profile.roles == ["店长", "采购员"]
        assert len(profile.permissions) == 26
        assert "pur:order:create" in profile.permissions
        assert "pur:supplier" in profile.permissions
        assert "pos:use" in profile.permissions
        assert "sys:user" not in profile.permissions

    def test_directories_never_leak_into_permissions(self, db_session: Session) -> None:
        """⑤ 目录码 (menu:daily / menu:stock / menu:analysis) 绝不进入 permissions。"""
        service = AuthService(db_session)
        directory_codes = {"menu:daily", "menu:stock", "menu:analysis"}
        # 检查所有 5 个账号（admin 是 ["*"] 特殊，跳过）
        for user_id in (2, 3, 4, 5):
            profile = service.get_user_profile(user_id=user_id)
            leaked = directory_codes & set(profile.permissions)
            assert not leaked, f"user_id={user_id} 泄漏了目录码：{leaked}"

    def test_all_roles_have_expected_names(self, db_session: Session) -> None:
        """⚠️ roles 是**中文角色名**（不是编码），spec 1.2 冲突 ② 的关键防线。"""
        service = AuthService(db_session)
        expected_roles = {
            1: ["系统管理员"],
            2: ["店长"],
            3: ["收银员"],
            4: ["库管员"],
            5: ["店长", "采购员"],
        }
        for user_id, expected in expected_roles.items():
            profile = service.get_user_profile(user_id=user_id)
            assert profile.roles == expected, (
                f"user_id={user_id} roles 应为 {expected}，实际 {profile.roles}"
            )
            for role in profile.roles:
                assert role not in ("ADMIN", "MANAGER", "CASHIER", "STOCKER", "PURCHASER")


@pytest.mark.integration
class TestRefreshAndChangePassword:
    """刷新令牌与修改密码的关键路径。"""

    def test_refresh_rejects_access_token(self, db_session: Session) -> None:
        """⛔ 用 access token 冒充 refresh token 必须拒绝（spec 4.5）。"""
        from app.core.security import TokenError, create_access_token

        access, _ = create_access_token(user_id=1, username="admin")
        service = AuthService(db_session)
        with pytest.raises(TokenError):
            service.refresh(access)

    def test_refresh_disabled_user_rejected(self, db_session: Session) -> None:
        """已停用用户不允许刷新（否则停用形同虚设，spec 4.5）。"""
        from app.core.security import create_refresh_token

        _set_user_status(db_session, 2, status=0)
        refresh = create_refresh_token(user_id=2, username="manager01")
        service = AuthService(db_session)
        with pytest.raises(BusinessError) as exc:
            service.refresh(refresh)
        assert exc.value.code == ErrorCode.ACCOUNT_DISABLED

    def test_refresh_success_returns_only_two_fields(self, db_session: Session) -> None:
        """刷新成功返回**只有** access_token 与 expires_in 两个字段（spec 4.5）。"""
        from app.core.security import create_refresh_token

        refresh = create_refresh_token(user_id=1, username="admin")
        service = AuthService(db_session)
        result = service.refresh(refresh)
        dumped = result.model_dump()
        assert set(dumped.keys()) == {"access_token", "expires_in"}
        assert dumped["expires_in"] == 7200

    def test_change_password_wrong_old_returns_1002(self, db_session: Session) -> None:
        """原密码错误 → 1002（spec 4.7 补充约定）。"""
        service = AuthService(db_session)
        params = ChangePasswordParams(old_password="wrong", new_password="newpassword123")
        with pytest.raises(BusinessError) as exc:
            service.change_password(1, params, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.LOGIN_FAILED

    def test_change_password_same_as_old_returns_2001(self, db_session: Session) -> None:
        """新旧密码相同 → 2001（防"改了个寂寞"）。"""
        service = AuthService(db_session)
        # 先改一次让密码变成 newpassword123
        params1 = ChangePasswordParams(old_password="123456", new_password="newpassword123")
        service.change_password(1, params1, "127.0.0.1", "pytest")
        # 再改成同样
        params2 = ChangePasswordParams(old_password="newpassword123", new_password="newpassword123")
        with pytest.raises(BusinessError) as exc:
            service.change_password(1, params2, "127.0.0.1", "pytest")
        assert exc.value.code == ErrorCode.REQUIRED_MISSING

    def test_change_password_success_and_old_hash_not_in_log(self, db_session: Session) -> None:
        """改密成功后：新密码可校验，sys_operation_log 里**不含密码哈希**（spec 4.7）。"""
        from app.core.security import verify_password

        original_hash = db_session.execute(
            text("SELECT password_hash FROM sys_user WHERE id=1")
        ).scalar_one()

        service = AuthService(db_session)
        params = ChangePasswordParams(old_password="123456", new_password="newpassword123")
        service.change_password(1, params, "127.0.0.1", "pytest")

        new_hash = db_session.execute(
            text("SELECT password_hash FROM sys_user WHERE id=1")
        ).scalar_one()
        assert new_hash != original_hash
        assert verify_password("newpassword123", new_hash)
        assert not verify_password("123456", new_hash)

        # 查 sys_operation_log 最新一条"修改密码"记录
        row = db_session.execute(
            text("SELECT module, action, before_value, after_value FROM sys_operation_log "
                 "WHERE target_type='sys_user' AND target_id=1 AND action='修改密码' "
                 "ORDER BY id DESC LIMIT 1")
        ).fetchone()
        assert row is not None
        assert row[0] == "认证"
        before_str = str(row[2]) if row[2] else ""
        after_str = str(row[3]) if row[3] else ""
        # ⛔ before_value / after_value 里绝不能出现 bcrypt 哈希（$2b$ 前缀）
        assert "$2b$" not in before_str, f"before_value 泄漏密码哈希：{before_str}"
        assert "$2b$" not in after_str, f"after_value 泄漏密码哈希：{after_str}"
        assert original_hash not in before_str
        assert new_hash not in after_str
