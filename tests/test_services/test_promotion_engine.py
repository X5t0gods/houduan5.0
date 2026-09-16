"""促销引擎测试（阶段 4 CP1 交付物）—— 与 Mock `promotion-engine.ts` 逐条对拍。

覆盖 spec 验收项 2-7：
- 6 种促销类型的计算
- MEMBER 不参与（冲突表 ①）
- CATEGORY 递归匹配父分类（spec 5.2 坑）
- COMBO 组合价共享（禁止累加，spec 5.3 坑）
- 互斥（is_stackable=0 生效后其他 is_stackable=0 跳过）
- 满减基数扣已生效优惠（spec 验收 7）

⚠️ 演示数据里 8 个促销只有 6 个 status=RUNNING（id=7 DRAFT / id=8 EXPIRED 不参与）。
⚠️ 促销 2（每周三生鲜日）需要周三 + 08:00-21:00；促销 3（啤酒组合）需要 17:00-22:00。
   测试用 `now` 参数注入特定时间。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.promotion_service import (
    EngineItem,
    calc_member_discount,
    calc_promotion,
)


def _tz(dt: datetime) -> datetime:
    """把 naive datetime 加上 Asia/Shanghai 时区。"""
    return dt.replace(tzinfo=ZoneInfo(settings.TZ))


# 关键测试时刻
WED_18 = _tz(datetime(2026, 9, 16, 18, 0, 0))   # 周三 18:00 → 促销 2 (8-21) + 促销 3 (17-22) 都在
WED_10 = _tz(datetime(2026, 9, 16, 10, 0, 0))   # 周三 10:00 → 促销 2 生效，促销 3 不在时段
THU_18 = _tz(datetime(2026, 9, 17, 18, 0, 0))   # 周四 18:00 → 促销 2 不在 week_days，促销 3 生效
MON_10 = _tz(datetime(2026, 9, 14, 10, 0, 0))   # 周一 10:00 → 促销 2 与 3 都不在


@pytest.mark.integration
class TestSpecialPromotion:
    """SPECIAL 类型：促销 1 可乐特价（商品 14 原价 3.80，特价 3.20，不可叠加）。"""

    def test_special_applies_to_matched_product(self, db_session: Session) -> None:
        """商品 14 × 3 → 优惠 = (3.80 - 3.20) × 3 = 1.80。"""
        items = [EngineItem(product_id=14, quantity=Decimal("3"), unit_price=Decimal("3.80"))]
        result = calc_promotion(db_session, items, now=MON_10)
        # 找到促销 1 的 detail
        detail = next((d for d in result.details if d.promo_id == 1), None)
        assert detail is not None, f"促销 1 未命中：{result.details}"
        assert detail.amount == Decimal("1.80")
        assert detail.promo_type == "SPECIAL"
        assert "特价" in detail.desc
        assert "3.2" in detail.desc  # 特价 ¥3.20

    def test_special_not_applied_when_price_already_lower(self, db_session: Session) -> None:
        """若购物车传入的 unit_price 已低于 promo_price，不产生负优惠。"""
        items = [EngineItem(product_id=14, quantity=Decimal("1"), unit_price=Decimal("2.00"))]
        result = calc_promotion(db_session, items, now=MON_10)
        # 促销 1 命中但 amount = max(0, (2.00 - 3.20) × 1) ≤ 0 → 跳过
        assert all(d.promo_id != 1 for d in result.details)

    def test_special_uses_sale_price_as_fallback(self, db_session: Session) -> None:
        """购物车不传 unit_price 时，回退到商品档案 sale_price。"""
        items = [EngineItem(product_id=14, quantity=Decimal("2"))]  # 不传 unit_price
        result = calc_promotion(db_session, items, now=MON_10)
        # 商品 14 sale_price=3.80，2 件 → total=7.60，特价 3.20 × 2 = 6.40，优惠 1.20
        assert result.total_amount == Decimal("7.60")
        detail = next((d for d in result.details if d.promo_id == 1), None)
        assert detail is not None
        assert detail.amount == Decimal("1.20")


@pytest.mark.integration
class TestDiscountPromotion:
    """DISCOUNT 类型：促销 2 每周三生鲜日（分类 11/12/13 打 8 折，可叠加）。"""

    def test_category_recursive_match_three_level(self, db_session: Session) -> None:
        """⚠️ spec 验收项 4：商品 1「有机小番茄」cat=111（三级），促销 2 适用分类 11（二级），
        必须通过递归父分类命中。
        """
        items = [EngineItem(product_id=1, quantity=Decimal("2"), unit_price=Decimal("6.80"))]
        result = calc_promotion(db_session, items, now=WED_18)
        # 促销 2 应命中：6.80 × 2 × 0.2 = 2.72
        detail = next((d for d in result.details if d.promo_id == 2), None)
        assert detail is not None, f"促销 2 未通过分类递归命中：{result.details}"
        assert detail.amount == Decimal("2.72")
        assert "8.0 折" in detail.desc

    def test_weekday_filter(self, db_session: Session) -> None:
        """周四不命中促销 2（week_days='3' 只周三）。"""
        items = [EngineItem(product_id=1, quantity=Decimal("2"), unit_price=Decimal("6.80"))]
        result = calc_promotion(db_session, items, now=THU_18)
        # 促销 2 不命中
        assert all(d.promo_id != 2 for d in result.details)

    def test_time_period_filter(self, db_session: Session) -> None:
        """周三但不在 08:00-21:00 时段（如凌晨 3 点）→ 促销 2 不命中。"""
        wed_3am = _tz(datetime(2026, 9, 16, 3, 0, 0))
        items = [EngineItem(product_id=1, quantity=Decimal("2"), unit_price=Decimal("6.80"))]
        result = calc_promotion(db_session, items, now=wed_3am)
        assert all(d.promo_id != 2 for d in result.details)


@pytest.mark.integration
class TestComboPromotion:
    """COMBO 类型：促销 3 啤酒+花生米组合（商品 17+19，组合价 11.50，17:00-22:00，不可叠加）。"""

    def test_combo_price_not_summed(self, db_session: Session) -> None:
        """⚠️ spec 验收项 5：两条明细 promo_price 都是 11.50，**不能相加**成 23.00。

        商品 17 (青岛啤酒 5.50) + 商品 19 (黄飞红花生米 7.50) = 13.00
        组合优惠 = 13.00 - 11.50 = **1.50**（不是 13.00 - 23.00 = -10 → 0）
        """
        items = [
            EngineItem(product_id=17, quantity=Decimal("1"), unit_price=Decimal("5.50")),
            EngineItem(product_id=19, quantity=Decimal("1"), unit_price=Decimal("7.50")),
        ]
        result = calc_promotion(db_session, items, now=WED_18)
        detail = next((d for d in result.details if d.promo_id == 3), None)
        assert detail is not None, f"促销 3 未命中：{result.details}"
        assert detail.amount == Decimal("1.50"), \
            f"组合优惠应 1.50（=13.00-11.50），实际 {detail.amount}。若为 0 说明累加了 promo_price"
        assert "组合价" in detail.desc
        assert "11.5" in detail.desc

    def test_combo_requires_all_items_in_cart(self, db_session: Session) -> None:
        """组合内商品**必须全部**在购物车中，否则跳过。"""
        items = [EngineItem(product_id=17, quantity=Decimal("1"), unit_price=Decimal("5.50"))]
        result = calc_promotion(db_session, items, now=WED_18)
        # 只有商品 17，缺 19 → 促销 3 不生效
        assert all(d.promo_id != 3 for d in result.details)

    def test_combo_time_period(self, db_session: Session) -> None:
        """促销 3 时段是 17:00-22:00，非此时段不生效。"""
        items = [
            EngineItem(product_id=17, quantity=Decimal("1"), unit_price=Decimal("5.50")),
            EngineItem(product_id=19, quantity=Decimal("1"), unit_price=Decimal("7.50")),
        ]
        result = calc_promotion(db_session, items, now=WED_10)  # 10:00，不在 17-22
        assert all(d.promo_id != 3 for d in result.details)


@pytest.mark.integration
class TestFullReducePromotion:
    """FULL_REDUCE 类型：促销 4 满 50 减 5（target_id=0 哨兵，可叠加）。"""

    def test_full_reduce_applies_when_threshold_met(self, db_session: Session) -> None:
        """购物车小计 ≥ 50 → 减 5。"""
        # 商品 8 (金龙鱼调和油 45.90) × 2 = 91.80，超过 50
        items = [EngineItem(product_id=8, quantity=Decimal("2"), unit_price=Decimal("45.90"))]
        result = calc_promotion(db_session, items, now=MON_10)
        detail = next((d for d in result.details if d.promo_id == 4), None)
        assert detail is not None
        assert detail.amount == Decimal("5.00")
        assert "满" in detail.desc and "减" in detail.desc

    def test_full_reduce_not_applied_below_threshold(self, db_session: Session) -> None:
        """购物车小计 < 50 → 不生效。"""
        items = [EngineItem(product_id=8, quantity=Decimal("1"), unit_price=Decimal("45.90"))]
        result = calc_promotion(db_session, items, now=MON_10)
        assert all(d.promo_id != 4 for d in result.details)

    def test_full_reduce_base_deducts_accumulated(self, db_session: Session) -> None:
        """⚠️ spec 验收项 7：满减基数 = totalAmount - accumulated（已生效优惠）。

        构造：商品 14 (可口可乐) × 20 原价 3.80 = 76.00
        - 促销 1 特价 3.20 生效 → 优惠 (3.80-3.20)×20 = 12.00
        - accumulated = 12.00
        - base = 76.00 - 12.00 = 64.00 → 仍 >= 50 → 促销 4 生效减 5
        若基数不扣 accumulated，base = 76 → 也满足；此测试主要验证 base 计算逻辑存在。
        """
        items = [EngineItem(product_id=14, quantity=Decimal("20"), unit_price=Decimal("3.80"))]
        result = calc_promotion(db_session, items, now=MON_10)
        assert result.total_amount == Decimal("76.00")
        # 促销 1（不可叠加）生效后，促销 4（可叠加）仍应生效
        promo_ids = {d.promo_id for d in result.details}
        assert 1 in promo_ids
        assert 4 in promo_ids

    def test_full_reduce_base_below_threshold_after_special(self, db_session: Session) -> None:
        """⚠️ 关键：先命中特价后 base 变小，可能不再满足满减门槛。

        构造：商品 14 × 15 = 57.00
        - 促销 1 特价生效 → 优惠 (3.80-3.20)×15 = 9.00
        - accumulated = 9.00
        - base = 57.00 - 9.00 = 48.00 < 50 → **促销 4 不生效**
        若基数不扣 accumulated，base = 57 → 会误减 5 元（店铺多让利）。
        """
        items = [EngineItem(product_id=14, quantity=Decimal("15"), unit_price=Decimal("3.80"))]
        result = calc_promotion(db_session, items, now=MON_10)
        promo_ids = {d.promo_id for d in result.details}
        assert 1 in promo_ids  # 特价生效
        assert 4 not in promo_ids, \
            f"促销 4 应因 base=48<50 被跳过，实际生效的促销：{promo_ids}"


@pytest.mark.integration
class TestCouponPromotion:
    """COUPON 类型：促销 5 新客 5 元券（满 30 减 5，会员专享，不可叠加）。"""

    def test_coupon_requires_member(self, db_session: Session) -> None:
        """非会员不生效（is_member_only=1）。"""
        items = [EngineItem(product_id=8, quantity=Decimal("1"), unit_price=Decimal("45.90"))]
        result = calc_promotion(db_session, items, is_member=False, now=MON_10)
        assert all(d.promo_id != 5 for d in result.details)

    def test_coupon_applies_for_member(self, db_session: Session) -> None:
        """会员 + 满 30 → 减 5。但促销 5 是 is_stackable=0，与促销 1 互斥。"""
        # 用商品 8 (45.90) 避免命中促销 1
        items = [EngineItem(product_id=8, quantity=Decimal("1"), unit_price=Decimal("45.90"))]
        result = calc_promotion(db_session, items, is_member=True, now=MON_10)
        # 促销 4 (可叠加，满 50 减 5) 不满足门槛 (45.90<50)
        # 促销 5 (不可叠加，会员满 30 减 5) 满足 → 生效
        promo_ids = {d.promo_id for d in result.details}
        assert 5 in promo_ids


@pytest.mark.integration
class TestMemberPromotionExcluded:
    """⚠️ spec 验收项 3：MEMBER 促销绝不参与计算（冲突表 ①）。"""

    def test_member_promo_never_in_details(self, db_session: Session) -> None:
        """金卡会员购物车调 calc_promotion，details 里不含 promo_id=6（MEMBER 类型）。"""
        # 促销 6 是 MEMBER 类型，金卡 95 折
        items = [EngineItem(product_id=8, quantity=Decimal("2"), unit_price=Decimal("45.90"))]
        result = calc_promotion(db_session, items, is_member=True, now=MON_10)
        # 断言 details 里绝无 promo_type='MEMBER' 或 promo_id=6
        assert all(d.promo_id != 6 for d in result.details), \
            f"MEMBER 促销不应参与计算，实际 details: {result.details}"
        assert all(d.promo_type != "MEMBER" for d in result.details)

    def test_calc_member_discount_formula(self) -> None:
        """会员折扣公式（结算专用，calc 接口不返回）：
        `base × (1 - rate)`，rate>=1 时返 0。

        对应 stores/pos.ts:57-62 的公式，spec 三 ②。
        """
        # 金卡 95 折：rate=0.95，base=100 → 折扣 = 100 × 0.05 = 5.00
        assert calc_member_discount(
            total_amount=Decimal("100.00"),
            item_discount=Decimal("0.00"),
            promo_discount=Decimal("0.00"),
            member_discount_rate=Decimal("0.9500"),
        ) == Decimal("5.00")

        # 普通会员 rate=1.0 → 折扣 0
        assert calc_member_discount(
            total_amount=Decimal("100.00"),
            item_discount=Decimal("0.00"),
            promo_discount=Decimal("0.00"),
            member_discount_rate=Decimal("1.0000"),
        ) == Decimal("0.00")

        # 银卡 98 折，base = 100 - 10 - 5 = 85 → 折扣 = 85 × 0.02 = 1.70
        assert calc_member_discount(
            total_amount=Decimal("100.00"),
            item_discount=Decimal("10.00"),
            promo_discount=Decimal("5.00"),
            member_discount_rate=Decimal("0.9800"),
        ) == Decimal("1.70")


@pytest.mark.integration
class TestStackableExclusion:
    """⚠️ spec 验收项 6：互斥逻辑（is_stackable=0 生效后其他不可叠加促销跳过）。"""

    def test_two_non_stackable_only_first_wins(self, db_session: Session) -> None:
        """促销 1 (SPECIAL, stackable=0, priority=1) 与 促销 3 (COMBO, stackable=0, priority=3)
        同时命中时**只有促销 1 生效**（priority 小者优先）。
        """
        # 商品 14 (可乐，促销 1 目标) + 商品 17 (啤酒) + 商品 19 (花生米) → 促销 3 目标
        items = [
            EngineItem(product_id=14, quantity=Decimal("1"), unit_price=Decimal("3.80")),
            EngineItem(product_id=17, quantity=Decimal("1"), unit_price=Decimal("5.50")),
            EngineItem(product_id=19, quantity=Decimal("1"), unit_price=Decimal("7.50")),
        ]
        result = calc_promotion(db_session, items, now=WED_18)
        promo_ids = {d.promo_id for d in result.details}
        # 促销 1 应生效（priority=1，先命中）
        assert 1 in promo_ids
        # 促销 3 应被互斥跳过
        assert 3 not in promo_ids, f"促销 3 应被互斥跳过，实际生效：{promo_ids}"

    def test_stackable_still_applies_after_exclusive(self, db_session: Session) -> None:
        """促销 1 (不可叠加) 生效后，促销 4 (可叠加) 仍能生效。"""
        # 商品 14 × 20 → 促销 1 特价生效（12 元优惠），促销 4 满 50 减 5 也生效
        items = [EngineItem(product_id=14, quantity=Decimal("20"), unit_price=Decimal("3.80"))]
        result = calc_promotion(db_session, items, now=MON_10)
        promo_ids = {d.promo_id for d in result.details}
        assert 1 in promo_ids
        assert 4 in promo_ids  # 可叠加，仍生效


@pytest.mark.integration
class TestInactivePromotions:
    """status != RUNNING 的促销不参与（id=7 DRAFT / id=8 EXPIRED）。"""

    def test_draft_and_expired_excluded(self, db_session: Session) -> None:
        """促销 7 (DRAFT) 与促销 8 (EXPIRED) 绝不出现在 details 里。"""
        # 构造能命中它们的购物车
        items = [
            EngineItem(product_id=1, quantity=Decimal("10"), unit_price=Decimal("6.80")),
            EngineItem(product_id=13, quantity=Decimal("10"), unit_price=Decimal("2.00")),
        ]
        result = calc_promotion(db_session, items, now=WED_18)
        promo_ids = {d.promo_id for d in result.details}
        assert 7 not in promo_ids, "DRAFT 促销不应参与"
        assert 8 not in promo_ids, "EXPIRED 促销不应参与"


@pytest.mark.integration
class TestEdgeCases:
    """边界情况。"""

    def test_empty_cart(self, db_session: Session) -> None:
        """空购物车 → 全 0，不抛异常。"""
        result = calc_promotion(db_session, [], now=WED_18)
        assert result.total_amount == Decimal("0.00")
        assert result.discount_amount == Decimal("0.00")
        assert result.receivable == Decimal("0.00")
        assert result.details == []

    def test_point_deduct_always_zero(self, db_session: Session) -> None:
        """⚠️ spec 5.4：calc 接口 point_deduct 恒为 0（积分抵扣在结算时处理）。"""
        items = [EngineItem(product_id=8, quantity=Decimal("1"), unit_price=Decimal("45.90"))]
        result = calc_promotion(db_session, items, now=MON_10)
        assert result.point_deduct == Decimal("0.00")

    def test_receivable_never_negative(self, db_session: Session) -> None:
        """优惠超过总额时 receivable = 0（不能是负数）。"""
        # 用极小价格触发（虽然实际不会发生，但公式必须兜底）
        items = [EngineItem(product_id=14, quantity=Decimal("1"), unit_price=Decimal("0.01"))]
        result = calc_promotion(db_session, items, now=MON_10)
        assert result.receivable >= Decimal("0.00")

    def test_details_amount_are_decimal(self, db_session: Session) -> None:
        """details[].amount 必须是 Decimal（不是 float），量化到分。"""
        items = [EngineItem(product_id=14, quantity=Decimal("3"), unit_price=Decimal("3.80"))]
        result = calc_promotion(db_session, items, now=MON_10)
        for d in result.details:
            assert isinstance(d.amount, Decimal), f"{d.promo_id} amount 类型 {type(d.amount)}"
            # 检查量化到 2 位小数
            assert d.amount == d.amount.quantize(Decimal("0.01"))
