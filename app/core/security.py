"""安全模块 - 密码哈希与校验。

技术决策：
- ⛔ 不引入 passlib（已停止维护，与 bcrypt >= 4.0 不兼容）
- 直接使用 bcrypt 库的 hashpw / checkpw
- cost=10 必须与 db/02_init_data.sql 里已有的密码哈希一致
- JWT 签发与解析留到阶段 2，本阶段不写
"""

import bcrypt

from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError


def hash_password(plain: str) -> str:
    """对明文密码进行 bcrypt 哈希。

    使用 cost=10（与 db/02_init_data.sql 中已有的密码哈希一致），
    确保演示账号（密码统一 123456）可直接被 checkpw 校验。

    Args:
        plain: 明文密码字符串。

    Returns:
        bcrypt 哈希后的密码字符串（$2b$ 格式）。
    """
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。

    用 try/except 包裹非法哈希格式，返回 False 而不抛异常。

    Args:
        plain: 明文密码字符串。
        hashed: 数据库中存储的 bcrypt 哈希字符串。

    Returns:
        匹配返回 True，不匹配或哈希格式非法返回 False。
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # 非法哈希格式（如被截断、非 bcrypt 格式）
        return False


def validate_password_strength(password: str) -> None:
    """校验密码强度（基本规则）。

    规则：长度 >= 6、不含空白字符。
    不满足时抛出 BusinessError。

    选择 ErrorCode.REQUIRED_MISSING(2001) 而非 AMOUNT_INVALID(9002) 的理由：
    密码强度校验属于"参数校验"范畴，2001 的语义是"必填项缺失/参数不合法"，
    而 9002 专用于会员模块的金额校验，语义不匹配。

    Args:
        password: 待校验的密码字符串。

    Raises:
        BusinessError: 密码不满足强度要求时抛出（code=2001）。
    """
    if len(password) < 6:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "密码长度不能少于6位")
    if any(c.isspace() for c in password):
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "密码不能包含空白字符")
