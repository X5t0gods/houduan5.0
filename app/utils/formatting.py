"""格式化工具 - 金额/数量/时间的格式化与转换。

本模块提供通用的格式化函数，供各层按需调用。
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.core.config import settings


def format_money(value: Decimal | float | int | None, default: str = "0.00") -> str:
    """格式化金额为两位小数字符串。

    使用 ROUND_HALF_UP 舍入规则（四舍五入），与抹零参数一致。

    Args:
        value: 金额值（Decimal/float/int），None 时返回默认值。
        default: 值为 None 时的默认返回。

    Returns:
        格式化后的金额字符串，如 "12.30"。
    """
    if value is None:
        return default
    decimal_value = Decimal(str(value))
    return str(decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def format_qty(value: Decimal | float | int | None, default: str = "0.000") -> str:
    """格式化数量为三位小数字符串（支持称重商品）。

    Args:
        value: 数量值，None 时返回默认值。
        default: 值为 None 时的默认返回。

    Returns:
        格式化后的数量字符串，如 "1.500"。
    """
    if value is None:
        return default
    decimal_value = Decimal(str(value))
    return str(decimal_value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def format_datetime(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化 datetime 为字符串。

    Args:
        dt: datetime 对象，None 时返回空字符串。
        fmt: 格式字符串，默认 YYYY-MM-DD HH:mm:ss。

    Returns:
        格式化后的时间字符串。
    """
    if dt is None:
        return ""
    return dt.strftime(fmt)


def now_str() -> str:
    """获取当前时间字符串（使用配置的时区）。

    Returns:
        格式为 YYYY-MM-DD HH:mm:ss 的当前时间。
    """
    return datetime.now(ZoneInfo(settings.TZ)).strftime("%Y-%m-%d %H:%M:%S")


def to_decimal(value: float | int | str | None, default: Decimal = Decimal("0")) -> Decimal:
    """安全转换为 Decimal（避免 float 精度问题）。

    金额运算全程使用 Decimal，禁止 float 参与。

    Args:
        value: 待转换的值，None 时返回默认值。
        default: 值为 None 时的默认 Decimal。

    Returns:
        Decimal 实例。
    """
    if value is None:
        return default
    return Decimal(str(value))
