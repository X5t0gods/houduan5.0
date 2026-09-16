"""money() 量化工具测试（阶段 4 CP1 交付物）。

⚠️ 覆盖 spec 验收项 1：`money(Decimal("2.675")) == Decimal("2.68")` 且与 Python
   内置 `round()` 的银行家舍入行为**不同**（差 1 分钱会导致前后端 7001）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.utils.money import CENT, ZERO, money, money_max_zero, to_decimal


class TestMoneyRounding:
    """ROUND_HALF_UP 与 Python round() 的差异（spec 第四节头号陷阱）。"""

    def test_2_675_rounds_to_2_68(self) -> None:
        """⚠️ 关键：money(2.675) = 2.68（四舍五入），Python round(2.675, 2) = 2.67（银行家）。"""
        assert money(Decimal("2.675")) == Decimal("2.68")
        # 对比 Python 内置 round（银行家舍入）
        assert round(2.675, 2) == 2.67  # 差 1 分钱！
        assert round(2.675, 2) != 2.68

    def test_0_125_rounds_to_0_13(self) -> None:
        """spec 验收项 1 明确要求断言：money(0.125) = 0.13。"""
        assert money(Decimal("0.125")) == Decimal("0.13")
        # Python 内置 round 会得 0.12（银行家舍入到偶数）
        assert round(0.125, 2) == 0.12

    def test_0_135_rounds_to_0_14(self) -> None:
        """另一个银行家舍入 vs 四舍五入的差异点。"""
        assert money(Decimal("0.135")) == Decimal("0.14")

    def test_negative_half_up(self) -> None:
        """负数也走 HALF_UP：-2.675 → -2.68（远离零方向）。"""
        assert money(Decimal("-2.675")) == Decimal("-2.68")

    def test_integer_input(self) -> None:
        """int 输入 → 量化到 2 位小数。"""
        assert money(3) == Decimal("3.00")
        assert money(0) == Decimal("0.00")

    def test_string_input(self) -> None:
        """字符串输入避免浮点误差。"""
        assert money("1.005") == Decimal("1.01")
        assert money("0.1") == Decimal("0.10")

    def test_float_input_via_str(self) -> None:
        """⚠️ float 输入必须先经 str() 转 Decimal，避免二进制浮点表示误差。

        Decimal(0.1) 实际是 0.1000000000000000055511151231257827...
        Decimal(str(0.1)) 才是精确的 0.1
        """
        assert money(0.1) == Decimal("0.10")
        assert money(2.675) == Decimal("2.68")  # 若直接 Decimal(2.675) 会得 2.67

    def test_already_2_decimals_unchanged(self) -> None:
        """已是 2 位小数的值不变。"""
        assert money(Decimal("12.34")) == Decimal("12.34")
        assert money(Decimal("0.00")) == Decimal("0.00")

    def test_more_precision_truncated(self) -> None:
        """超过 2 位小数的部分按 HALF_UP 舍入。"""
        assert money(Decimal("1.234")) == Decimal("1.23")
        assert money(Decimal("1.236")) == Decimal("1.24")
        assert money(Decimal("1.2350000")) == Decimal("1.24")  # HALF_UP

    def test_cent_constant(self) -> None:
        """CENT 常量应为 Decimal('0.01')。"""
        assert Decimal("0.01") == CENT

    def test_zero_constant(self) -> None:
        """ZERO 常量应为 Decimal('0.00')。"""
        assert Decimal("0.00") == ZERO


class TestMoneyMaxZero:
    """money_max_zero 用于 receivable 等不允许负数的场景。"""

    def test_positive_unchanged(self) -> None:
        assert money_max_zero(Decimal("5.00")) == Decimal("5.00")

    def test_negative_becomes_zero(self) -> None:
        """优惠超过总额时 receivable 应为 0，不能是负数。"""
        assert money_max_zero(Decimal("-3.00")) == Decimal("0.00")

    def test_zero_stays_zero(self) -> None:
        assert money_max_zero(Decimal("0")) == Decimal("0.00")


class TestToDecimal:
    """to_decimal 只做类型转换，不量化。"""

    def test_none_returns_default(self) -> None:
        assert to_decimal(None) == ZERO
        assert to_decimal(None, Decimal("1.00")) == Decimal("1.00")

    def test_decimal_passthrough(self) -> None:
        v = Decimal("1.234567")
        assert to_decimal(v) is v  # 同一对象
        assert to_decimal(v) == Decimal("1.234567")  # 未量化

    def test_float_via_str(self) -> None:
        assert to_decimal(0.1) == Decimal("0.1")

    def test_int_and_str(self) -> None:
        assert to_decimal(5) == Decimal("5")
        assert to_decimal("1.5") == Decimal("1.5")


class TestNoFloatContamination:
    """确保返回类型永远是 Decimal，不会漏 float。"""

    @pytest.mark.parametrize("value", [
        Decimal("1.00"), 1, "1.0", 1.0, Decimal("2.675"),
    ])
    def test_returns_decimal(self, value) -> None:
        result = money(value)
        assert isinstance(result, Decimal), f"money({value}) 返回了 {type(result)}，必须是 Decimal"
