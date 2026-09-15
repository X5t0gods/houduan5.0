"""认证与权限路由（阶段 2 交付物）—— 6 个接口。

对应 docs/06 §3.1 与 spec 4.2-4.7：
| # | 方法 | 路径 | 权限 |
| 1 | GET  | /auth/captcha | 无需登录 |
| 2 | POST | /auth/login   | 无需登录 |
| 3 | POST | /auth/logout  | 登录即可 |
| 4 | POST | /auth/refresh | 无需登录（凭 refresh_token） |
| 5 | GET  | /auth/me      | 登录即可 |
| 6 | POST | /auth/password| 登录即可 |

⚠️ 分层约束（决策 8）：路由**只做参数校验与权限声明**，业务在 service 层。
⚠️ 响应体：全部走 `success(data)` 构造器返回统一 5 字段结构（阶段 0 已定）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request

from app.core.response import success
from app.deps import CurrentUser, DbSession
from app.schemas.auth import (
    ChangePasswordParams,
    LoginParams,
    RefreshParams,
)
from app.services.auth_service import AuthService, _extract_client_ip

router = APIRouter(prefix="/auth", tags=["认证与权限"])


# ---------- 1. GET /auth/captcha ----------


@router.get(
    "/captcha",
    summary="获取图形验证码",
    description=(
        "生成 4 位图形验证码（排除易混淆字符 0/O/1/I/l），返回 captcha_key 与 Base64 Data URL。"
        "有效期 2 分钟，一 key 只能用一次。\n\n"
        "**无需登录**，也不参与权限校验。"
    ),
)
def get_captcha(
    db: DbSession,
    request: Request,
    x_forwarded_for: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
) -> dict[str, Any]:
    """生成图形验证码。

    Args:
        db: 数据库会话（FastAPI 自动注入）。
        request: 请求对象（用于取客户端 IP）。
        x_forwarded_for: 代理链 IP 列表（可选）。

    Returns:
        统一响应体，data = CaptchaResult(captcha_key, captcha_image)。
    """
    client_ip = _extract_client_ip(x_forwarded_for, request.client.host if request.client else None)
    service = AuthService(db)
    result = service.generate_captcha(client_ip=client_ip)
    return success(result.model_dump())


# ---------- 2. POST /auth/login ----------


@router.post(
    "/login",
    summary="用户登录",
    description=(
        "校验图形验证码 + 用户名密码，成功后返回双令牌与用户档案。\n\n"
        "**校验顺序**（严格按此顺序，防账号枚举与自动化爆破）：\n"
        "1. 验证码错/过期/已用 → `1001`\n"
        "2. 用户不存在 → `1002`（与密码错共用文案，防枚举）\n"
        "3. 已锁定 → `1003`\n"
        "4. 密码错 → 计数+1，达阈值同时锁定；返回 `1002` 或 `1003`\n"
        "5. 停用 → `1004`（放密码之后防枚举）\n"
        "6. 成功 → 返回 LoginResult\n\n"
        "⛔ 所有失败一律 **HTTP 200 + 非 0 code**，不返回 401（否则前端会清登录态踢用户）。"
    ),
)
def login(
    params: LoginParams,
    db: DbSession,
    request: Request,
    x_forwarded_for: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
) -> dict[str, Any]:
    """用户登录。

    Args:
        params: 登录参数（username/password/captcha_key/captcha_code）。
        db: 数据库会话。
        request: 请求对象。
        x_forwarded_for: 代理链 IP。

    Returns:
        统一响应体，data = LoginResult(access_token, refresh_token, token_type,
        expires_in, user_profile)。

    Raises:
        BusinessError: 各步骤校验失败（1001/1002/1003/1004），由全局异常处理器转 HTTP 200。
    """
    client_ip = _extract_client_ip(x_forwarded_for, request.client.host if request.client else None)
    user_agent = request.headers.get("User-Agent")
    service = AuthService(db)
    result = service.login(params, client_ip=client_ip, user_agent=user_agent)
    return success(result.model_dump())


# ---------- 3. POST /auth/logout ----------


@router.post(
    "/logout",
    summary="退出登录",
    description=(
        "一期实现为**空操作**：返回成功，由前端 clearAuth() 清除本地令牌。\n\n"
        "⚠️ 取舍说明（spec 4.6）：docs/06 §1.2 说'服务端可将令牌加入黑名单'（**可选**）。"
        "本项目不引入 Redis（决策 10），也没有令牌黑名单表，因此一期不做服务端失效。"
        "**不要偷偷加内存黑名单** —— 多进程部署下会失效，制造虚假安全感。\n\n"
        "这是 JWT 无状态设计的固有代价，不是缺陷。"
    ),
)
def logout(current_user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """退出登录。

    Args:
        current_user: 当前登录用户（get_current_user 依赖注入）。
        db: 数据库会话。

    Returns:
        统一响应体，data = null。
    """
    service = AuthService(db)
    service.logout(current_user.user_id, current_user.username)
    return success(None)


# ---------- 4. POST /auth/refresh ----------


@router.post(
    "/refresh",
    summary="刷新访问令牌",
    description=(
        "用 refresh_token 换新的 access_token。**无需登录**（凭 refresh_token 即可）。\n\n"
        "⛔ 只接受 `type=refresh` 的令牌；用 access token 来刷新会返回 401。\n"
        "⛔ 已停用/已删除的用户不允许刷新（否则停用形同虚设）。\n"
        "⚠️ 返回值**只有两个字段**：access_token + expires_in，不塞 refresh_token 或 user_profile。"
    ),
)
def refresh_token(params: RefreshParams, db: DbSession) -> dict[str, Any]:
    """刷新 access token。

    Args:
        params: RefreshParams(refresh_token)。
        db: 数据库会话。

    Returns:
        统一响应体，data = RefreshResult(access_token, expires_in)。

    Raises:
        TokenError: refresh_token 无效/过期/类型不符（由路由层依赖转 401）。
        BusinessError(1004): 用户已停用。
    """
    # TokenError 需转成 HTTPException(401)，让 exceptions 处理器返回统一结构
    from fastapi import HTTPException, status

    from app.core.security import TokenError

    service = AuthService(db)
    try:
        result = service.refresh(params.refresh_token)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=e.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    return success(result.model_dump())


# ---------- 5. GET /auth/me ----------


@router.get(
    "/me",
    summary="获取当前用户信息",
    description=(
        "返回当前登录用户的档案（含角色名与权限码）。**登录即可**，不加权限码校验。\n\n"
        "⚠️ 关键规则（spec 1.2 三处契约冲突）：\n"
        "- `permissions`：ADMIN 用户返回 `[\"*\"]` 通配符，其他用户返回 perm_type=2 的权限码并集\n"
        "- `roles`：返回**中文角色名**数组（按 sort 升序），前端 HeaderBar 直接展示\n"
        "- 目录码 `menu:*` 绝不进入 permissions\n\n"
        "该接口是**权限的唯一权威来源**：前端页面刷新时调它恢复登录态，"
        "角色/权限被改动后刷新即可生效（这也是不把权限放进 token 的原因）。"
    ),
)
def get_me(current_user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """获取当前用户档案。

    Args:
        current_user: 当前登录用户上下文。
        db: 数据库会话。

    Returns:
        统一响应体，data = UserProfile。
    """
    service = AuthService(db)
    profile = service.get_user_profile(current_user.user_id)
    return success(profile.model_dump())


# ---------- 6. POST /auth/password ----------


@router.post(
    "/password",
    summary="修改当前用户密码",
    description=(
        "登录用户修改自己的密码。**新密码至少 8 位**（docs/06 §3.1.6）。\n\n"
        "⚠️ 补充约定（spec 4.7，文档未定义）：\n"
        "- 原密码错误 → `1002`（复用\"密码错误\"错误码）\n"
        "- 新密码少于 8 位 → `2001`（Pydantic 校验层拦截）\n"
        "- 新旧密码相同 → `2001`\n"
        "- 一期不支持\"改密后令旧令牌立即失效\"（无黑名单/无 token_version 字段）\n\n"
        "⛔ 审计日志 `sys_operation_log` 的 before_value/after_value **绝不写密码哈希**，"
        "写 `{\"password\": \"***\"}` 明确脱敏。"
    ),
)
def change_password(
    params: ChangePasswordParams,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    x_forwarded_for: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
) -> dict[str, Any]:
    """修改当前登录用户的密码。

    Args:
        params: ChangePasswordParams(old_password, new_password)。
        current_user: 当前登录用户。
        db: 数据库会话。
        request: 请求对象。
        x_forwarded_for: 代理链 IP。

    Returns:
        统一响应体，data = null。

    Raises:
        BusinessError: 1002（原密码错）/ 2001（新旧相同或规则不满足）。
    """
    client_ip = _extract_client_ip(x_forwarded_for, request.client.host if request.client else None)
    user_agent = request.headers.get("User-Agent")
    service = AuthService(db)
    service.change_password(
        user_id=current_user.user_id,
        params=params,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return success(None)
