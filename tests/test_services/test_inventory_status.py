"""库存状态判定测试（阶段 5 CP1 交付物）。

覆盖 spec 验收项 3/4/6/8：
- LOW 判定（quantity <= safe_qty，含等于）
- stock_status 优先级（LOW > NEAR_EXPIRY > OVER > NORMAL）
- remain_days 可为负（已过期），不依赖 inv_batch.status
- stock_amount = quantity × avg_cost（量化到分）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.repositories.inventory_repository import (
    compute_stock_status,
    get_nearest_expiry_map,
    list_stock,
)
from app.utils.money import money


class TestComputeStockStatus:
    """纯函数：状态优先级（spec 4.2）。"""

    def test_low_when_qty_le_safe(self) -> None:
        """quantity <= safe_qty → LOW（含等于）。"""
        assert compute_stock_status(Decimal("18.2"), Decimal("20"), Decimal("200"), None, 30) == "LOW"
        assert compute_stock_status(Decimal("20"), Decimal("20"), Decimal("200"), None, 30) == "LOW"

    def test_over_when_qty_gt_max(self) -> None:
        """quantity > max_qty → OVER。"""
        assert compute_stock_status(Decimal("150"), Decimal("10"), Decimal("100"), None, 30) == "OVER"

    def test_over_not_triggered_when_max_none(self) -> None:
        """max_qty 为 None → 不判 OVER。"""
        assert compute_stock_status(Decimal("9999"), Decimal("10"), None, None, 30) == "NORMAL"

    def test_near_expiry(self) -> None:
        """剩余天数 <= 阈值 → NEAR_EXPIRY。"""
        assert compute_stock_status(Decimal("50"), Decimal("10"), Decimal("200"), 15, 30) == "NEAR_EXPIRY"
        # 已过期（负数）也算临期
        assert compute_stock_status(Decimal("50"), Decimal("10"), Decimal("200"), -1, 30) == "NEAR_EXPIRY"

    def test_normal(self) -> None:
        """都不满足 → NORMAL。"""
        assert compute_stock_status(Decimal("50"), Decimal("10"), Decimal("200"), None, 30) == "NORMAL"
        assert compute_stock_status(Decimal("50"), Decimal("10"), Decimal("200"), 60, 30) == "NORMAL"

    def test_priority_low_over_near_expiry(self) -> None:
        """优先级：既 LOW 又临期 → LOW（spec 验收 6）。"""
        # quantity <= safe_qty 且 remain_days <= 30
        assert compute_stock_status(Decimal("5"), Decimal("10"), Decimal("200"), 10, 30) == "LOW"

    def test_priority_near_expiry_over_over(self) -> None:
        """优先级：既临期又积压 → NEAR_EXPIRY。"""
        # quantity > max_qty 且 remain_days <= 30，但 quantity > safe_qty
        assert compute_stock_status(Decimal("150"), Decimal("10"), Decimal("100"), 10, 30) == "NEAR_EXPIRY"


class TestStockAmountRounding:
    """stock_amount 量化（spec 验收 2）。"""

    def test_stock_amount_product_1(self) -> None:
        """商品 1：42.5 × 4.2 = 178.50。"""
        assert money(Decimal("42.5") * Decimal("4.2")) == Decimal("178.50")


@pytest.mark.integration
class TestStockQueryRealData:
    """真实数据验证（spec 验收 1/3/4/8）。"""

    def test_product_2_and_4_are_low(self, db_session: Session) -> None:
        """商品 2（18.2≤20）、商品 4（8.6≤10）判为 LOW。"""
        rows, total = list_stock(db_session, page=1, page_size=100, store_id=1)
        status_map = {r["product_id"]: r["stock_status"] for r in rows}
        assert status_map.get(2) == "LOW"
        assert status_map.get(4) == "LOW"

    def test_product_1_near_expiry_negative_days(self, db_session: Session) -> None:
        """商品 1 批次 expiry=2026-09-15 已过期 → remain_days 负数（spec 验收 4/8）。

        ⚠️ 商品 1 quantity=42.5 > safe_qty=20，不满足 LOW，
           但有已过期批次 → 应判 NEAR_EXPIRY。
        """
        rows, _ = list_stock(db_session, page=1, page_size=100, store_id=1)
        p1 = next((r for r in rows if r["product_id"] == 1), None)
        assert p1 is not None
        # remain_days 应为负数（2026-09-15 已过，当前 2026-09-16）
        assert p1["remain_days"] is not None
        assert p1["remain_days"] < 0
        # 状态：quantity(42.5) > safe_qty(20) 不 LOW，有临期批次 → NEAR_EXPIRY
        assert p1["stock_status"] == "NEAR_EXPIRY"

    def test_stock_amount_is_decimal(self, db_session: Session) -> None:
        """商品 1 stock_amount = 178.50（spec 验收 2）。"""
        rows, _ = list_stock(db_session, page=1, page_size=100, store_id=1)
        p1 = next((r for r in rows if r["product_id"] == 1), None)
        assert p1 is not None
        assert p1["stock_amount"] == Decimal("178.50")

    def test_nearest_expiry_map_ignores_status(self, db_session: Session) -> None:
        """get_nearest_expiry_map 不依赖 inv_batch.status（spec 验收 8）。

        批次 1（商品 1）status=1 但已过期，仍应返回负 remain_days。
        """
        expiry_map = get_nearest_expiry_map(db_session, 1, [1])
        assert 1 in expiry_map
        expiry_date, remain_days = expiry_map[1]
        assert expiry_date == date(2026, 9, 15)
        assert remain_days < 0

    def test_list_stock_returns_20(self, db_session: Session) -> None:
        """门店 1 库存 20 条（spec 验收 1）。"""
        rows, total = list_stock(db_session, page=1, page_size=100, store_id=1)
        assert total == 20
        assert len(rows) == 20
        # 每条含必需字段
        for r in rows:
            assert r["unit_name"] is not None or r["product_id"] == 0
            assert "stock_amount" in r
            assert "stock_status" in r
            assert "remain_days" in r
