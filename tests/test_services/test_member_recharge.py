"""会员充值测试（阶段 7 CP2 交付物）—— 幂等 + 分开记账（本阶段核心）。

覆盖 spec 验收项 7/8/9/10/11/12：
- 充值幂等（同 request_id 两次 → balance 只加一次，第二次返回首次结果）
- 分开记账（amount=100,gift=20 → 2条流水 RECHARGE+GIFT，4个before/after字段正确）
- 幂等键只出现一次（2条流水只1条带 request_id，否则撞 uk_request_id）
- 赠送比例限制（60% → 9003；50% → 成功）
- 金额校验（都0 → 9002；负数 → schema/service 拒绝）
- 冻结会员不能充值
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
from app.schemas.member import RechargeParams
from app.services import member_service


@pytest.fixture(autouse=True)
def cleanup_recharge_data() -> Generator[None, None, None]:
    """每个测试后恢复会员种子值 + 清流水 + 重置 CZ 取号器。"""
    yield
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM mem_balance_flow"))
        s.execute(text("DELETE FROM mem_point_flow"))
        s.execute(text(
            "UPDATE mem_member SET balance=386.50, gift_balance=30.00, points=1286, "
            "total_consume=8642.30, consume_count=96, level_id=3, status=1 WHERE id=1"
        ))
        s.execute(text(
            "UPDATE mem_member SET balance=50.00, gift_balance=10.00, points=96, "
            "total_consume=486.20, consume_count=12, level_id=1, status=1 WHERE id=4"
        ))
        s.execute(text("DELETE FROM sys_no_seq WHERE seq_key='CZ'"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestRechargeIdempotent:
    """充值幂等（spec 验收 7）。"""

    def test_same_request_id_only_adds_once(self, db_session: Session) -> None:
        """验收 7：同 request_id 连续充值2次 → balance 只加一次，返回首次结果。"""
        req_id = f"test-{uuid.uuid4().hex[:12]}"
        # 会员1 本金 386.50
        r1 = member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("100"), gift_amount=Decimal("0"),
                           pay_method="CASH", request_id=req_id),
            operator_id=1,
        )
        assert r1.balance == Decimal("486.50")  # 386.50 + 100

        # 第二次同 request_id
        r2 = member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("100"), gift_amount=Decimal("0"),
                           pay_method="CASH", request_id=req_id),
            operator_id=1,
        )
        # 返回首次结果，余额不再增加
        assert r2.balance == Decimal("486.50")

        # DB 里只加了 1 次
        balance = db_session.execute(text(
            "SELECT balance FROM mem_member WHERE id=1"
        )).scalar_one()
        assert balance == Decimal("486.50")

        # 只有 1 条该 request_id 的流水
        count = db_session.execute(text(
            "SELECT COUNT(*) FROM mem_balance_flow WHERE request_id=:r"
        ), {"r": req_id}).scalar_one()
        assert count == 1

    def test_no_request_id_no_idempotency(self, db_session: Session) -> None:
        """request_id 为空 → 不做幂等保护（两次都加钱）。"""
        member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("50"), gift_amount=Decimal("0"), pay_method="CASH"),
            operator_id=1,
        )
        member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("50"), gift_amount=Decimal("0"), pay_method="CASH"),
            operator_id=1,
        )
        balance = db_session.execute(text(
            "SELECT balance FROM mem_member WHERE id=1"
        )).scalar_one()
        assert balance == Decimal("486.50")  # 386.50 + 50 + 50


@pytest.mark.integration
class TestSplitAccounting:
    """分开记账（spec 验收 8/9）。"""

    def test_two_flows_recharge_and_gift(self, db_session: Session) -> None:
        """验收 8：amount=100,gift=20 → 2条流水，balance+100、gift_balance+20。"""
        req_id = f"test-{uuid.uuid4().hex[:12]}"
        result = member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("100"), gift_amount=Decimal("20"),
                           pay_method="WECHAT", request_id=req_id),
            operator_id=1,
        )
        # 会员1: balance 386.50+100=486.50, gift 30+20=50
        assert result.balance == Decimal("486.50")
        assert result.gift_balance == Decimal("50.00")

        # 2 条流水
        flows = db_session.execute(text(
            "SELECT flow_type, amount, gift_amount, before_balance, after_balance, "
            "before_gift, after_gift, request_id FROM mem_balance_flow "
            "WHERE member_id=1 ORDER BY id"
        )).fetchall()
        assert len(flows) == 2
        # RECHARGE 流水
        recharge_flow = next(f for f in flows if f[0] == "RECHARGE")
        assert recharge_flow[1] == Decimal("100.00")  # amount
        assert recharge_flow[2] == Decimal("0.00")    # gift_amount=0
        assert recharge_flow[3] == Decimal("386.50")  # before_balance
        assert recharge_flow[4] == Decimal("486.50")  # after_balance
        # GIFT 流水
        gift_flow = next(f for f in flows if f[0] == "GIFT")
        assert gift_flow[1] == Decimal("0.00")        # amount=0
        assert gift_flow[2] == Decimal("20.00")       # gift_amount
        assert gift_flow[5] == Decimal("30.00")       # before_gift
        assert gift_flow[6] == Decimal("50.00")       # after_gift

    def test_only_one_flow_has_request_id(self, db_session: Session) -> None:
        """验收 9：2条流水中只有1条带 request_id（否则撞 uk_request_id）。"""
        req_id = f"test-{uuid.uuid4().hex[:12]}"
        member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("100"), gift_amount=Decimal("20"),
                           pay_method="CASH", request_id=req_id),
            operator_id=1,
        )
        with_rid = db_session.execute(text(
            "SELECT COUNT(*) FROM mem_balance_flow WHERE request_id=:r"
        ), {"r": req_id}).scalar_one()
        assert with_rid == 1  # ⛔ 只 1 条
        total = db_session.execute(text(
            "SELECT COUNT(*) FROM mem_balance_flow WHERE member_id=1"
        )).scalar_one()
        assert total == 2  # 共 2 条

    def test_pure_gift_carries_request_id(self, db_session: Session) -> None:
        """纯赠送（amount=0）→ 只 GIFT 流水，且它带 request_id（幂等仍生效）。"""
        req_id = f"test-{uuid.uuid4().hex[:12]}"
        member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("0"), gift_amount=Decimal("20"),
                           pay_method="CASH", request_id=req_id),
            operator_id=1,
        )
        flows = db_session.execute(text(
            "SELECT flow_type, request_id FROM mem_balance_flow WHERE member_id=1"
        )).fetchall()
        assert len(flows) == 1
        assert flows[0][0] == "GIFT"
        assert flows[0][1] == req_id


@pytest.mark.integration
class TestRechargeValidation:
    """充值校验（spec 验收 10/11/12）。"""

    def test_gift_ratio_exceed_9003(self, db_session: Session) -> None:
        """验收 10：amount=100,gift=60（60%）→ 9003。"""
        with pytest.raises(BusinessError) as exc_info:
            member_service.recharge(
                db_session, 1,
                RechargeParams(amount=Decimal("100"), gift_amount=Decimal("60"),
                               pay_method="CASH"),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.GIFT_RATIO_EXCEED
        db_session.rollback()

    def test_gift_ratio_50_percent_ok(self, db_session: Session) -> None:
        """验收 10：gift=50（50%）→ 成功（阈值是 >0.50 才拒）。"""
        result = member_service.recharge(
            db_session, 1,
            RechargeParams(amount=Decimal("100"), gift_amount=Decimal("50"),
                           pay_method="CASH"),
            operator_id=1,
        )
        assert result.gift_balance == Decimal("80.00")  # 30 + 50

    def test_both_zero_9002(self, db_session: Session) -> None:
        """验收 11：amount=0,gift=0 → 9002。"""
        with pytest.raises(BusinessError) as exc_info:
            member_service.recharge(
                db_session, 1,
                RechargeParams(amount=Decimal("0"), gift_amount=Decimal("0"),
                               pay_method="CASH"),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.AMOUNT_INVALID

    def test_frozen_member_cannot_recharge(self, db_session: Session) -> None:
        """验收 12：冻结会员（status=0）充值 → 拒绝，余额不变。"""
        db_session.execute(text("UPDATE mem_member SET status=0 WHERE id=4"))
        db_session.commit()
        try:
            with pytest.raises(BusinessError) as exc_info:
                member_service.recharge(
                    db_session, 4,
                    RechargeParams(amount=Decimal("100"), gift_amount=Decimal("0"),
                                   pay_method="CASH"),
                    operator_id=1,
                )
            assert exc_info.value.code == ErrorCode.MEMBER_NOT_FOUND
            db_session.rollback()
            # 余额不变（会员4 种子 50.00）
            balance = db_session.execute(text(
                "SELECT balance FROM mem_member WHERE id=4"
            )).scalar_one()
            assert balance == Decimal("50.00")
        finally:
            db_session.execute(text("UPDATE mem_member SET status=1 WHERE id=4"))
            db_session.commit()
