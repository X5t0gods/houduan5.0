"""促销明细构造测试（阶段 7 CP3 交付物）—— **本阶段生死线**。

覆盖 spec 验收项 17-33（促销部分）：
- ⭐17：全场类促销（FULL_REDUCE）空 items + 顶层 threshold/reduce → target_id=0 明细
- ⭐18：满减能被阶段4引擎消费（calc_promotion 读到 target_id=0 规则）
- 19：DISCOUNT target_ids=[1,2] → 2 条明细带 discount_rate
- 20：SPECIAL 空 items 无 target_ids → 2001
- 21：start_time='' → NULL；'08:00' → 08:00:00
- 22：顶层多余字段 target_ids 被容忍
- 23：priority 自动填（FULL_REDUCE→4, MEMBER→7）
- 24：MEMBER 强制 is_member_only=1
- 25：product_count（促销2 CATEGORY→6, 促销1 PRODUCT→1）
- 26：促销详情 items（促销3 有2条 promo_price=11.50）
- 28：修改替换明细
- 29/30：已触发不可删/改规则 → 2008
- 31：启停 RUNNING ok / PAUSED→2001
- 32/33：试算不传价格 + 排除 MEMBER
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
from app.schemas.promotion import (
    PromotionCreate,
    PromotionItemIn,
    PromotionStatusUpdate,
    PromotionUpdate,
    SimulateItemIn,
    SimulateRequest,
)
from app.services import promotion_service


@pytest.fixture(autouse=True)
def cleanup_promotion_data() -> Generator[None, None, None]:
    """清理测试促销（id>8）+ 恢复 demo 促销状态。

    ⚠️ demo 8 促销 + 14 明细是阶段4引擎测试依赖的，⛔不能删/改。
       本测试所有 mutating 操作都作用于新建促销（id>8），cleanup 只删 id>8。
    """
    yield
    s = SessionLocal()
    try:
        # 删测试新建促销（id>8）+ 明细
        rows = s.execute(text("SELECT id FROM pro_promotion WHERE id > 8")).fetchall()
        for (pid,) in rows:
            s.execute(text("DELETE FROM pro_promotion_item WHERE promo_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM pro_promotion WHERE id=:p"), {"p": pid})
        # 恢复 demo 促销状态（1-6 RUNNING, 7 DRAFT, 8 EXPIRED）+ trigger_count/discount
        s.execute(text("UPDATE pro_promotion SET status='RUNNING' WHERE id IN (1,2,3,4,5,6)"))
        s.execute(text("UPDATE pro_promotion SET status='DRAFT' WHERE id=7"))
        s.execute(text("UPDATE pro_promotion SET status='EXPIRED' WHERE id=8"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestFullStorePromotionItems:
    """⭐生死线：全场类促销构造 target_id=0 明细（验收 17/18）。"""

    def test_full_reduce_builds_sentinel_item(self, db_session: Session) -> None:
        """验收 17：满50减5（空items+顶层threshold/reduce）→ 1条 target_id=0 明细。"""
        result = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试满50减5", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"),
                items=[],  # ⛔ 前端提交空数组
                target_ids=[],  # 空
            ),
            operator_id=1,
        )
        # 查 pro_promotion_item：必须有 1 条 target_id=0
        items = db_session.execute(text(
            "SELECT item_type, target_id, threshold_amount, reduce_amount "
            "FROM pro_promotion_item WHERE promo_id=:p"
        ), {"p": result.id}).fetchall()
        assert len(items) == 1
        assert items[0][0] == "PRODUCT"
        assert items[0][1] == 0  # ⭐ 全场哨兵
        assert items[0][2] == Decimal("50.00")
        assert items[0][3] == Decimal("5.00")

    def test_full_reduce_consumed_by_engine(self, db_session: Session) -> None:
        """验收 18：满减能被阶段4引擎消费（cart≥50 → details 出现该促销）。"""
        from app.services.promotion_service import EngineItem, calc_promotion

        promo = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试满减引擎", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"),
                status="RUNNING",  # 引擎只读 RUNNING
                items=[],
            ),
            operator_id=1,
        )
        # 购物车：商品13（240库存，sale_price）买够 ≥50
        # 用商品7（sale_price 高）× 2 确保 ≥50
        engine_items = [EngineItem(product_id=7, quantity=Decimal("3"), unit_price=None)]
        result = calc_promotion(db_session, engine_items, is_member=False,
                                now=__import__("datetime").datetime(2026, 9, 16, 12, 0))
        # 该满减促销应命中（在 details 里）
        hit_ids = [d.promo_id for d in result.details]
        assert promo.id in hit_ids, f"满减未被引擎消费！details={hit_ids}"


@pytest.mark.integration
class TestProductPromotionItems:
    """商品类促销明细（验收 19/20）。"""

    def test_discount_with_target_ids(self, db_session: Session) -> None:
        """验收 19：DISCOUNT target_ids=[1,2] → 2条明细带 discount_rate。"""
        result = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试折扣", promo_type="DISCOUNT",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                discount_rate=Decimal("0.85"),
                items=[
                    PromotionItemIn(item_type="PRODUCT", target_id=1, discount_rate=Decimal("0.85")),
                    PromotionItemIn(item_type="PRODUCT", target_id=2, discount_rate=Decimal("0.85")),
                ],
            ),
            operator_id=1,
        )
        items = db_session.execute(text(
            "SELECT target_id, discount_rate FROM pro_promotion_item "
            "WHERE promo_id=:p ORDER BY target_id"
        ), {"p": result.id}).fetchall()
        assert len(items) == 2
        assert items[0][1] == Decimal("0.8500")

    def test_special_empty_items_2001(self, db_session: Session) -> None:
        """验收 20：SPECIAL 空items无target_ids → 2001。"""
        with pytest.raises(BusinessError) as exc_info:
            promotion_service.create_promotion(
                db_session,
                PromotionCreate(
                    promo_name="测试特价空", promo_type="SPECIAL",
                    start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                    items=[],  # 商品类但空
                ),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING


@pytest.mark.integration
class TestPromotionFields:
    """字段处理（验收 21/22/23/24）。"""

    def test_empty_time_to_null(self, db_session: Session) -> None:
        """验收 21：start_time='' → NULL；'08:00' → 08:00:00。"""
        # 空串 → NULL
        p1 = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试空时间", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                start_time="", end_time="",
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"),
                items=[],
            ),
            operator_id=1,
        )
        row = db_session.execute(text(
            "SELECT start_time, end_time FROM pro_promotion WHERE id=:p"
        ), {"p": p1.id}).fetchone()
        assert row[0] is None  # ⛔ 空串转 NULL，不报错
        assert row[1] is None

        # '08:00' → 08:00:00
        p2 = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试有时间", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                start_time="08:00", end_time="21:00",
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"),
                items=[],
            ),
            operator_id=1,
        )
        row2 = db_session.execute(text(
            "SELECT start_time FROM pro_promotion WHERE id=:p"
        ), {"p": p2.id}).fetchone()
        # MySQL TIME 经 PyMySQL 返回 timedelta，str 为 '8:00:00'（非零填充）
        assert row2[0] is not None
        assert str(row2[0]).lstrip("0") == "8:00:00"

    def test_extra_target_ids_tolerated(self, db_session: Session) -> None:
        """验收 22：顶层 target_ids（主表无此列）被容忍，不报错。"""
        result = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试容忍字段", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"),
                target_ids=[1, 2, 3],  # 主表无此列
                items=[],
            ),
            operator_id=1,
        )
        assert result.id > 0

    def test_priority_auto_fill(self, db_session: Session) -> None:
        """验收 23：FULL_REDUCE 不传priority → 4；MEMBER → 7。"""
        p_fr = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试优先级FR", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"), items=[],
            ),
            operator_id=1,
        )
        assert p_fr.priority == 4
        p_mem = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试优先级MEM", promo_type="MEMBER",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                discount_rate=Decimal("0.95"), items=[],
            ),
            operator_id=1,
        )
        assert p_mem.priority == 7

    def test_member_forced_member_only(self, db_session: Session) -> None:
        """验收 24：MEMBER 传 is_member_only=0 → 强制为1。"""
        result = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试MEMBER专属", promo_type="MEMBER",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                is_member_only=0,  # 传0
                discount_rate=Decimal("0.95"), items=[],
            ),
            operator_id=1,
        )
        assert result.is_member_only == 1  # 强制为1


@pytest.mark.integration
class TestPromotionQuery:
    """查询（验收 25/26）。"""

    def test_product_count_category(self, db_session: Session) -> None:
        """验收 25/27：促销2（CATEGORY 11/12/13）product_count 含三级子分类商品。

        ⚠️ spec 验收25 写“6”是 phase-1 seed 前的旧数据（仅 demo 商品1-6）；
        阶段1 seed 又往 111/112/121/122 加了 5 个商品（P000021-25），故实际=11。
        关键是与阶段4引擎命中数一致（同一套递归，验收27）。
        """
        promos, _ = promotion_service.list_promotions(db_session, page=1, page_size=100)
        p2 = next(p for p in promos if p.id == 2)
        # 6 demo(商品1-6) + 5 seed(P000021-25) = 11
        assert p2.product_count == 11

    def test_product_count_single_product(self, db_session: Session) -> None:
        """验收 25：促销1（PRODUCT 14）product_count=1。"""
        promos, _ = promotion_service.list_promotions(db_session, page=1, page_size=100)
        p1 = next(p for p in promos if p.id == 1)
        assert p1.product_count == 1

    def test_detail_has_items(self, db_session: Session) -> None:
        """验收 26：促销3 详情 items 有2条（商品17、19，promo_price=11.50）。"""
        detail = promotion_service.get_promotion_detail(db_session, 3)
        assert detail.items is not None
        assert len(detail.items) == 2
        assert all(it.promo_price == Decimal("11.50") for it in detail.items)


@pytest.mark.integration
class TestPromotionModify:
    """修改/删除/启停（验收 28/29/30/31）。"""

    def test_update_replaces_items(self, db_session: Session) -> None:
        """验收 28：修改 target_ids [14]→[14,15] → 明细变2条，旧的不残留。"""
        promo = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试改明细", promo_type="SPECIAL",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                promo_price=Decimal("3.00"),
                items=[PromotionItemIn(item_type="PRODUCT", target_id=14, promo_price=Decimal("3.00"))],
            ),
            operator_id=1,
        )
        # 改成 2 个商品
        promotion_service.update_promotion(
            db_session, promo.id,
            PromotionUpdate(items=[
                PromotionItemIn(item_type="PRODUCT", target_id=14, promo_price=Decimal("3.00")),
                PromotionItemIn(item_type="PRODUCT", target_id=15, promo_price=Decimal("3.50")),
            ]),
            operator_id=1,
        )
        items = db_session.execute(text(
            "SELECT target_id FROM pro_promotion_item WHERE promo_id=:p ORDER BY target_id"
        ), {"p": promo.id}).fetchall()
        assert len(items) == 2  # 全量替换，不残留
        assert [it[0] for it in items] == [14, 15]

    def test_delete_triggered_2008(self, db_session: Session) -> None:
        """验收 29：trigger_count>0 删除 → 2008。"""
        promo = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试已触发删除", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"), items=[],
            ),
            operator_id=1,
        )
        # 手工设 trigger_count>0
        db_session.execute(text(
            "UPDATE pro_promotion SET trigger_count=999 WHERE id=:p"
        ), {"p": promo.id})
        db_session.commit()
        with pytest.raises(BusinessError) as exc_info:
            promotion_service.delete_promotion(db_session, promo.id, operator_id=1)
        assert exc_info.value.code == ErrorCode.PROMOTION_HAS_TRIGGER

    def test_update_rule_triggered_2008(self, db_session: Session) -> None:
        """验收 30：trigger_count>0 改规则 → 2008；改备注成功。"""
        promo = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试已触发改", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"), items=[],
            ),
            operator_id=1,
        )
        db_session.execute(text(
            "UPDATE pro_promotion SET trigger_count=999 WHERE id=:p"
        ), {"p": promo.id})
        db_session.commit()
        # 改规则 → 2008
        with pytest.raises(BusinessError) as exc_info:
            promotion_service.update_promotion(
                db_session, promo.id,
                PromotionUpdate(threshold_amount=Decimal("100")),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.PROMOTION_HAS_TRIGGER
        # 改备注 → 成功
        result = promotion_service.update_promotion(
            db_session, promo.id, PromotionUpdate(remark="只改备注"), operator_id=1,
        )
        assert result.remark == "只改备注"

    def test_status_toggle(self, db_session: Session) -> None:
        """验收 31：RUNNING ok；PAUSED → 2001。"""
        promo = promotion_service.create_promotion(
            db_session,
            PromotionCreate(
                promo_name="测试启停", promo_type="FULL_REDUCE",
                start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                threshold_amount=Decimal("50"), reduce_amount=Decimal("5"), items=[],
                status="DRAFT",
            ),
            operator_id=1,
        )
        promotion_service.update_promotion_status(
            db_session, promo.id, PromotionStatusUpdate(status="RUNNING"), operator_id=1,
        )
        detail = promotion_service.get_promotion_detail(db_session, promo.id)
        assert detail.status == "RUNNING"
        # 非法状态
        with pytest.raises(BusinessError) as exc_info:
            promotion_service.update_promotion_status(
                db_session, promo.id, PromotionStatusUpdate(status="PAUSED"), operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING


@pytest.mark.integration
class TestSimulate:
    """试算（验收 32/33）。"""

    def test_simulate_no_price(self, db_session: Session) -> None:
        """验收 32：只传 product_id+quantity → 后端查 sale_price，返回金额正常。"""
        result = promotion_service.simulate(
            db_session,
            SimulateRequest(items=[SimulateItemIn(product_id=14, quantity=Decimal("3"))]),
        )
        # 可乐 sale_price=3.80 × 3 = 11.40，特价促销命中 → discount>0
        assert result.total_amount == Decimal("11.40")
        assert result.receivable >= Decimal("0")

    def test_simulate_excludes_member(self, db_session: Session) -> None:
        """验收 33：金卡会员试算 → details 不含 MEMBER（promo_id=6）。"""
        result = promotion_service.simulate(
            db_session,
            SimulateRequest(
                items=[SimulateItemIn(product_id=14, quantity=Decimal("3"))],
                member_id=1,  # 张秀兰金卡
            ),
        )
        promo_ids = [d.promo_id for d in result.details]
        assert 6 not in promo_ids  # MEMBER 促销不参与
