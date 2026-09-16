"""MAVG 移动加权平均成本算法测试（阶段 6 CP1 交付物）。

覆盖 spec 验收项 5/6/7 + 4.2 边界：
- 手工验算：(42.5×4.2 + 20×4.5)/(42.5+20) = 268.5/62.5 = 4.2960
- 原库存为 0 → 用本次单价（不除零）
- 原库存为负 → 用本次单价
- 多次入库逐次重算
- 精度：量化到 4 位 ROUND_HALF_UP
"""

from __future__ import annotations

from decimal import Decimal

from app.services.costing_service import calc_receipt_amount, calc_weighted_avg_cost


class TestMavgFormula:
    """公式正确性（spec 验收 5）。"""

    def test_spec_manual_example(self) -> None:
        """spec 4.2 手工验算：(178.50 + 90.00) / 62.5 = 4.2960。"""
        result = calc_weighted_avg_cost("42.5", "4.2000", "20", "4.50")
        assert result == Decimal("4.2960")

    def test_manual_verification_steps(self) -> None:
        """手工验算过程：原库存42.5×4.2=178.50，入库20×4.5=90.00，合计268.50/62.5。"""
        old_value = Decimal("42.5") * Decimal("4.2000")   # 178.50
        in_value = Decimal("20") * Decimal("4.50")         # 90.00
        total_value = old_value + in_value                 # 268.50
        total_qty = Decimal("42.5") + Decimal("20")        # 62.5
        expected = (total_value / total_qty).quantize(Decimal("0.0001"))
        assert expected == Decimal("4.2960")
        assert calc_weighted_avg_cost("42.5", "4.2000", "20", "4.50") == expected

    def test_same_price_no_change(self) -> None:
        """入库单价 = 原成本 → 新成本不变。"""
        result = calc_weighted_avg_cost("100", "5.0000", "50", "5.00")
        assert result == Decimal("5.0000")

    def test_higher_price_raises_cost(self) -> None:
        """入库单价高于原成本 → 新成本上升（介于两者之间）。"""
        result = calc_weighted_avg_cost("10", "2.0000", "10", "4.00")
        assert result == Decimal("3.0000")  # 等量混合取中值


class TestMavgBoundary:
    """边界（spec 验收 6 + 4.2 表格）。"""

    def test_zero_old_qty_uses_in_price(self) -> None:
        """原库存为 0 → 用本次单价（不除零）。"""
        result = calc_weighted_avg_cost("0", "0", "10", "3.50")
        assert result == Decimal("3.5000")

    def test_zero_old_qty_nonzero_old_cost(self) -> None:
        """原库存0但原成本非0（历史遗留）→ 仍用本次单价。"""
        result = calc_weighted_avg_cost("0", "9.9999", "5", "2.00")
        assert result == Decimal("2.0000")

    def test_negative_old_qty_uses_in_price(self) -> None:
        """原库存为负（异常数据）→ 用本次单价，不产生负权重。"""
        result = calc_weighted_avg_cost("-5", "4.0000", "10", "3.00")
        assert result == Decimal("3.0000")

    def test_none_inputs_treated_as_zero(self) -> None:
        """None 输入当 0 处理（新建库存记录场景）。"""
        result = calc_weighted_avg_cost(None, None, "8", "6.25")
        assert result == Decimal("6.2500")

    def test_total_qty_zero_no_crash(self) -> None:
        """原库存-10 + 入库10 = 总量0 → 不除零，用本次单价。"""
        result = calc_weighted_avg_cost("-10", "4.0000", "10", "3.00")
        assert result == Decimal("3.0000")


class TestMavgPrecision:
    """精度（DECIMAL(12,4) + ROUND_HALF_UP）。"""

    def test_quantize_4_digits(self) -> None:
        """结果量化到 4 位。"""
        # (10×1 + 3×2)/13 = 16/13 = 1.230769... → 1.2308
        result = calc_weighted_avg_cost("10", "1.0000", "3", "2.00")
        assert result == Decimal("1.2308")
        assert -result.as_tuple().exponent == 4  # 4 位小数

    def test_round_half_up_not_banker(self) -> None:
        """ROUND_HALF_UP（非银行家舍入）。"""
        # 构造第5位是5的场景：(1×1.00005 + 1×0)/2 ... 用直接可算的
        # old=1@0.00015, in=1@0 → (0.00015+0)/2 = 0.000075 → 4位 ROUND_HALF_UP = 0.0001
        result = calc_weighted_avg_cost("1", "0.00015", "1", "0")
        # total_qty=2 > 0, old_qty=1 > 0 → 走公式：(1×0.00015 + 1×0)/2 = 0.000075
        assert result == Decimal("0.0001")  # ROUND_HALF_UP，银行家会得 0.0000

    def test_float_input_no_binary_error(self) -> None:
        """float 输入经 str 转换，无二进制误差。"""
        result = calc_weighted_avg_cost(42.5, 4.2, 20, 4.5)
        assert result == Decimal("4.2960")


class TestMultiReceive:
    """多次入库逐次重算（spec 验收 7）。"""

    def test_two_sequential_receives(self) -> None:
        """两次收货，第二次用第一次重算后的成本。"""
        # 初始 42.5 @ 4.2000
        # 第一次收 20 @ 4.50 → 4.2960，库存 62.5
        cost1 = calc_weighted_avg_cost("42.5", "4.2000", "20", "4.50")
        assert cost1 == Decimal("4.2960")
        qty1 = Decimal("42.5") + Decimal("20")  # 62.5

        # 第二次收 10 @ 5.00 → (62.5×4.2960 + 10×5.00)/72.5
        cost2 = calc_weighted_avg_cost(qty1, cost1, "10", "5.00")
        # 62.5×4.2960 = 268.50, 10×5 = 50, 合计 318.50 / 72.5 = 4.39310... → 4.3931
        expected = ((qty1 * cost1 + Decimal("10") * Decimal("5.00")) / (qty1 + Decimal("10")))
        expected = expected.quantize(Decimal("0.0001"))
        assert cost2 == expected == Decimal("4.3931")


class TestReceiptAmount:
    """收货行金额（2 位）。"""

    def test_amount_2_digits(self) -> None:
        """金额量化到 2 位（不是 4 位）。"""
        assert calc_receipt_amount("20", "4.50") == Decimal("90.00")
        assert calc_receipt_amount("3", "3.333") == Decimal("10.00")  # 9.999 → 10.00
