"""报损与调拨测试（阶段 5 CP3 交付物）。

覆盖 spec 验收项 19-26：
- 报损多明细（inv_loss 1 + inv_loss_item 2，total=两条之和）
- 报损扣库存 + LOSS_OUT 流水（direction=-1，before-qty=after）
- 报损库存不足 → 6003，库存不变
- 报损枚举：BREAK/EXPIRE/FRESH/OTHER 成功，BREAKAGE 被拒
- 调拨跨门店：门店1减、门店2增，4条流水（2出2入），status=IN
- 调拨同门店被拒 → 2001
- 调拨建新库存记录（调入门店原无该商品 → 自动创建 safe_qty=0）
"""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.inventory import (
    LossCreateRequest,
    LossItemIn,
    TransferCreateRequest,
    TransferItemIn,
)
from app.services import loss_service, transfer_service


@pytest.fixture(autouse=True)
def cleanup_loss_transfer() -> Generator[None, None, None]:
    """每个测试后清理报损/调拨数据 + 恢复库存。"""
    yield
    s = SessionLocal()
    try:
        # 清报损
        rows = s.execute(text("SELECT id FROM inv_loss WHERE reason LIKE '%测试%'")).fetchall()
        for (lid,) in rows:
            s.execute(text("DELETE FROM inv_loss_item WHERE loss_id=:l"), {"l": lid})
            s.execute(text("DELETE FROM inv_loss WHERE id=:l"), {"l": lid})
        # 清调拨
        rows = s.execute(text("SELECT id FROM inv_transfer WHERE remark LIKE '%测试%'")).fetchall()
        for (tid,) in rows:
            s.execute(text("DELETE FROM inv_transfer_item WHERE transfer_id=:t"), {"t": tid})
            s.execute(text("DELETE FROM inv_transfer WHERE id=:t"), {"t": tid})
        # 清流水
        s.execute(text("DELETE FROM inv_stock_flow WHERE flow_type IN ('LOSS_OUT','TRANSFER_IN','TRANSFER_OUT')"))
        # 恢复门店1库存
        s.execute(text("UPDATE inv_stock SET quantity=42.500 WHERE store_id=1 AND product_id=1"))
        s.execute(text("UPDATE inv_stock SET quantity=18.200 WHERE store_id=1 AND product_id=2"))
        # 删除调拨到门店2可能新建的库存记录（门店2初始无库存）
        s.execute(text("DELETE FROM inv_stock WHERE store_id=2"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestLoss:
    """报损（spec 验收 19-22）。"""

    def test_loss_multi_items(self, db_session: Session) -> None:
        """验收 19：2 个商品的 items 数组 → inv_loss 1 + inv_loss_item 2，total=两条之和。"""
        params = LossCreateRequest(
            loss_type="BREAK",
            remark="测试报损多明细",
            store_id=1,
            items=[
                LossItemIn(product_id=1, quantity=Decimal("2")),
                LossItemIn(product_id=2, quantity=Decimal("1")),
            ],
        )
        result = loss_service.create_loss(db_session, params, store_id=1, user_id=1)
        assert result.loss_no.startswith("BS")
        assert result.item_count == 2
        assert result.status == "FINISHED"

        # 明细 2 条
        items = db_session.execute(text(
            "SELECT COUNT(*) FROM inv_loss_item WHERE loss_id=:l"
        ), {"l": result.id}).scalar_one()
        assert items == 2

        # total_amount = 两条之和
        lines = db_session.execute(text(
            "SELECT amount FROM inv_loss_item WHERE loss_id=:l"
        ), {"l": result.id}).fetchall()
        assert result.total_amount == sum(Decimal(str(x[0])) for x in lines)

    def test_loss_deducts_stock_and_flow(self, db_session: Session) -> None:
        """验收 20：报损 2 件 → quantity 减 2，LOSS_OUT 流水 direction=-1，before-2=after。"""
        before = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()

        params = LossCreateRequest(
            loss_type="FRESH", remark="测试报损扣库存", store_id=1,
            items=[LossItemIn(product_id=1, quantity=Decimal("2"))],
        )
        result = loss_service.create_loss(db_session, params, store_id=1, user_id=1)

        after = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()
        assert after == before - 2

        flow = db_session.execute(text(
            "SELECT flow_type, direction, quantity, before_qty, after_qty FROM inv_stock_flow "
            "WHERE source_no=:sn ORDER BY id DESC LIMIT 1"
        ), {"sn": result.loss_no}).fetchone()
        assert flow[0] == "LOSS_OUT"
        assert flow[1] == -1
        assert flow[3] - flow[2] == flow[4]  # before - qty = after

    def test_loss_insufficient_stock(self, db_session: Session) -> None:
        """验收 21：报损数量 > 库存 → 6003，库存不变。"""
        before = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()
        params = LossCreateRequest(
            loss_type="BREAK", remark="测试库存不足", store_id=1,
            items=[LossItemIn(product_id=1, quantity=Decimal("99999"))],
        )
        with pytest.raises(BusinessError) as exc_info:
            loss_service.create_loss(db_session, params, store_id=1, user_id=1)
        assert exc_info.value.code == ErrorCode.STOCK_NOT_ENOUGH
        db_session.rollback()
        after = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()
        assert after == before

    def test_loss_type_short_names_ok(self, db_session: Session) -> None:
        """验收 22：BREAK/EXPIRE/FRESH/OTHER 全部成功。"""
        for lt in ["BREAK", "EXPIRE", "FRESH", "OTHER"]:
            params = LossCreateRequest(
                loss_type=lt, remark=f"测试枚举{lt}", store_id=1,
                items=[LossItemIn(product_id=1, quantity=Decimal("0.1"))],
            )
            result = loss_service.create_loss(db_session, params, store_id=1, user_id=1)
            assert result.loss_type == lt

    def test_loss_type_breakage_rejected(self, db_session: Session) -> None:
        """验收 22：传 BREAKAGE（文档写法）→ 被拒（证明用短名）。"""
        params = LossCreateRequest(
            loss_type="BREAKAGE", remark="测试文档写法被拒", store_id=1,
            items=[LossItemIn(product_id=1, quantity=Decimal("1"))],
        )
        with pytest.raises(BusinessError) as exc_info:
            loss_service.create_loss(db_session, params, store_id=1, user_id=1)
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING


@pytest.mark.integration
class TestTransfer:
    """调拨（spec 验收 23-25）。"""

    def test_transfer_cross_store(self, db_session: Session) -> None:
        """验收 23：1→2 两个商品 → 门店1减、门店2增，4条流水（2出2入），status=IN。"""
        before1 = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()

        params = TransferCreateRequest(
            from_store_id=1, to_store_id=2, remark="测试调拨跨门店",
            items=[
                TransferItemIn(product_id=1, quantity=Decimal("5")),
                TransferItemIn(product_id=2, quantity=Decimal("3")),
            ],
        )
        result = transfer_service.create_transfer(db_session, params, user_id=1)
        assert result.status == "IN"
        assert result.transfer_no.startswith("DB")

        # 门店1减少5
        after1 = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()
        assert after1 == before1 - 5

        # 门店2增加5（新建记录）
        store2_qty = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=2 AND product_id=1"
        )).scalar_one()
        assert store2_qty == Decimal("5.000")

        # 4 条流水（2 出 2 入）
        flows = db_session.execute(text(
            "SELECT flow_type, direction FROM inv_stock_flow WHERE source_no=:sn"
        ), {"sn": result.transfer_no}).fetchall()
        assert len(flows) == 4
        out_count = sum(1 for f in flows if f[0] == "TRANSFER_OUT")
        in_count = sum(1 for f in flows if f[0] == "TRANSFER_IN")
        assert out_count == 2
        assert in_count == 2

    def test_transfer_same_store_rejected(self, db_session: Session) -> None:
        """验收 24：from == to → 2001。"""
        params = TransferCreateRequest(
            from_store_id=1, to_store_id=1, remark="测试同门店",
            items=[TransferItemIn(product_id=1, quantity=Decimal("1"))],
        )
        with pytest.raises(BusinessError) as exc_info:
            transfer_service.create_transfer(db_session, params, user_id=1)
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING

    def test_transfer_creates_new_stock(self, db_session: Session) -> None:
        """验收 25：调入门店原无该商品 → 自动创建 inv_stock（safe_qty=0）。"""
        # 门店2初始无商品1库存
        exists = db_session.execute(text(
            "SELECT COUNT(*) FROM inv_stock WHERE store_id=2 AND product_id=1"
        )).scalar_one()
        assert exists == 0

        params = TransferCreateRequest(
            from_store_id=1, to_store_id=2, remark="测试建新库存",
            items=[TransferItemIn(product_id=1, quantity=Decimal("3"))],
        )
        transfer_service.create_transfer(db_session, params, user_id=1)

        row = db_session.execute(text(
            "SELECT quantity, safe_qty FROM inv_stock WHERE store_id=2 AND product_id=1"
        )).fetchone()
        assert row is not None
        assert row[0] == Decimal("3.000")
        assert row[1] == Decimal("0.000")  # safe_qty 默认 0

    def test_transfer_insufficient_stock(self, db_session: Session) -> None:
        """调出库存不足 → 6003。"""
        params = TransferCreateRequest(
            from_store_id=1, to_store_id=2, remark="测试调拨库存不足",
            items=[TransferItemIn(product_id=1, quantity=Decimal("99999"))],
        )
        with pytest.raises(BusinessError) as exc_info:
            transfer_service.create_transfer(db_session, params, user_id=1)
        assert exc_info.value.code == ErrorCode.STOCK_NOT_ENOUGH
