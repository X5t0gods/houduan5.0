"""认证与权限模块的 Pydantic 模型（阶段 2 交付物）。

⚠️ 与 docs/06 §2.2 的三处契约冲突（以本文件为准，spec 1.2 节已核对前端真相源）：
1. UserProfile.roles 返回**角色名称**（如 ["店长", "采购员"]），不是编码 ["MANAGER"]
   —— HeaderBar.vue:141 直接展示 `roles.join(' / ')`，返回编码会显示英文
2. UserProfile.permissions 对 admin 返回 ["*"] 通配符，不是 32 个码
   —— stores/user.ts:17 `isAdmin = permissions.includes('*')`
3. perm_type 只有 1（目录）和 2（功能权限），文档说的 3（按钮）实际不存在
   —— /auth/me 只返回 perm_type=2 的码
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema

# ---------- 请求参数 ----------


class LoginParams(BaseModel):
    """登录请求参数（POST /auth/login）。

    ⚠️ 四字段全部必填。captcha_key 是 GET /auth/captcha 返回的 UUID，
       captcha_code 是用户输入的 4 位字符（后端校验时不区分大小写）。
    """

    username: str = Field(min_length=1, max_length=32, description="登录账号")
    password: str = Field(min_length=1, max_length=128, description="明文密码")
    captcha_key: str = Field(min_length=1, max_length=64, description="验证码标识（UUID）")
    captcha_code: str = Field(min_length=1, max_length=8, description="用户输入的验证码")


class RefreshParams(BaseModel):
    """刷新令牌请求参数（POST /auth/refresh）。"""

    refresh_token: str = Field(min_length=1, description="refresh token（type=refresh）")


class ChangePasswordParams(BaseModel):
    """修改密码请求参数（POST /auth/password）。

    ⚠️ 新密码规则：不少于 8 位（docs/06 §3.1.6）。演示账号初始密码 123456（6 位）
       是**初始密码**，不受此约束；改密时才校验。
    """

    old_password: str = Field(min_length=1, max_length=128, description="原密码")
    new_password: str = Field(min_length=8, max_length=128, description="新密码，至少 8 位")


# ---------- 响应模型 ----------


class UserProfile(BaseSchema):
    """用户档案（/auth/me 与登录返回的 user_profile）。

    ⚠️ roles 是**中文角色名**（按 sys_role.sort 升序），permissions 是权限码数组
       或 ["*"]（管理员通配符）。
    """

    id: int = Field(description="用户ID")
    username: str = Field(description="登录账号")
    real_name: str = Field(description="真实姓名")
    phone: str | None = Field(default=None, description="手机号")
    avatar: str | None = Field(default=None, description="头像地址")
    store_id: int | None = Field(default=None, description="所属门店ID")
    store_name: str | None = Field(default=None, description="所属门店名称（无门店时 null）")
    roles: list[str] = Field(default_factory=list, description="角色名称数组（按 sort 升序）")
    permissions: list[str] = Field(
        default_factory=list,
        description="权限码数组（perm_type=2）；管理员返回 ['*'] 通配符",
    )


class LoginResult(BaseSchema):
    """登录成功响应（POST /auth/login 的 data）。

    ⚠️ 前端 request.ts 期望这 5 个字段一字不差，尤其 expires_in=7200（120 分钟）。
    """

    access_token: str = Field(description="访问令牌（JWT，type=access）")
    refresh_token: str = Field(description="刷新令牌（JWT，type=refresh）")
    token_type: str = Field(default="Bearer", description="令牌类型，固定 Bearer")
    expires_in: int = Field(description="access_token 有效期（秒）")
    user_profile: UserProfile = Field(description="用户档案")


class RefreshResult(BaseSchema):
    """刷新令牌响应（POST /auth/refresh 的 data）。

    ⚠️ 只有两个字段，不要顺手塞 refresh_token 或 user_profile（前端类型里没有）。
    """

    access_token: str = Field(description="新的访问令牌")
    expires_in: int = Field(description="有效期（秒）")


class CaptchaResult(BaseSchema):
    """图形验证码响应（GET /auth/captcha 的 data）。"""

    captcha_key: str = Field(description="验证码标识（UUID），登录时原样回传")
    captcha_image: str = Field(description="Base64 Data URL（data:image/png;base64,...）")


# ---------- 内部使用（不对外暴露） ----------


class TokenPayload(BaseModel):
    """JWT 解析后的载荷结构。

    ⚠️ 只放 sub / username / type / iat / exp / jti，**绝不放 permissions 或 roles**
       —— 权限会随「角色与权限」页面变更失效，塞进 token 后旧令牌会一直拿旧权限，
       越权问题很难排查（决策 3）。

    Attributes:
        sub: 用户 ID（JWT 标准里 sub 是字符串，解析时转回 int）
        username: 登录账号（冗余，便于日志追踪）
        type: 令牌类型 "access" 或 "refresh"
        iat: 签发时间（Unix 时间戳）
        exp: 过期时间（Unix 时间戳）
        jti: 令牌唯一 ID（uuid4().hex），为将来做令牌失效预留
    """

    model_config = ConfigDict(frozen=True)

    sub: int = Field(description="用户 ID（已从 str 转回 int）")
    username: str = Field(description="登录账号")
    type: str = Field(description="令牌类型 access/refresh")
    iat: int = Field(description="签发时间戳")
    exp: int = Field(description="过期时间戳")
    jti: str = Field(description="令牌唯一 ID")
