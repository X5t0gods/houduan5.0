"""金额量化工具（阶段 4 交付物）—— 与前端 JS `Math.round(n*100)/100` 严格一致。

⚠️ 头号陷阱（spec 第四节）
------------------------
- 前端 JS：`Math.round(n * 100) / 100` = **四舍五入**
- Python 内置 `round(x, 2)` = **银行家舍入**（四舍六入五取偶）
  * `round(2.675, 2)` = **2.67**（Python）
  * `Math.round(2.675 * 100) / 100` = **2.68**（JS）
- 差 1 分钱就会导致前后端 `receivable` 不一致 → 结算校验 `7001` → **收银台瘫痪**

⛔ 强制约束
----------
1. 全程用 `decimal.Decimal`，**禁止 float 参与金额运算**
2. 只使用本模块的 `money()` 函数做量化，⛔ **禁止**在金额计算中出现：
   - `round(x, 2)`（银行家舍入）
   - `float(x)`（浮点误差）
   - `//`（整除会丢精度）
   - `%`（模运算同上）
3. 中间计算（如折扣乘法）保留更高精度，**只在最终落库/输出前**量化一次

用法：
    from app.utils.money import money
    total = money(price * quantity)  # 只在最后一步量化
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

# 分位精度（0.01）—— 所有金额量化的目标精度
CENT: Final[Decimal] = Decimal("0.01")

# 零值常量（避免各处重复 Decimal("0")）
ZERO: Final[Decimal] = Decimal("0.00")


def money(value: Decimal | int | str | float) -> Decimal:
    """金额量化到分（0.01），使用 **ROUND_HALF_UP**（四舍五入）。

    ⚠️ **必须与前端 `Math.round(n * 100) / 100` 行为一致**，否则前后端算出的
    `receivable` 会差 1 分钱，触发结算 `7001` 校验失败，收银台不可用。

    ⛔ 不要用 Python 内置 `round(x, 2)` —— 那是**银行家舍入**（四舍六入五取偶），
    例如 `round(2.675, 2) == 2.67`，但 JS `Math.round(2.675 * 100) / 100 == 2.68`。

    Args:
        value: 待量化的值。允许 Decimal / int / str / float。
              ⚠️ 传 float 时先经过 `str(value)` 转成 Decimal，避免浮点表示误差
              （如 `Decimal(0.1) = 0.1000000000000000055511151231257827...`）。

    Returns:
        量化到 0.01 的 Decimal（如 `Decimal("2.68")`）。

    Examples:
        >>> money(Decimal("2.675"))
        Decimal('2.68')
        >>> money(Decimal("0.125"))
        Decimal('0.13')
        >>> money(Decimal("-2.675"))
        Decimal('-2.68')
        >>> money(3)
        Decimal('3.00')
        >>> money("1.005")
        Decimal('1.01')
    """
    # float 走 str() 转 Decimal，避免二进制浮点表示误差
    if isinstance(value, float):
        d = Decimal(str(value))
    elif isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(value)
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def money_max_zero(value: Decimal | int | str | float) -> Decimal:
    """量化到分，且不小于 0（用于 receivable 等金额，防止负数）。

    等价于前端 `round2(Math.max(0, x))`。

    Args:
        value: 待量化的值。

    Returns:
        max(0, money(value))。
    """
    result = money(value)
    return result if result > ZERO else ZERO


def to_decimal(value: Decimal | int | str | float | None, default: Decimal = ZERO) -> Decimal:
    """安全转 Decimal（None 走默认值）。

    ⚠️ 与 money() 的区别：**不做量化**，仅类型转换。用于中间计算保留精度。

    Args:
        value: 待转换的值；None 时返回 default。
        default: 默认值（默认为 0.00）。

    Returns:
        Decimal 实例（未量化）。
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)
