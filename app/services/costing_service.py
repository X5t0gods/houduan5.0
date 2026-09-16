"""移动加权平均成本（MAVG）算法 —— **纯函数模块**（阶段 6 交付物）。

⚠️ 为什么单独成模块（spec 七）：
   成本算法是论文"成本核算"章节要单独论述的核心逻辑，独立出来便于单元测试与引用。
   ⛔ 本模块**不掺任何数据库操作**，只做纯计算（输入原库存/原成本/入库量/单价 → 输出新成本）。

⚠️ 公式（spec 4.2）：
   ```
   新成本 = (原库存数量 × 原成本 + 本次入库数量 × 本次单价) ÷ (原库存数量 + 本次入库数量)
   ```

⛔ 头号陷阱：原库存 ≤ 0 时公式退化甚至除零 → **直接用本次单价**作为新成本。

⚠️ 精度：avg_cost 是 DECIMAL(12,4) → 量化到 **4 位**，ROUND_HALF_UP。
   中间计算用 Decimal，⛔禁止 float；"原库存×原成本"先乘后加，不要各自量化再相加（累积误差）。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

# 成本精度：DECIMAL(12,4) → 量化到 4 位（0.0001）
COST_PRECISION: Final[Decimal] = Decimal("0.0001")

_ZERO: Final[Decimal] = Decimal("0")


def _to_decimal(value: Decimal | int | str | float | None, default: Decimal = _ZERO) -> Decimal:
    """安全转 Decimal（None 走默认值；float 经 str 避免二进制误差）。"""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def calc_weighted_avg_cost(
    old_qty: Decimal | int | str | float | None,
    old_cost: Decimal | int | str | float | None,
    in_qty: Decimal | int | str | float,
    in_price: Decimal | int | str | float,
) -> Decimal:
    """计算移动加权平均成本（MAVG，spec 4.2）。

    Args:
        old_qty: 原库存数量（可为 0 或负数）。
        old_cost: 原单位成本（DECIMAL(12,4)）。
        in_qty: 本次入库数量（> 0）。
        in_price: 本次入库单价。

    Returns:
        新单位成本（量化到 4 位，ROUND_HALF_UP）。

    处理规则（spec 4.2 表格）：
    - 原库存 > 0：按公式 (old_qty×old_cost + in_qty×in_price) / (old_qty + in_qty)
    - **原库存 ≤ 0**（首次入库/库存为0）：⛔公式退化甚至除零 → 直接用 in_price
    - 入库后总量 ≤ 0（理论上不应发生，in_qty>0 且 old_qty≤0 时总量=in_qty+old_qty 可能≤0）：
      兜底用 in_price，绝不除零

    Examples:
        >>> calc_weighted_avg_cost("42.5", "4.2000", "20", "4.50")
        Decimal('4.2960')
        >>> calc_weighted_avg_cost("0", "0", "10", "3.50")   # 原库存0 → 用本次单价
        Decimal('3.5000')
    """
    oq = _to_decimal(old_qty)
    oc = _to_decimal(old_cost)
    iq = _to_decimal(in_qty)
    ip = _to_decimal(in_price)

    total_qty = oq + iq

    # ⛔ 边界：原库存 ≤ 0 或 入库后总量 ≤ 0 → 直接用本次单价（避免除零/负数权重）
    if oq <= _ZERO or total_qty <= _ZERO:
        return ip.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)

    # 正常：先乘后加，最后一次性量化（不要各自量化再相加，会累积误差）
    total_value = oq * oc + iq * ip
    new_cost = total_value / total_qty
    return new_cost.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


def calc_receipt_amount(
    qty: Decimal | int | str | float,
    unit_price: Decimal | int | str | float,
) -> Decimal:
    """收货行金额 = 数量 × 单价（量化到分 DECIMAL(12,2)）。

    ⚠️ 与成本（4 位）不同，金额是 2 位。用 app.utils.money.money 保持一致。
    """
    from app.utils.money import money

    return money(_to_decimal(qty) * _to_decimal(unit_price))
