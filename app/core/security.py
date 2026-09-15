"""安全模块 - 密码哈希、校验与 JWT 签发/解析（阶段 2 扩展）。

技术决策
--------
- ⛔ 不引入 passlib（已停止维护，与 bcrypt >= 4.0 不兼容）
- ⛔ 不用 python-jose（依赖重、维护少），用 **PyJWT**（决策 2）
- 直接使用 bcrypt.hashpw / checkpw，cost=10 与 db/02_init_data.sql 一致
- Token 只放 sub/username/type/iat/exp/jti，**绝不放 permissions 或 roles**（决策 3）
  权限每次从数据库查（两张关联表 JOIN，走索引，开销可接受）；塞进 token 后
  角色变更旧令牌仍拿旧权限，越权问题极难排查

安全要点
--------
1. jwt.decode **必须显式传 algorithms=[settings.JWT_ALGORITHM]**（防算法混淆攻击）
2. sub 在 JWT 标准里是字符串，写入时 str(user_id)、解析时转回 int
3. jti 用 uuid4().hex，为将来做令牌失效预留
4. 启动自检：DEBUG=False 且 JWT_SECRET_KEY 仍是 .env.example 里的占位值时抛异常拒绝启动
"""

from __future__ import annotations

import time
import uuid
from typing import Final

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError, PyJWTError

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.auth import TokenPayload

# 令牌类型常量
TOKEN_TYPE_ACCESS: Final[str] = "access"
TOKEN_TYPE_REFRESH: Final[str] = "refresh"

# .env.example 里的占位密钥前缀，用于启动自检
_PLACEHOLDER_SECRET_PREFIXES: Final[tuple[str, ...]] = (
    "change-me",
    "placeholder",
    "your-",
    "xxx",
)


class TokenError(Exception):
    """Token 校验失败（签名错/过期/类型不符/格式错）。

    ⚠️ 由 deps.py 的 get_current_user 依赖捕获并转成 HTTPException(401)。
    本异常本身不携带敏感信息，message 是给用户看的通用文案。
    """

    def __init__(self, message: str = "登录状态已失效，请重新登录") -> None:
        """初始化。

        Args:
            message: 面向用户的通用错误提示，不透露具体原因（防信息泄漏）。
        """
        self.message = message
        super().__init__(message)


# ---------- 密码哈希（阶段 0 已有，保留不变） ----------


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
    """校验密码强度（阶段 0 的基本规则：长度 ≥ 6、不含空白）。

    ⚠️ 阶段 2 的 POST /auth/password 有更严格的规则（新密码 ≥ 8 位），
       由 schemas/auth.py::ChangePasswordParams 的 Pydantic 校验兜住，
       此函数保留用于其他场景（如阶段 10 的用户重置密码）。

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


# ---------- JWT 签发与解析（阶段 2 新增） ----------


def _build_token(user_id: int, username: str, token_type: str, expires_seconds: int) -> str:
    """内部工具：构造 JWT。

    Args:
        user_id: 用户 ID（写入时转 str，符合 JWT sub 标准）。
        username: 登录账号（冗余，便于日志追踪与前端展示）。
        token_type: "access" 或 "refresh"。
        expires_seconds: 有效期（秒）。

    Returns:
        编码后的 JWT 字符串。
    """
    now = int(time.time())
    payload = {
        # ⚠️ sub 在 JWT 标准里是字符串，写入时 str(user_id)、解析时转回 int
        # 若这里写 int，某些库解析时会拿到 int 而非 str，与标准不符
        "sub": str(user_id),
        "username": username,
        "type": token_type,
        "iat": now,
        "exp": now + expires_seconds,
        # jti 为将来做令牌失效（黑名单/token_version）预留
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int, username: str) -> tuple[str, int]:
    """签发 access token。

    有效期取 settings.ACCESS_TOKEN_EXPIRE_MINUTES × 60，与 sys_config 里的
    `sys.session_timeout` 对齐（默认 120 分钟 = 7200 秒，与前端 Mock 的 expires_in 一致）。

    Args:
        user_id: 用户 ID。
        username: 登录账号。

    Returns:
        (token, expires_in_seconds) 元组。
    """
    expires_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    token = _build_token(user_id, username, TOKEN_TYPE_ACCESS, expires_seconds)
    return token, expires_seconds


def create_refresh_token(user_id: int, username: str) -> str:
    """签发 refresh token。

    有效期取 settings.REFRESH_TOKEN_EXPIRE_DAYS 天（默认 7 天）。

    Args:
        user_id: 用户 ID。
        username: 登录账号。

    Returns:
        refresh token 字符串。
    """
    expires_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    return _build_token(user_id, username, TOKEN_TYPE_REFRESH, expires_seconds)


def decode_token(token: str, *, expected_type: str) -> TokenPayload:
    """解析并校验 JWT。

    ⛔ 必须显式传 `algorithms=[settings.JWT_ALGORITHM]`，防算法混淆攻击
       （攻击者可能把 alg 改成 none 或用公钥当 HMAC 密钥）。

    Args:
        token: JWT 字符串。
        expected_type: 期望的令牌类型（"access" 或 "refresh"）。
                      用 refresh token 当 access 用（或反过来）**必须拒绝**。

    Returns:
        TokenPayload 实例（sub 已转回 int）。

    Raises:
        TokenError: 签名错、过期、格式错、type 不匹配等，统一转成此异常。
                   ⚠️ message 是通用文案，不透露具体原因（防信息泄漏）。
    """
    try:
        # ⚠️ algorithms 必须显式传列表，不能省
        raw = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except InvalidTokenError as e:
        # 涵盖 ExpiredSignatureError / InvalidSignatureError / DecodeError 等
        # ⚠️ 不要把底层异常信息透传给前端（可能泄漏算法/密钥线索）
        raise TokenError from e

    # type 校验：拿 refresh 当 access 用（或反过来）必须拒绝
    token_type = raw.get("type")
    if token_type != expected_type:
        raise TokenError

    # sub 必须存在且能转成 int
    sub = raw.get("sub")
    if sub is None:
        raise TokenError
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as e:
        raise TokenError from e

    try:
        return TokenPayload(
            sub=user_id,
            username=raw.get("username", ""),
            type=token_type,
            iat=int(raw.get("iat", 0)),
            exp=int(raw.get("exp", 0)),
            jti=raw.get("jti", ""),
        )
    except (TypeError, ValueError) as e:
        # Pydantic 校验失败也归为 TokenError
        raise TokenError from e


def validate_jwt_config() -> None:
    """启动自检：生产环境不允许用占位密钥。

    ⚠️ 触发条件：`DEBUG=False` **且** `JWT_SECRET_KEY` 命中占位前缀
       （change-me / placeholder / your- / xxx）。
       开发环境（DEBUG=True）不阻断，方便本地跑通。

    Raises:
        RuntimeError: 生产环境带着占位密钥启动时抛出，直接拒绝启动。
    """
    if settings.DEBUG:
        return
    secret = settings.JWT_SECRET_KEY.lower()
    for prefix in _PLACEHOLDER_SECRET_PREFIXES:
        if secret.startswith(prefix):
            raise RuntimeError(
                f"JWT_SECRET_KEY 仍是占位值（以 '{prefix}' 开头），"
                f"生产环境（DEBUG=False）必须替换为至少 32 字符的随机强密钥。"
                f"生成方式：python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )


__all__ = [
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "TokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "validate_jwt_config",
    "validate_password_strength",
    "verify_password",
]

# 让 lint 知道 PyJWTError 是被间接使用的（InvalidTokenError 是它的子类）
_ = PyJWTError
