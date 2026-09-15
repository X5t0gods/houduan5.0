"""安全模块测试 - bcrypt 哈希与校验。

验证：
- hash_password 生成有效的 bcrypt 哈希
- verify_password 正确校验密码
- verify_password 对非法哈希返回 False（不抛异常）
- validate_password_strength 校验密码强度
"""

import pytest

from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.security import hash_password, validate_password_strength, verify_password


class TestPasswordHashing:
    """测试密码哈希与校验。"""

    def test_hash_password_produces_bcrypt_format(self) -> None:
        """hash_password 必须生成 $2b$ 格式的 bcrypt 哈希。"""
        hashed = hash_password("123456")

        assert hashed.startswith("$2b$")
        # bcrypt cost=10 的哈希长度固定为 60
        assert len(hashed) == 60

    def test_verify_password_correct(self) -> None:
        """正确密码校验必须返回 True。"""
        hashed = hash_password("123456")
        assert verify_password("123456", hashed) is True

    def test_verify_password_incorrect(self) -> None:
        """错误密码校验必须返回 False。"""
        hashed = hash_password("123456")
        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_invalid_hash_returns_false(self) -> None:
        """非法哈希格式必须返回 False，不抛异常。"""
        # 测试各种非法哈希
        assert verify_password("123456", "invalid_hash") is False
        assert verify_password("123456", "") is False
        assert verify_password("123456", "$2b$10$truncated") is False

    def test_hash_password_different_salts(self) -> None:
        """相同密码多次哈希必须产生不同结果（盐值随机）。"""
        hash1 = hash_password("123456")
        hash2 = hash_password("123456")

        assert hash1 != hash2
        # 但都能校验通过
        assert verify_password("123456", hash1) is True
        assert verify_password("123456", hash2) is True


class TestPasswordStrength:
    """测试密码强度校验。"""

    def test_valid_password_passes(self) -> None:
        """合法密码（>=6位，无空白）不抛异常。"""
        # 不抛异常即为通过
        validate_password_strength("123456")
        validate_password_strength("abc123")
        validate_password_strength("a1b2c3d4")

    def test_short_password_raises(self) -> None:
        """密码少于 6 位必须抛 BusinessError(2001)。"""
        with pytest.raises(BusinessError) as exc_info:
            validate_password_strength("12345")

        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING

    def test_password_with_whitespace_raises(self) -> None:
        """密码含空白字符必须抛 BusinessError(2001)。"""
        with pytest.raises(BusinessError) as exc_info:
            validate_password_strength("123 456")

        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING

    def test_password_with_tab_raises(self) -> None:
        """密码含制表符必须抛 BusinessError(2001)。"""
        with pytest.raises(BusinessError) as exc_info:
            validate_password_strength("123\t456")

        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING
