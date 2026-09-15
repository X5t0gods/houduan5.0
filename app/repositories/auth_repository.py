"""认证与权限的数据访问层（阶段 2 交付物）。

⚠️ 分层约束（阶段 0 决策）：
- Repository **不 commit**（事务边界归 service 层），只 flush
- 跨表用**显式 select().join()**（决策 2：禁 relationship 避免隐式懒加载）
- 数据范围过滤（data_scope）本阶段暂不接入，阶段 3 起由各业务 repository 处理
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base_models import BaseStore
from app.models.sys_models import (
    SysCaptcha,
    SysLoginLog,
    SysOperationLog,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)

# perm_type 常量（与 02_init_data.sql:117-119 注释一致）
PERM_TYPE_DIRECTORY: int = 1
PERM_TYPE_FUNCTION: int = 2
# ⚠️ 数据库中不存在 perm_type=3（按钮），docs/06 §2.2 与建表脚本注释里的"3 按钮"
# 是空头承诺，实际数据里从未使用。/auth/me 只返回 perm_type=2 的权限码。

# 管理员角色编码（用于 permissions=['*'] 通配符判定）
ADMIN_ROLE_CODE: str = "ADMIN"


def _now() -> datetime:
    """当前时间（Asia/Shanghai）。"""
    return datetime.now(ZoneInfo(settings.TZ))


# ---------- 用户 ----------


def get_user_by_username(session: Session, username: str) -> SysUser | None:
    """按用户名查用户（不校验状态，登录流程需要看到停用账号才能返回 1004）。"""
    return session.execute(
        select(SysUser).where(SysUser.username == username)
    ).scalar_one_or_none()


def get_user_by_id(session: Session, user_id: int) -> SysUser | None:
    """按 ID 查用户（get_current_user 依赖用）。"""
    return session.execute(
        select(SysUser).where(SysUser.id == user_id)
    ).scalar_one_or_none()


def update_login_success(session: Session, user_id: int, ip: str) -> None:
    """登录成功后更新用户状态：清零失败计数、清除锁定、记录最后登录时间/IP。

    ⚠️ 只 flush 不 commit，事务边界归 service 层。
    """
    session.execute(
        update(SysUser)
        .where(SysUser.id == user_id)
        .values(
            login_fail_count=0,
            locked_until=None,
            last_login_at=_now(),
            last_login_ip=ip[:45],  # 字段是 VARCHAR(45)，防越界
        )
    )
    session.flush()


def update_login_failure(
    session: Session,
    user_id: int,
    current_fail_count: int,
    fail_limit: int,
    lock_minutes: int,
) -> bool:
    """登录失败后更新计数；若达到阈值则同时锁定。

    Args:
        session: 数据库会话。
        user_id: 用户 ID。
        current_fail_count: 当前失败计数（从 SysUser 读出的原值）。
        fail_limit: 锁定阈值（sys.login_fail_limit，默认 5）。
        lock_minutes: 锁定时长分钟（sys.login_lock_minutes，默认 15）。

    Returns:
        True 表示本次失败已触发锁定；False 表示仅计数 +1。
    """
    new_count = current_fail_count + 1
    values: dict[str, Any] = {"login_fail_count": new_count}
    locked = False
    if new_count >= fail_limit:
        # 触发锁定：从当前时间起 lock_minutes 分钟后解锁
        values["locked_until"] = _now().replace(microsecond=0) + timedelta(minutes=lock_minutes)
        locked = True

    session.execute(update(SysUser).where(SysUser.id == user_id).values(**values))
    session.flush()
    return locked


def update_password(session: Session, user_id: int, new_hash: str) -> None:
    """更新用户密码哈希。"""
    session.execute(
        update(SysUser).where(SysUser.id == user_id).values(password_hash=new_hash)
    )
    session.flush()


# ---------- 角色与权限 ----------


def get_user_roles(session: Session, user_id: int) -> list[SysRole]:
    """获取用户的所有角色（按 sys_role.sort 升序）。

    ⚠️ 用于 UserProfile.roles 装配 —— 前端 HeaderBar.vue:141 直接展示 role_name。
    """
    stmt = (
        select(SysRole)
        .join(SysUserRole, SysUserRole.role_id == SysRole.id)
        .where(SysUserRole.user_id == user_id, SysRole.status == 1)
        .order_by(SysRole.sort.asc(), SysRole.id.asc())
    )
    return list(session.execute(stmt).scalars().all())


def is_admin(session: Session, user_id: int) -> bool:
    """判断用户是否拥有 ADMIN 角色（用于 permissions=['*'] 通配符判定）。"""
    stmt = (
        select(func.count())
        .select_from(SysUserRole)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(
            SysUserRole.user_id == user_id,
            SysRole.role_code == ADMIN_ROLE_CODE,
            SysRole.status == 1,
        )
    )
    return session.execute(stmt).scalar_one() > 0


def get_user_permissions(session: Session, user_id: int) -> list[str]:
    """获取用户的**功能权限码**（perm_type=2）并集，去重后按 perm_code 排序。

    ⚠️ 关键规则（spec 1.1 + 4.4）：
    1. **只返回 perm_type = 2** 的权限码（不返回目录 perm_type=1）
    2. 一人多岗时取所有角色的**并集**（如 buyer01 = PURCHASER ∪ MANAGER）
    3. **不包含 ADMIN 特判**：admin 的通配符 ['*'] 由 service 层根据 is_admin() 决定

    ⚠️ `/auth/me` 与 `require_perm` 依赖共用此方法（避免两处逻辑漂移，spec 4.8）。

    Args:
        session: 数据库会话。
        user_id: 用户 ID。

    Returns:
        权限码列表（去重、按字母序），如 ["dashboard:view", "pos:use", ...]
    """
    stmt = (
        select(SysPermission.perm_code)
        .join(SysRolePermission, SysRolePermission.permission_id == SysPermission.id)
        .join(SysUserRole, SysUserRole.role_id == SysRolePermission.role_id)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(
            SysUserRole.user_id == user_id,
            SysPermission.perm_type == PERM_TYPE_FUNCTION,  # ⭐ 只取功能权限，过滤目录
            SysPermission.status == 1,
            SysRole.status == 1,
        )
        .distinct()
        .order_by(SysPermission.perm_code.asc())
    )
    return list(session.execute(stmt).scalars().all())


def get_user_data_scope(session: Session, user_id: int) -> str:
    """获取用户的 data_scope（多角色时取范围最大的那个）。

    优先级：ALL > STORE > DEPT > SELF（一人多岗时权限取并集，spec 4.8）。

    Args:
        session: 数据库会话。
        user_id: 用户 ID。

    Returns:
        data_scope 字符串；无任何角色时返回 "SELF"（最保守）。
    """
    stmt = (
        select(SysRole.data_scope)
        .join(SysUserRole, SysUserRole.role_id == SysRole.id)
        .where(SysUserRole.user_id == user_id, SysRole.status == 1)
    )
    scopes = {row[0] for row in session.execute(stmt).all()}
    # 按优先级返回最大范围
    for scope in ("ALL", "STORE", "DEPT", "SELF"):
        if scope in scopes:
            return scope
    return "SELF"


# ---------- 门店 ----------


def get_store_name(session: Session, store_id: int | None) -> str | None:
    """获取门店名称（UserProfile.store_name 装配用）。"""
    if store_id is None:
        return None
    return session.execute(
        select(BaseStore.store_name).where(BaseStore.id == store_id)
    ).scalar_one_or_none()


# ---------- 验证码 ----------


def create_captcha(
    session: Session,
    captcha_key: str,
    captcha_code: str,
    ip: str | None,
    expire_at: datetime,
) -> None:
    """写入验证码记录。"""
    session.add(SysCaptcha(
        captcha_key=captcha_key,
        captcha_code=captcha_code,
        ip=(ip or "")[:45] or None,
        used=0,
        expire_at=expire_at,
    ))
    session.flush()


def get_captcha_by_key(session: Session, captcha_key: str) -> SysCaptcha | None:
    """按 key 查验证码（登录校验用）。"""
    return session.execute(
        select(SysCaptcha).where(SysCaptcha.captcha_key == captcha_key)
    ).scalar_one_or_none()


def mark_captcha_used(session: Session, captcha_id: int) -> None:
    """把验证码置为已使用（一 key 只能用一次，spec 4.3）。"""
    session.execute(
        update(SysCaptcha).where(SysCaptcha.id == captcha_id).values(used=1)
    )
    session.flush()


def cleanup_expired_captchas(session: Session) -> int:
    """清理过期验证码（低频顺带执行；阶段 8 会有定时任务统一接管）。

    Returns:
        清理的记录数。
    """
    now = _now()
    result = session.execute(
        delete(SysCaptcha).where(SysCaptcha.expire_at < now)
    )
    session.flush()
    return result.rowcount or 0


# ---------- 日志 ----------


def write_login_log(
    session: Session,
    username: str,
    user_id: int | None,
    ip: str | None,
    user_agent: str | None,
    result: int,
    fail_reason: str | None = None,
) -> None:
    """写入登录日志（成功与失败都要写，spec 4.3）。

    Args:
        result: 1=成功, 0=失败
        fail_reason: 失败原因（如"验证码错误"、"密码错误"、"账号已锁定"），成功时 None
    """
    session.add(SysLoginLog(
        username=username[:32],
        user_id=user_id,
        ip=(ip or "")[:45] or None,
        user_agent=(user_agent or "")[:255] or None,
        result=result,
        fail_reason=(fail_reason or "")[:128] or None,
    ))
    session.flush()


def write_operation_log(
    session: Session,
    user_id: int | None,
    user_name: str | None,
    module: str,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    before_value: dict[str, Any] | None = None,
    after_value: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    cost_ms: int | None = None,
    result: int = 1,
    error_msg: str | None = None,
) -> None:
    """写入操作日志（阶段 2 用于修改密码；后续阶段扩展）。

    ⛔ before_value / after_value **绝对不要写入密码哈希**（spec 4.7）——
       审计表是给人和论文看的，写入哈希等于把凭据留在日志表。
       修改密码时传 None 或 {"password": "***"} 即可。
    """
    session.add(SysOperationLog(
        user_id=user_id,
        user_name=(user_name or "")[:32] or None,
        module=module[:32],
        action=action[:32],
        target_type=(target_type or "")[:32] or None,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        ip=(ip or "")[:45] or None,
        user_agent=(user_agent or "")[:255] or None,
        cost_ms=cost_ms,
        result=result,
        error_msg=(error_msg or "")[:500] or None,
    ))
    session.flush()


# ---------- 权限树（阶段 10 会用到，此处仅提供基础查询） ----------


def list_all_permissions(session: Session) -> list[SysPermission]:
    """列出所有权限（含目录与功能），按 sort 排序。阶段 10 的角色权限树会用。"""
    return list(session.execute(
        select(SysPermission)
        .where(SysPermission.status == 1)
        .order_by(SysPermission.parent_id.asc(), SysPermission.sort.asc())
    ).scalars().all())


def list_permission_codes_by_type(session: Session, perm_type: int) -> list[str]:
    """按 perm_type 列出权限码。用于测试断言与调试。"""
    return list(session.execute(
        select(SysPermission.perm_code)
        .where(SysPermission.perm_type == perm_type, SysPermission.status == 1)
        .order_by(SysPermission.perm_code.asc())
    ).scalars().all())


# ---------- 角色（阶段 10 用户管理会用到） ----------


def list_active_roles(session: Session) -> list[SysRole]:
    """列出所有启用角色（按 sort 升序）。"""
    return list(session.execute(
        select(SysRole).where(SysRole.status == 1).order_by(SysRole.sort.asc())
    ).scalars().all())


def get_role_permissions(session: Session, role_id: int) -> list[str]:
    """获取单个角色的权限码列表（含目录）。阶段 10 会用。"""
    stmt = (
        select(SysPermission.perm_code)
        .join(SysRolePermission, SysRolePermission.permission_id == SysPermission.id)
        .where(SysRolePermission.role_id == role_id)
        .order_by(SysPermission.perm_code.asc())
    )
    return list(session.execute(stmt).scalars().all())
