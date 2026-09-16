"""采购收货服务测试（阶段 6 CP4 交付物）—— 本阶段核心。

覆盖 spec 验收项 5-20：
- MAVG 成本重算（手工验算 4.2960）
- 原库存为 0 边界（用本次单价，不除零）
- 二次收货累加、部分收货状态 PARTIAL→FINISHED
- 超收 4002、状态校验 4001
- 批次只给保质期商品建（1 保质期+1 非保质期 → 只 1 条批次）
- 保质期缺到期日 2001
- 库存流水 PURCHASE_IN、应付增加
- 无单入库（order FINISHED + is_direct=1 + order_id 非空）
- 采购退货（扣库存 + PURCHASE_RETURN_OUT + 冲减应付允许负）
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.purchase import (
    AuditRequest,
    DirectReceiveItemIn,
    DirectReceiveParams,
    PurchaseOrderCreate,
    PurchaseOrderItemIn,
    PurchaseReturnItemIn,
    PurchaseReturnParams,
    ReceiveItemIn,
    ReceiveParams,
)
from app.services import purchase_service, receipt_service

# 保质期商品（is_perishable=1）与非保质期商品（=0），用于验收 11
PERISHABLE_PID = 1      # 有机小番茄，store1 qty=42.5 avg_cost=4.2
NON_PERISHABLE_PID = 14  # 可口可乐，store1 qty=168 avg_cost=2.4


@pytest.fixture(autouse=True)
def cleanup_receipt_data() -> Generator[None, None, None]:
    """每个测试后恢复到种子状态（用户选择：测后恢复种子值）。"""
    yield
    s = SessionLocal()
    try:
        # 删采购单据（初始都为空）
        s.execute(text("DELETE FROM pur_receipt_item"))
        s.execute(text("DELETE FROM pur_receipt"))
        s.execute(text("DELETE FROM pur_return_item"))
        s.execute(text("DELETE FROM pur_return"))
        s.execute(text("DELETE FROM pur_order_item"))
        s.execute(text("DELETE FROM pur_order"))
        # 删采购相关流水
        s.execute(text(
            "DELETE FROM inv_stock_flow WHERE flow_type IN ('PURCHASE_IN','PURCHASE_RETURN_OUT')"
        ))
        # 删测试批次（保留 4 条 demo：P000001-20260910-01 等）
        s.execute(text(
            "DELETE FROM inv_batch WHERE batch_no NOT IN "
            "('P000001-20260910-01','P000003-20260901-01',"
            "'P000005-20260901-01','P000006-20260913-01')"
        ))
        # 删门店2库存（收货/无单入库可能新建）
        s.execute(text("DELETE FROM inv_stock WHERE store_id=2"))
        # 恢复门店1种子库存（quantity + avg_cost）
        s.execute(text(
            "UPDATE inv_stock SET quantity=42.500, avg_cost=4.2000 "
            "WHERE store_id=1 AND product_id=1"
        ))
        s.execute(text(
            "UPDATE inv_stock SET quantity=18.200, avg_cost=1.8000 "
            "WHERE store_id=1 AND product_id=2"
        ))
        s.execute(text(
            "UPDATE inv_stock SET quantity=168.000, avg_cost=2.4000 "
            "WHERE store_id=1 AND product_id=14"
        ))
        # 恢复供应商应付余额种子值
        s.execute(text("UPDATE pur_supplier SET balance_payable=0.00 WHERE id=1"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=12860.00 WHERE id=2"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=4320.50 WHERE id=3"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=8640.00 WHERE id=4"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=0.00 WHERE id=5"))
        # 重置取号器
        s.execute(text("DELETE FROM sys_no_seq WHERE seq_key IN ('CG','SH','CT','FK','BATCH')"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def _create_audited_order(
    session: Session, items: list[PurchaseOrderItemIn], supplier_id: int = 1,
) -> int:
    """建单 + 审核通过，返回 order_id（收货前置）。"""
    order = purchase_service.create_order(
        session,
        PurchaseOrderCreate(supplier_id=supplier_id, store_id=1, items=items, remark="TEST"),
        store_id=1, operator_id=1,
    )
    purchase_service.audit_order(
        session, order.id, AuditRequest(approved=True), operator_id=1,
    )
    return order.id


@pytest.mark.integration
class TestMavgCostRecalc:
    """MAVG 成本重算（spec 验收 5/6/7）。"""

    def test_cost_recalc_manual_example(self, db_session: Session) -> None:
        """验收 5：商品1 (42.5@4.2) 收 20@4.50 → qty=62.5, avg_cost=4.2960。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("20"), unit_price=Decimal("4.50")),
        ])
        receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("20"), unit_price=Decimal("4.50"),
                              expiry_date=date(2026, 9, 21)),
            ]),
            operator_id=1,
        )
        row = db_session.execute(text(
            "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).fetchone()
        assert row[0] == Decimal("62.500")       # 42.5 + 20
        assert row[1] == Decimal("4.2960")       # (178.50+90.00)/62.5

    def test_zero_stock_uses_unit_price(self, db_session: Session) -> None:
        """验收 6：门店2 商品1 无库存 → 新建记录，avg_cost=本次单价（不除零）。"""
        # 门店2 无商品1库存
        exists = db_session.execute(text(
            "SELECT COUNT(*) FROM inv_stock WHERE store_id=2 AND product_id=1"
        )).scalar_one()
        assert exists == 0

        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("10"), unit_price=Decimal("5.00")),
        ])
        receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("10"), unit_price=Decimal("5.00"),
                              expiry_date=date(2026, 9, 21)),
            ], store_id=2),
            operator_id=1,
        )
        row = db_session.execute(text(
            "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=2 AND product_id=1"
        )).fetchone()
        assert row is not None
        assert row[0] == Decimal("10.000")
        assert row[1] == Decimal("5.0000")  # 原库存0 → 用本次单价

    def test_second_receive_recalc(self, db_session: Session) -> None:
        """验收 7：二次收货，成本逐次重算。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("30"), unit_price=Decimal("4.50")),
        ])
        # 第一次收 20 @ 4.50 → 4.2960, qty 62.5
        receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("20"), unit_price=Decimal("4.50"),
                              expiry_date=date(2026, 9, 21)),
            ]),
            operator_id=1,
        )
        # 第二次收 10 @ 5.00 → (62.5×4.2960 + 10×5.00)/72.5 = 4.3931
        receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("10"), unit_price=Decimal("5.00"),
                              expiry_date=date(2026, 9, 21)),
            ]),
            operator_id=1,
        )
        row = db_session.execute(text(
            "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).fetchone()
        assert row[0] == Decimal("72.500")
        assert row[1] == Decimal("4.3931")


@pytest.mark.integration
class TestReceiveStatus:
    """收货状态与校验（spec 验收 8/9/10）。"""

    def test_partial_then_finished(self, db_session: Session) -> None:
        """验收 8：2商品只收1个 → PARTIAL；再收完 → FINISHED。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("10"), unit_price=Decimal("4.50")),
            PurchaseOrderItemIn(product_id=14, quantity=Decimal("10"), unit_price=Decimal("2.40")),
        ])
        # 只收商品1
        r1 = receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("10"), unit_price=Decimal("4.50"),
                              expiry_date=date(2026, 9, 21)),
            ]),
            operator_id=1,
        )
        assert r1.order_status == "PARTIAL"
        # 再收商品14（非保质期，无需 expiry）
        r2 = receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=14, receive_qty=Decimal("10"), unit_price=Decimal("2.40")),
            ]),
            operator_id=1,
        )
        assert r2.order_status == "FINISHED"

    def test_over_receive_4002(self, db_session: Session) -> None:
        """验收 9：超收 → 4002。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=14, quantity=Decimal("5"), unit_price=Decimal("2.40")),
        ])
        with pytest.raises(BusinessError) as exc_info:
            receipt_service.receive_order(
                db_session, order_id,
                ReceiveParams(items=[
                    ReceiveItemIn(product_id=14, receive_qty=Decimal("10"), unit_price=Decimal("2.40")),
                ]),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.RECEIVE_QTY_EXCEED
        db_session.rollback()

    def test_receive_draft_order_4001(self, db_session: Session) -> None:
        """验收 10：对 DRAFT 订单收货 → 4001。"""
        order = purchase_service.create_order(
            db_session,
            PurchaseOrderCreate(supplier_id=1, store_id=1, items=[
                PurchaseOrderItemIn(product_id=14, quantity=Decimal("5"), unit_price=Decimal("2.40")),
            ], remark="TEST"),
            store_id=1, operator_id=1,
        )
        # 不审核，直接收货
        with pytest.raises(BusinessError) as exc_info:
            receipt_service.receive_order(
                db_session, order.id,
                ReceiveParams(items=[
                    ReceiveItemIn(product_id=14, receive_qty=Decimal("5"), unit_price=Decimal("2.40")),
                ]),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.ORDER_STATUS_INVALID
        db_session.rollback()


@pytest.mark.integration
class TestReceiveBatch:
    """批次（spec 验收 11/12/13/14/15）。"""

    def test_batch_only_for_perishable(self, db_session: Session) -> None:
        """验收 11：1保质期(商品1)+1非保质期(商品14)，前端都传日期 → 只建 1 条批次。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("5"), unit_price=Decimal("4.50")),
            PurchaseOrderItemIn(product_id=14, quantity=Decimal("5"), unit_price=Decimal("2.40")),
        ])
        # 两个商品都传日期（模拟前端无条件传）
        receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("5"), unit_price=Decimal("4.50"),
                              production_date=date(2026, 9, 16), expiry_date=date(2026, 9, 21)),
                ReceiveItemIn(product_id=14, receive_qty=Decimal("5"), unit_price=Decimal("2.40"),
                              production_date=date(2026, 9, 16), expiry_date=date(2026, 9, 21)),
            ]),
            operator_id=1,
        )
        # 只新增 1 条批次（商品1），demo 4 条 + 1 = 5
        batch_count = db_session.execute(text("SELECT COUNT(*) FROM inv_batch")).scalar_one()
        assert batch_count == 5
        # 非保质期商品14 的收货明细 batch_no 为 NULL
        item14 = db_session.execute(text(
            "SELECT batch_no FROM pur_receipt_item WHERE product_id=14 "
            "ORDER BY id DESC LIMIT 1"
        )).scalar_one()
        assert item14 is None

    def test_batch_no_format(self, db_session: Session) -> None:
        """验收 12：批次号形如 P000001-20260916-01。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("5"), unit_price=Decimal("4.50")),
        ])
        receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=1, receive_qty=Decimal("5"), unit_price=Decimal("4.50"),
                              production_date=date(2026, 9, 16), expiry_date=date(2026, 9, 21)),
            ]),
            operator_id=1,
        )
        batch_no = db_session.execute(text(
            "SELECT batch_no FROM inv_batch WHERE product_id=1 AND batch_no LIKE '%20260916%' "
            "ORDER BY id DESC LIMIT 1"
        )).scalar_one()
        assert batch_no is not None
        assert batch_no.startswith("P000001-")

    def test_perishable_missing_expiry_2001(self, db_session: Session) -> None:
        """验收 13：保质期商品不传 expiry_date → 2001。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("5"), unit_price=Decimal("4.50")),
        ])
        with pytest.raises(BusinessError) as exc_info:
            receipt_service.receive_order(
                db_session, order_id,
                ReceiveParams(items=[
                    ReceiveItemIn(product_id=1, receive_qty=Decimal("5"), unit_price=Decimal("4.50")),
                    # ⛔ 不传 expiry_date
                ]),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING
        db_session.rollback()

    def test_flow_and_payable(self, db_session: Session) -> None:
        """验收 14/15：PURCHASE_IN 流水 direction=1；应付增加收货金额。"""
        order_id = _create_audited_order(db_session, [
            PurchaseOrderItemIn(product_id=14, quantity=Decimal("10"), unit_price=Decimal("2.40")),
        ], supplier_id=2)
        result = receipt_service.receive_order(
            db_session, order_id,
            ReceiveParams(items=[
                ReceiveItemIn(product_id=14, receive_qty=Decimal("10"), unit_price=Decimal("2.40")),
            ]),
            operator_id=1,
        )
        # 流水
        flow = db_session.execute(text(
            "SELECT flow_type, direction, before_qty, after_qty FROM inv_stock_flow "
            "WHERE source_no=:sn ORDER BY id DESC LIMIT 1"
        ), {"sn": result.receipt_no}).fetchone()
        assert flow[0] == "PURCHASE_IN"
        assert flow[1] == 1
        assert flow[2] + Decimal("10") == flow[3]
        # 应付：供应商2 原 12860 + 24.00 = 12884
        payable = db_session.execute(text(
            "SELECT balance_payable FROM pur_supplier WHERE id=2"
        )).scalar_one()
        assert payable == Decimal("12884.00")


@pytest.mark.integration
class TestDirectReceive:
    """无单入库（spec 验收 16/17）。"""

    def test_direct_receive_creates_order_and_receipt(self, db_session: Session) -> None:
        """验收 16：order FINISHED + receipt is_direct=1 + order_id 非空 + 库存增加。"""
        before = db_session.execute(text(
            "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=1 AND product_id=14"
        )).fetchone()
        result = receipt_service.direct_receive(
            db_session,
            DirectReceiveParams(supplier_id=1, store_id=1, items=[
                DirectReceiveItemIn(product_id=14, quantity=Decimal("10"), unit_price=Decimal("2.60")),
            ]),
            store_id=1, operator_id=1,
        )
        assert result.is_direct == 1
        assert result.order_id is not None
        assert result.order_status == "FINISHED"

        # receipt is_direct=1 且 order_id 非空
        rec = db_session.execute(text(
            "SELECT is_direct, order_id FROM pur_receipt WHERE receipt_no=:rn"
        ), {"rn": result.receipt_no}).fetchone()
        assert rec[0] == 1
        assert rec[1] == result.order_id

        # 库存增加 + 成本重算：(168×2.4 + 10×2.6)/178
        after = db_session.execute(text(
            "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=1 AND product_id=14"
        )).fetchone()
        assert after[0] == before[0] + Decimal("10")
        expected_cost = ((before[0] * before[1] + Decimal("10") * Decimal("2.60"))
                         / (before[0] + Decimal("10")))
        assert after[1] == expected_cost.quantize(Decimal("0.0001"))

    def test_direct_receive_stopped_supplier_3001(self, db_session: Session) -> None:
        """验收 17：停用供应商 → 3001。"""
        db_session.execute(text("UPDATE pur_supplier SET status=0 WHERE id=1"))
        db_session.commit()
        try:
            with pytest.raises(BusinessError) as exc_info:
                receipt_service.direct_receive(
                    db_session,
                    DirectReceiveParams(supplier_id=1, store_id=1, items=[
                        DirectReceiveItemIn(product_id=14, quantity=Decimal("5"), unit_price=Decimal("2.40")),
                    ]),
                    store_id=1, operator_id=1,
                )
            assert exc_info.value.code == ErrorCode.SUPPLIER_DISABLED
        finally:
            db_session.execute(text("UPDATE pur_supplier SET status=1 WHERE id=1"))
            db_session.commit()


@pytest.mark.integration
class TestPurchaseReturn:
    """采购退货（spec 验收 18/19/20）。"""

    def test_return_deducts_stock_and_payable(self, db_session: Session) -> None:
        """验收 18：退5件 → 库存减5、PURCHASE_RETURN_OUT direction=-1、应付减少。"""
        before = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=14"
        )).scalar_one()
        result = receipt_service.create_return(
            db_session,
            PurchaseReturnParams(supplier_id=2, store_id=1, reason="TEST退货", items=[
                PurchaseReturnItemIn(product_id=14, quantity=Decimal("5"), unit_price=Decimal("2.40")),
            ]),
            store_id=1, operator_id=1,
        )
        assert result.return_no.startswith("CT")
        # 库存减5
        after = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=14"
        )).scalar_one()
        assert after == before - Decimal("5")
        # 流水
        flow = db_session.execute(text(
            "SELECT flow_type, direction FROM inv_stock_flow WHERE source_no=:sn "
            "ORDER BY id DESC LIMIT 1"
        ), {"sn": result.return_no}).fetchone()
        assert flow[0] == "PURCHASE_RETURN_OUT"
        assert flow[1] == -1
        # 应付减少：12860 - 12.00 = 12848
        assert result.balance_payable == Decimal("12848.00")

    def test_return_insufficient_stock_6003(self, db_session: Session) -> None:
        """验收 19：退货数量 > 库存 → 6003，库存与应付都不变。"""
        with pytest.raises(BusinessError) as exc_info:
            receipt_service.create_return(
                db_session,
                PurchaseReturnParams(supplier_id=2, store_id=1, reason="TEST超退", items=[
                    PurchaseReturnItemIn(product_id=14, quantity=Decimal("99999"), unit_price=Decimal("2.40")),
                ]),
                store_id=1, operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.STOCK_NOT_ENOUGH
        db_session.rollback()

    def test_return_allows_negative_payable(self, db_session: Session) -> None:
        """验收 20：应付余额小于退货金额 → 退货后为负（不报错）。"""
        # 供应商1 balance=0，退货 100 → -100
        result = receipt_service.create_return(
            db_session,
            PurchaseReturnParams(supplier_id=1, store_id=1, reason="TEST负应付", items=[
                PurchaseReturnItemIn(product_id=14, quantity=Decimal("2"), unit_price=Decimal("50.00")),
            ]),
            store_id=1, operator_id=1,
        )
        # 2 × 50 = 100，供应商1 原 0 → -100
        assert result.balance_payable == Decimal("-100.00")
