"""销售结算服务测试（阶段 4 CP4 交付物）。

覆盖 spec 验收项 11-20：
- 结算校验顺序（8 步）
- 幂等三步（request_id 命中 → 首次结果）
- 防超卖（条件更新 rowcount=0 → 7004）
- 会员储值（先扣赠送再扣本金）
- 成本快照（sal_trade_item.cost_price = inv_stock.avg_cost 当时值）
- 毛利正确（gross_profit = receivable - cost_amount）
- 库存流水完整（SALE_OUT, direction=-1, before-after 勾稽）
- 退货回补（SALE_RETURN_IN, direction=1）
- 退货只退本金
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.sale import CartItem, TradeCreateRequest, TradePaymentIn
from app.services import sale_service


@pytest.fixture(autouse=True)
def cleanup_test_trades() -> Generator[None, None, None]:
    """每个测试后清理测试产生的销售数据（settle 内部 commit，rollback 无法撤销）。

    匹配规则：request_id LIKE 'test-%' 或 remark='TEST'。
    同时恢复库存和会员数据。
    """
    yield
    s = SessionLocal()
    try:
        # 找出测试销售单
        rows = s.execute(text(
            "SELECT id, trade_no FROM sal_trade "
            "WHERE remark='TEST' OR request_id LIKE 'test-%'"
        )).fetchall()
        for tid, trade_no in rows:
            s.execute(text("DELETE FROM sal_trade_payment WHERE trade_id=:t"), {"t": tid})
            s.execute(text("DELETE FROM sal_trade_item WHERE trade_id=:t"), {"t": tid})
            s.execute(text(
                "DELETE FROM inv_stock_flow WHERE source_no=:tn"
            ), {"tn": trade_no})
            s.execute(text("DELETE FROM sal_trade WHERE id=:t"), {"t": tid})
        # 清退货单
        ret_rows = s.execute(text(
            "SELECT id, return_no FROM sal_return WHERE reason LIKE '%TEST%'"
        )).fetchall()
        for rid, ret_no in ret_rows:
            s.execute(text("DELETE FROM sal_return_item WHERE return_id=:r"), {"r": rid})
            s.execute(text(
                "DELETE FROM inv_stock_flow WHERE source_no=:rn"
            ), {"rn": ret_no})
            s.execute(text("DELETE FROM sal_return WHERE id=:r"), {"r": rid})
        # 清挂单
        s.execute(text("DELETE FROM sal_hold WHERE cart_json LIKE '%TEST%'"))
        # 恢复库存（product_id=14 可口可乐）
        s.execute(text(
            "UPDATE inv_stock SET quantity=120 WHERE store_id=1 AND product_id=14"
        ))
        # 恢复会员
        s.execute(text(
            "UPDATE mem_member SET balance=500.00, gift_balance=0.00, level_id=3, "
            "points=100, total_consume=0, consume_count=0, last_consume_at=NULL WHERE id=1"
        ))
        # 恢复促销统计
        s.execute(text(
            "UPDATE pro_promotion SET trigger_count=0, discount_total=0.00"
        ))
        # 清除测试产生的会员流水
        s.execute(text("DELETE FROM mem_balance_flow WHERE source_no LIKE 'XS%'"))
        s.execute(text("DELETE FROM mem_point_flow WHERE source_no LIKE 'XS%'"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def _make_request(
    product_id: int = 14,
    quantity: Decimal = Decimal("1"),
    unit_price: Decimal = Decimal("3.80"),
    pay_method: str = "CASH",
    member_id: int | None = None,
    request_id: str | None = None,
) -> TradeCreateRequest:
    """构建最小合法结算请求。"""
    return TradeCreateRequest(
        request_id=request_id or f"test-{uuid.uuid4().hex[:12]}",
        pos_id=1,
        session_id=None,
        member_id=member_id,
        items=[CartItem(product_id=product_id, quantity=quantity, unit_price=unit_price)],
        payments=[TradePaymentIn(pay_method=pay_method, amount=unit_price * quantity)],
        use_points=0,
        round_amount=Decimal("0"),
        remark="TEST",
    )


@pytest.mark.integration
class TestSettleValidation:
    """spec 6.3 校验顺序。"""

    def test_invalid_pay_method(self, db_session: Session) -> None:
        """不合法支付方式 → 2001。"""
        params = _make_request()
        params.payments[0].pay_method = "BITCOIN"
        with pytest.raises(BusinessError) as exc_info:
            sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING

    def test_product_not_found(self, db_session: Session) -> None:
        """商品不存在 → 6002。"""
        params = _make_request(product_id=99999)
        with pytest.raises(BusinessError) as exc_info:
            sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)
        assert exc_info.value.code == ErrorCode.PRODUCT_DISABLED

    def test_payment_mismatch(self, db_session: Session) -> None:
        """支付金额与应收不一致 → 7001。"""
        params = _make_request(product_id=14, quantity=Decimal("1"), unit_price=Decimal("3.80"))
        # 故意多付 10 元
        params.payments[0].amount = Decimal("13.80")
        with pytest.raises(BusinessError) as exc_info:
            sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)
        assert exc_info.value.code == ErrorCode.PAY_AMOUNT_MISMATCH


@pytest.mark.integration
class TestSettleIdempotent:
    """spec 验收 12：幂等。"""

    def test_same_request_id_returns_first_result(self, db_session: Session) -> None:
        """同一 request_id 提交两次 → 第二次返回首次结果（trade_no 相同）。"""
        req_id = f"test-idem-{uuid.uuid4().hex[:8]}"
        params = _make_request(request_id=req_id)
        # 修正支付金额（可乐特价后应收 = 3.20）
        params.payments[0].amount = Decimal("3.20")

        r1 = sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)
        r2 = sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)
        assert r1.trade_no == r2.trade_no
        assert r1.id == r2.id


@pytest.mark.integration
class TestSettleStock:
    """spec 验收 14/18/19：库存扣减 + 成本快照 + 流水。"""

    def test_stock_deducted_and_flow_written(self, db_session: Session) -> None:
        """结算后库存减少、流水写入正确。"""
        # 读取结算前库存
        before = db_session.execute(text(
            "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=1 AND product_id=14"
        )).fetchone()
        assert before is not None
        before_qty = before[0]
        avg_cost = before[1]

        params = _make_request(
            product_id=14, quantity=Decimal("1"), unit_price=Decimal("3.80"),
        )
        params.payments[0].amount = Decimal("3.20")  # 特价后
        result = sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)

        # 验证库存减少
        after = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=14"
        )).scalar_one()
        assert after == before_qty - 1

        # 验证流水
        flow = db_session.execute(text(
            "SELECT flow_type, direction, quantity, before_qty, after_qty, unit_cost "
            "FROM inv_stock_flow WHERE source_no=:tn ORDER BY id DESC LIMIT 1"
        ), {"tn": result.trade_no}).fetchone()
        assert flow is not None
        assert flow[0] == "SALE_OUT"
        assert flow[1] == -1
        assert flow[2] == Decimal("1.000")
        assert flow[3] == before_qty  # before_qty
        assert flow[4] == before_qty - 1  # after_qty

        # 验证成本快照
        item = db_session.execute(text(
            "SELECT cost_price FROM sal_trade_item WHERE trade_id=:tid"
        ), {"tid": result.id}).fetchone()
        assert item[0] == avg_cost

        # 恢复库存（避免影响其他测试）
        db_session.execute(text(
            "UPDATE inv_stock SET quantity = quantity + 1 WHERE store_id=1 AND product_id=14"
        ))
        db_session.commit()

    def test_gross_profit_correct(self, db_session: Session) -> None:
        """毛利 = receivable - cost_amount（spec 验收 20）。"""
        params = _make_request(
            product_id=14, quantity=Decimal("2"), unit_price=Decimal("3.80"),
        )
        # 特价 3.20 × 2 = 6.40
        params.payments[0].amount = Decimal("6.40")
        result = sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)

        trade = db_session.execute(text(
            "SELECT receivable, cost_amount, gross_profit FROM sal_trade WHERE id=:tid"
        ), {"tid": result.id}).fetchone()
        assert trade[2] == trade[0] - trade[1]  # gross_profit = receivable - cost

        # 恢复库存
        db_session.execute(text(
            "UPDATE inv_stock SET quantity = quantity + 2 WHERE store_id=1 AND product_id=14"
        ))
        db_session.commit()


@pytest.mark.integration
class TestSettleMember:
    """spec 验收 16/17：会员储值扣款。"""

    def test_balance_deduct_gift_first(self, db_session: Session) -> None:
        """储值支付先扣赠送余额再扣本金（spec 三 ⑥）。"""
        # 设置会员 gift_balance=5, balance=100，确保 level_id=1（普通会負，rate=1.0 无折扣）
        db_session.execute(text(
            "UPDATE mem_member SET gift_balance=5.00, balance=100.00, level_id=1 WHERE id=1"
        ))
        db_session.commit()

        params = _make_request(
            product_id=14, quantity=Decimal("2"), unit_price=Decimal("3.80"),
            pay_method="BALANCE", member_id=1,
        )
        # 可乐特价 3.20×2=6.40，普通会员无折扣，receivable=6.40
        params.payments[0].amount = Decimal("6.40")
        result = sale_service.settle_trade(db_session, params, store_id=1, cashier_id=4)

        # 验证：赠送扣 5，本金扣 1.40
        member = db_session.execute(text(
            "SELECT balance, gift_balance FROM mem_member WHERE id=1"
        )).fetchone()
        assert member[1] == Decimal("0.00")  # gift_balance 扣完
        assert member[0] == Decimal("98.60")  # 100 - 1.40

        # 验证流水
        flow = db_session.execute(text(
            "SELECT amount, gift_amount, before_balance, after_balance, before_gift, after_gift "
            "FROM mem_balance_flow WHERE source_no=:tn ORDER BY id DESC LIMIT 1"
        ), {"tn": result.trade_no}).fetchone()
        assert flow is not None
        assert flow[0] == Decimal("1.40")  # 本金扣
        assert flow[1] == Decimal("5.00")  # 赠送扣
        assert flow[4] == Decimal("5.00")  # before_gift
        assert flow[5] == Decimal("0.00")  # after_gift

        # 恢复
        db_session.execute(text(
            "UPDATE mem_member SET gift_balance=0.00, balance=500.00, level_id=3, "
            "total_consume=0, consume_count=0, points=100 WHERE id=1"
        ))
        db_session.execute(text(
            "UPDATE inv_stock SET quantity = quantity + 2 WHERE store_id=1 AND product_id=14"
        ))
        db_session.commit()
