"""sys_config 参数读取工具（阶段 2 交付物）。

设计要点
--------
1. **不硬编码策略**：登录锁定阈值、会话超时等运行时可调参数从 sys_config 读取，
   读不到再用兜底默认值。
2. **进程内 TTL 缓存**（默认 60 秒）：避免每次登录都查一次表；
   ⚠️ 多 worker 部署下缓存不一致（决策 10），但因 sys_config 变更低频，
   60 秒内的不一致对业务无感。若确实需要立即生效，调 `invalidate_cache()`。
3. **线程安全**：用 threading.Lock 保护缓存字典（FastAPI 同步路由跑在线程池里）。
4. **类型转换**：按 sys_config.value_type 自动把字符串转成 int/bool/Decimal/JSON。

用法：
    from app.core.sys_config_reader import get_config
    limit = get_config(session, "sys.login_fail_limit", default=5)
"""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.sys_models import SysConfig

logger = get_logger(__name__)

T = TypeVar("T")

# 默认 TTL：60 秒（sys_config 变更低频，60 秒内的不一致对业务无感）
DEFAULT_TTL_SECONDS: float = 60.0

# 进程内缓存：{config_key: (value, cached_at_unix_ts)}
# value 为 None 表示"DB 里没这个 key"，也缓存起来避免重复查库（防穿透）
_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_LOCK = threading.Lock()


def _cast_value(raw: str, value_type: str) -> Any:
    """按 sys_config.value_type 把字符串转成对应 Python 类型。

    Args:
        raw: sys_config.config_value 的原始字符串。
        value_type: sys_config.value_type，取值 string/int/decimal/bool/json。

    Returns:
        转换后的值；转换失败时返回原始字符串并 WARNING（不抛异常，避免影响主流程）。
    """
    try:
        if value_type == "int":
            return int(raw)
        if value_type == "decimal":
            return Decimal(raw)
        if value_type == "bool":
            # 兼容多种真值写法
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if value_type == "json":
            return json.loads(raw)
        # string 或未知类型：原样返回
        return raw
    except (ValueError, InvalidOperation, json.JSONDecodeError) as e:
        logger.warning(f"sys_config 值类型转换失败：raw={raw!r}, type={value_type}, err={e}")
        return raw


def get_config(session: Session, key: str, default: T) -> T:
    """读取 sys_config 参数（带 60 秒 TTL 缓存）。

    Args:
        session: SQLAlchemy Session。
        key: sys_config.config_key，如 "sys.login_fail_limit"。
        default: 兜底默认值（key 不存在或类型转换失败时返回）。

    Returns:
        参数值（类型与 default 一致，或 DB 里 value_type 决定的类型）。

    ⚠️ 缓存命中时**不会用 session 查库**，因此调用方不需要保证 session 生命周期。
    ⚠️ 缓存 miss 时会缓存"None 值"60 秒，防止不存在的 key 反复穿透查库。
    """
    now = time.time()

    # 先查缓存（读锁粒度：整个字典操作）
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            value, cached_at = cached
            if now - cached_at < DEFAULT_TTL_SECONDS:
                return value if value is not None else default

    # 缓存 miss 或过期，查库
    # ⚠️ sys_config 无 status 列（与 sys_user/sys_role 不同），只按 config_key 查
    row = session.execute(
        select(SysConfig).where(SysConfig.config_key == key)
    ).scalar_one_or_none()

    if row is None:
        # 缓存"不存在"，防穿透
        with _CACHE_LOCK:
            _CACHE[key] = (None, now)
        return default

    value = _cast_value(row.config_value, row.value_type)
    with _CACHE_LOCK:
        _CACHE[key] = (value, now)
    return value  # type: ignore[return-value]


def invalidate_cache(key: str | None = None) -> None:
    """清空缓存（阶段 10 修改 sys_config 后调用）。

    Args:
        key: 指定 key 时只清该 key；None 时清空全部。
    """
    with _CACHE_LOCK:
        if key is None:
            _CACHE.clear()
            logger.info("sys_config 缓存已全部清空")
        else:
            _CACHE.pop(key, None)
            logger.info(f"sys_config 缓存已清空：{key}")


# ---------- 常用配置项与默认值（与 sys_config 表约定一致） ----------


class ConfigKey:
    """常用 sys_config key 常量（避免各处硬编码字符串）。"""

    # 登录锁定策略（阶段 2 使用）
    LOGIN_FAIL_LIMIT = "sys.login_fail_limit"          # 默认 5
    LOGIN_LOCK_MINUTES = "sys.login_lock_minutes"      # 默认 15
    SESSION_TIMEOUT = "sys.session_timeout"            # 默认 120 分钟（对齐 access_token）

    # 收银（阶段 4 起使用）
    POS_AUTO_ROUND = "pos.auto_round"
    POS_RECEIPT_FOOTER = "pos.receipt_footer"

    # 库存（阶段 5 起使用）
    INV_EXPIRY_WARN_DAYS = "inv.expiry_warn_days"
    INV_SLOW_MOVING_DAYS = "inv.slow_moving_days"


# ---------- 便捷读取函数（阶段 2 常用） ----------


def get_login_fail_limit(session: Session) -> int:
    """读取连续登录失败锁定阈值（默认 5 次）。"""
    return int(get_config(session, ConfigKey.LOGIN_FAIL_LIMIT, default=5))


def get_login_lock_minutes(session: Session) -> int:
    """读取锁定时长（分钟，默认 15）。"""
    return int(get_config(session, ConfigKey.LOGIN_LOCK_MINUTES, default=15))


def get_session_timeout_minutes(session: Session) -> int:
    """读取会话超时（分钟，默认 120，与 ACCESS_TOKEN_EXPIRE_MINUTES 对齐）。"""
    return int(get_config(session, ConfigKey.SESSION_TIMEOUT, default=120))
