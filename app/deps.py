"""通用依赖模块 - FastAPI 依赖注入函数。

阶段 0 交付：get_db、PageParams
阶段 2 扩展：UserContext、get_current_user、require_perm、resolve_store_filter

⚠️ 关键设计（spec 4.8）：
1. **401 vs 403 语义严格区分**：
   - 401 = "需要一个有效登录态但没拿到"（token 缺失/格式错/签名错/过期/用户停用）
   - 403 = "登录了但没这个权限"（响应体 code=1040）
2. 401 与 403 的响应体**也走统一结构**（阶段 0 exceptions.py 的 http_exception_handler
   已支持，会自动带 code/message/request_id/timestamp）
3. `get_user_permissions` 从 auth_repository 走，`/auth/me` 与 `require_perm` **共用它**
   （避免两处逻辑漂移）
4. `data_scope` 取该用户**所有角色中范围最大的那个**（ALL > STORE > DEPT > SELF），
   理由：一人多岗时权限取并集
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db as _get_db
from app.core.permissions import PERM_TYPE_FUNCTION  # noqa: F401  # 便于调用方 import
from app.core.security import TOKEN_TYPE_ACCESS, TokenError, decode_token
from app.repositories import auth_repository as repo

# 数据库会话依赖（转发 core.database.get_db）
# 使用方式：def endpoint(db: DbSession = Depends(get_db))
get_db = _get_db

# 类型别名，便于路由函数签名简洁
DbSession = Annotated[Session, Depends(get_db)]


# ---------- 分页参数（阶段 0） ----------


class PageParams:
    """分页参数依赖 - 从 Query String 解析分页参数。

    使用方式：
        def list_items(page: PageParams = Depends(PageParams)):
            offset = page.offset
            limit = page.page_size

    Attributes:
        page: 页码，从 1 开始，默认 1。
        page_size: 每页条数，默认 20，最大 100。
        keyword: 模糊检索关键字。
        start_date: 起始日期 YYYY-MM-DD。
        end_date: 结束日期 YYYY-MM-DD。
        store_id: 门店 ID。
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="页码，从1开始")] = 1,
        page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
        keyword: Annotated[str | None, Query(description="模糊检索关键字")] = None,
        start_date: Annotated[str | None, Query(description="起始日期 YYYY-MM-DD")] = None,
        end_date: Annotated[str | None, Query(description="结束日期 YYYY-MM-DD")] = None,
        store_id: Annotated[int | None, Query(description="门店ID")] = None,
    ) -> None:
        """初始化分页参数。

        Args:
            page: 页码，从 1 开始。
            page_size: 每页条数，默认 20，最大 100。
            keyword: 模糊检索关键字。
            start_date: 起始日期。
            end_date: 结束日期。
            store_id: 门店 ID。
        """
        self.page = page
        self.page_size = page_size
        self.keyword = keyword
        self.start_date = start_date
        self.end_date = end_date
        self.store_id = store_id

    @property
    def offset(self) -> int:
        """计算 SQL OFFSET 值。

        Returns:
            偏移量 = (page - 1) * page_size
        """
        return (self.page - 1) * self.page_size


# ---------- 用户上下文（阶段 2 新增） ----------


@dataclass
class UserContext:
    """已登录用户的上下文对象（get_current_user 依赖的返回值）。

    ⚠️ **不缓存 permissions**：每次请求都从 DB 查最新权限，避免"角色/权限被改动后
       旧令牌仍拿旧权限"的越权问题（决策 3：绝不把权限塞进 token）。

    Attributes:
        user_id: 用户 ID（sys_user.id）
        username: 登录账号
        real_name: 真实姓名
        store_id: 所属门店 ID（可能为 None，如 admin）
        role_codes: 角色编码列表（如 ["MANAGER", "PURCHASER"]）
        permissions: 权限码集合（**含 "*" 通配符**如果是 ADMIN）
        data_scope: 数据范围（ALL/STORE/DEPT/SELF，取所有角色中范围最大的）
    """

    user_id: int
    username: str
    real_name: str
    store_id: int | None
    role_codes: list[str] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)
    data_scope: str = "SELF"

    @property
    def is_admin(self) -> bool:
        """是否超级管理员（permissions 含 "*" 通配符）。"""
        return "*" in self.permissions

    def has_perm(self, code: str) -> bool:
        """检查是否有指定权限码（ADMIN 直接放行）。"""
        return self.is_admin or code in self.permissions


# ---------- 认证依赖 ----------


def _extract_bearer_token(authorization: str | None) -> str | None:
    """从 Authorization header 提取 Bearer token。

    Args:
        authorization: 完整的 Authorization header 值，如 "Bearer eyJhbGc..."

    Returns:
        token 字符串；格式错时返回 None。
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)  # 最多分两段
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> UserContext:
    """解析 Authorization 头，返回当前登录用户上下文。

    ⚠️ 401 触发条件（spec 4.8）：
    - Authorization 头缺失或格式错（不是 "Bearer xxx"）
    - Token 签名错、过期、type 不是 access
    - Token 里的 user_id 对应的用户不存在
    - 用户 status=0（已停用）

    ⚠️ 每次请求都查一次库（用户 + 角色 + 权限），这是刻意的：
       权限被改动后立即生效，不用等 token 过期。开销可接受（两次索引查询）。

    Args:
        authorization: HTTP Authorization header（FastAPI 自动注入）。
        db: 数据库会话。

    Returns:
        UserContext 实例。

    Raises:
        HTTPException(401): 上述任一 401 条件命中。
    """
    token = _extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 解析 token（TokenError 统一转 401）
    try:
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=e.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # 查用户（可能已被删除或停用）
    user = repo.get_user_by_id(db, payload.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已删除",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.status == 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已停用，请联系管理员",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 装配角色与权限（每次请求实时查，不缓存 —— 决策 3）
    roles = repo.get_user_roles(db, user.id)
    role_codes = [r.role_code for r in roles]

    # ADMIN 通配符特判（与 /auth/me 保持一致）
    if any(r.role_code == repo.ADMIN_ROLE_CODE for r in roles):
        permissions = {"*"}
    else:
        permissions = set(repo.get_user_permissions(db, user.id))

    data_scope = repo.get_user_data_scope(db, user.id)

    return UserContext(
        user_id=user.id,
        username=user.username,
        real_name=user.real_name,
        store_id=user.store_id,
        role_codes=role_codes,
        permissions=permissions,
        data_scope=data_scope,
    )


# 类型别名，便于路由函数签名简洁
CurrentUser = Annotated[UserContext, Depends(get_current_user)]


# ---------- 权限依赖工厂 ----------


def require_perm(*codes: str) -> Callable[..., UserContext]:
    """权限校验依赖工厂。

    用法：
        @router.get("/system/users", dependencies=[Depends(require_perm("sys:user"))])
        def list_users(): ...

        # 或者拿到 UserContext 用：
        @router.get("/xxx")
        def xxx(user: UserContext = Depends(require_perm("sys:user"))): ...

    放行规则：
    1. 用户是 ADMIN（permissions 含 "*"）→ 直接放行
    2. codes 中任一命中用户 permissions → 放行
    3. 都不命中 → HTTP 403 + code=1040（响应体走统一结构）

    Args:
        *codes: 需要的权限码（多个是"任一命中即可"关系）。

    Returns:
        FastAPI 依赖函数（返回 UserContext，便于路由函数复用）。
    """

    def _checker(current_user: CurrentUser) -> UserContext:
        # ADMIN 通配符直接放行
        if current_user.is_admin:
            return current_user
        # 任一命中即可
        for code in codes:
            if code in current_user.permissions:
                return current_user
        # 都不命中 → 403（响应体由 exceptions.http_exception_handler 统一封装）
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="没有该操作的权限",
        )

    return _checker


# ---------- 数据范围过滤骨架（spec 4.9） ----------


def resolve_store_filter(user_ctx: UserContext) -> int | None:
    """根据 data_scope 计算门店过滤条件（阶段 3 起各 repository 调用）。

    规则（spec 4.9）：
    - ALL   → None（不加门店条件，可查所有门店）
    - STORE → user_ctx.store_id（限本门店）
    - DEPT  → user_ctx.store_id（暂等同 STORE，未来接入岗位维度时扩展）
    - SELF  → user_ctx.store_id（同时调用方需额外用 user_id 过滤，如 created_by=user_id）

    Args:
        user_ctx: 当前登录用户上下文。

    Returns:
        门店 ID（用于 WHERE store_id = :value），或 None（不加此条件）。

    ⚠️ 本阶段只提供纯函数骨架，**不接入任何业务查询**；
       阶段 3 起由各 repository 在 SELECT 语句里显式使用。
    """
    if user_ctx.data_scope == "ALL":
        return None
    # STORE / DEPT / SELF 都返回本门店 ID
    # SELF 场景下调用方需再叠加 user_id 过滤（本函数不管）
    return user_ctx.store_id
