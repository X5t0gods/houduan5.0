"""JWT 与 TokenPayload 的单元测试（阶段 2 CP1 交付物）。

覆盖 spec 4.1 全部要点：
- 签发/解析/过期/类型不符/签名错
- sub 字符串↔int 转换
- algorithms 显式传（防算法混淆攻击）
- 启动自检：DEBUG=False 且 JWT_SECRET_KEY 是占位值时抛异常
"""

from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    validate_jwt_config,
)


class TestTokenCreation:
    """测试令牌签发。"""

    def test_access_token_returns_tuple(self) -> None:
        """create_access_token 返回 (token, expires_in_seconds) 元组。"""
        token, expires_in = create_access_token(user_id=1, username="admin")
        assert isinstance(token, str)
        assert token.count(".") == 2  # JWT 三段式：header.payload.signature
        assert expires_in == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        # 默认 120 分钟 = 7200 秒（与前端 Mock 的 expires_in 一致）
        assert expires_in == 7200

    def test_refresh_token_returns_string(self) -> None:
        """create_refresh_token 只返回 token 字符串（不带 expires_in）。"""
        token = create_refresh_token(user_id=1, username="admin")
        assert isinstance(token, str)
        assert token.count(".") == 2

    def test_access_and_refresh_tokens_differ(self) -> None:
        """同一用户签发的 access 与 refresh token 必须不同（type 字段不同）。"""
        access, _ = create_access_token(user_id=1, username="admin")
        refresh = create_refresh_token(user_id=1, username="admin")
        assert access != refresh

    def test_jti_is_unique_per_token(self) -> None:
        """每次签发的 jti 必须不同（uuid4().hex，为将来做令牌失效预留）。"""
        t1, _ = create_access_token(user_id=1, username="admin")
        t2, _ = create_access_token(user_id=1, username="admin")
        p1 = jwt.decode(t1, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        p2 = jwt.decode(t2, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        assert p1["jti"] != p2["jti"]
        assert len(p1["jti"]) == 32  # uuid4().hex 是 32 位


class TestTokenDecoding:
    """测试令牌解析。"""

    def test_decode_access_token_success(self) -> None:
        """正常解析 access token，sub 从 str 转回 int。"""
        token, _ = create_access_token(user_id=42, username="tester")
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
        assert payload.sub == 42
        assert isinstance(payload.sub, int)  # ⚠️ 必须是 int，不是 str
        assert payload.username == "tester"
        assert payload.type == TOKEN_TYPE_ACCESS
        assert payload.iat > 0
        assert payload.exp > payload.iat
        assert len(payload.jti) == 32

    def test_decode_refresh_token_success(self) -> None:
        """正常解析 refresh token。"""
        token = create_refresh_token(user_id=7, username="refresh_user")
        payload = decode_token(token, expected_type=TOKEN_TYPE_REFRESH)
        assert payload.sub == 7
        assert payload.type == TOKEN_TYPE_REFRESH

    def test_access_token_rejected_when_refresh_expected(self) -> None:
        """⛔ 用 access token 冒充 refresh token 必须拒绝。"""
        token, _ = create_access_token(user_id=1, username="admin")
        with pytest.raises(TokenError):
            decode_token(token, expected_type=TOKEN_TYPE_REFRESH)

    def test_refresh_token_rejected_when_access_expected(self) -> None:
        """⛔ 用 refresh token 冒充 access token 必须拒绝（决策 3 关键防线）。"""
        token = create_refresh_token(user_id=1, username="admin")
        with pytest.raises(TokenError):
            decode_token(token, expected_type=TOKEN_TYPE_ACCESS)

    def test_expired_token_rejected(self) -> None:
        """过期 token 必须拒绝。

        构造方法：直接用 pyjwt 签一个 exp 在过去的 token。
        """
        past = int(time.time()) - 100
        raw = {
            "sub": "1",
            "username": "admin",
            "type": TOKEN_TYPE_ACCESS,
            "iat": past - 100,
            "exp": past,
            "jti": "expired_test",
        }
        expired_token = jwt.encode(raw, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(TokenError):
            decode_token(expired_token, expected_type=TOKEN_TYPE_ACCESS)

    def test_wrong_signature_rejected(self) -> None:
        """用错误密钥签名的 token 必须拒绝（防伪造）。"""
        raw = {
            "sub": "1",
            "username": "admin",
            "type": TOKEN_TYPE_ACCESS,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "jti": "wrong_sig_test",
        }
        forged = jwt.encode(raw, "wrong-secret-key", algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(TokenError):
            decode_token(forged, expected_type=TOKEN_TYPE_ACCESS)

    def test_malformed_token_rejected(self) -> None:
        """格式错的 token（不是三段式）必须拒绝，不抛底层异常。"""
        with pytest.raises(TokenError):
            decode_token("not.a.valid.jwt.at.all", expected_type=TOKEN_TYPE_ACCESS)
        with pytest.raises(TokenError):
            decode_token("", expected_type=TOKEN_TYPE_ACCESS)
        with pytest.raises(TokenError):
            decode_token("random_garbage", expected_type=TOKEN_TYPE_ACCESS)

    def test_token_missing_sub_rejected(self) -> None:
        """payload 缺 sub 字段必须拒绝。"""
        raw = {
            "username": "admin",
            "type": TOKEN_TYPE_ACCESS,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "jti": "no_sub_test",
        }
        token = jwt.encode(raw, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(TokenError):
            decode_token(token, expected_type=TOKEN_TYPE_ACCESS)

    def test_token_with_non_int_sub_rejected(self) -> None:
        """sub 不是可转 int 的字符串必须拒绝（防注入）。"""
        raw = {
            "sub": "not_a_number",
            "username": "admin",
            "type": TOKEN_TYPE_ACCESS,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "jti": "bad_sub_test",
        }
        token = jwt.encode(raw, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(TokenError):
            decode_token(token, expected_type=TOKEN_TYPE_ACCESS)

    def test_algorithm_confusion_attack_rejected(self) -> None:
        """⛔ 算法混淆攻击：token header 声明 alg=none 必须被拒绝。

        这就是为什么 jwt.decode 必须显式传 algorithms=[HS256] —— 不传时会接受
        header 里声明的任何算法，攻击者可以用 alg=none 绕过签名校验。
        """
        # 构造一个 alg=none 的 token（PyJWT 不允许直接签，手工拼接）
        import base64
        import json

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({
                "sub": "1", "username": "admin", "type": "access",
                "iat": int(time.time()), "exp": int(time.time()) + 3600,
                "jti": "alg_none_attack",
            }).encode()
        ).rstrip(b"=").decode()
        forged = f"{header}.{payload}."
        with pytest.raises(TokenError):
            decode_token(forged, expected_type=TOKEN_TYPE_ACCESS)


class TestTokenError:
    """测试 TokenError 异常本身。"""

    def test_default_message(self) -> None:
        """默认 message 是通用文案，不透露具体原因（防信息泄漏）。"""
        err = TokenError()
        assert err.message == "登录状态已失效，请重新登录"

    def test_custom_message(self) -> None:
        """允许自定义 message（但业务代码不应透露具体原因）。"""
        err = TokenError("自定义提示")
        assert err.message == "自定义提示"


class TestStartupValidation:
    """测试启动自检：生产环境不允许用占位密钥。"""

    def test_debug_mode_skips_check(self) -> None:
        """DEBUG=True 时不阻断（本地开发环境）。"""
        with patch.object(settings, "DEBUG", True), \
             patch.object(settings, "JWT_SECRET_KEY", "change-me-in-production"):
            # 不抛异常
            validate_jwt_config()

    def test_prod_with_placeholder_secret_raises(self) -> None:
        """DEBUG=False 且 JWT_SECRET_KEY 是占位值 → 拒绝启动。"""
        with (
            patch.object(settings, "DEBUG", False),
            patch.object(settings, "JWT_SECRET_KEY", "change-me-in-production-use-random-string"),
            pytest.raises(RuntimeError, match="JWT_SECRET_KEY 仍是占位值"),
        ):
            validate_jwt_config()

    def test_prod_with_strong_secret_passes(self) -> None:
        """DEBUG=False 且 JWT_SECRET_KEY 是强密钥 → 通过。"""
        with patch.object(settings, "DEBUG", False), \
             patch.object(settings, "JWT_SECRET_KEY", "a-very-strong-random-secret-key-32chars"):
            validate_jwt_config()

    def test_prod_with_various_placeholders_raises(self) -> None:
        """多种占位前缀都必须被识别（change-me / placeholder / your- / xxx）。"""
        placeholders = [
            "change-me-please",
            "placeholder-secret",
            "your-secret-key-here",
            "xxxxxxxxxxxxxxxx",
        ]
        for secret in placeholders:
            with patch.object(settings, "DEBUG", False), \
                 patch.object(settings, "JWT_SECRET_KEY", secret), pytest.raises(RuntimeError):
                validate_jwt_config()
