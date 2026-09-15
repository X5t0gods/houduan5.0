"""促销域（pro_）—— 4 张表。

对应 db/01_schema.sql 第六节后半。

⚠️ 促销引擎行为基准（阶段 4）：qianduan/src/mock/promotion-engine.ts（160 行）。
   计算顺序按 priority 升序：SPECIAL(1) → DISCOUNT(2) → COMBO(3) →
   FULL_REDUCE(4) → FULL_GIFT(5) → COUPON(6) → MEMBER(7)；
   MEMBER 类型**不参与后端计算**（会员折扣由前端 store 汇总，避免重复扣减）。
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Time,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProPromotion(Base):
    """促销规则（pro_promotion）。

    ⚠️ `source_rule_id` 无 FK（脚本注释未列，脚本末尾也无对应 ALTER），
       阶段 9 BI 采纳规则时手动写入 rule_id，不加约束避免影响 BI 表变更。
    """

    __tablename__ = "pro_promotion"
    __table_args__ = {"comment": "促销规则"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="促销ID")
    promo_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="促销名称，如 每周三生鲜日")
    promo_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="类型 SPECIAL特价/DISCOUNT折扣/FULL_REDUCE满减/FULL_GIFT满赠/COMBO组合套餐/COUPON优惠券/MEMBER会员专享")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"), comment="优先级，数值小者优先")
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="生效开始日期")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="生效结束日期")
    week_days: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="生效星期，如 5,6 表示周五周六，空为每天")
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True, comment="每日生效开始时间")
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True, comment="每日生效结束时间")
    is_member_only: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否仅会员 1是 0否")
    # 关键：is_stackable=0 时该促销互斥（命中一个后其余跳过），阶段 4 促销引擎实现
    is_stackable: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="是否可与其他促销叠加 1是 0否")
    source_rule_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="来源关联规则ID（由智能分析生成时，无 FK）")
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="累计触发单数")
    discount_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="累计优惠支出")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'DRAFT'"), comment="状态 DRAFT未开始/RUNNING进行中/STOPPED已停用/EXPIRED已结束")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="创建人ID")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class ProPromotionItem(Base):
    """促销规则明细（pro_promotion_item）。

    ⚠️ 一表多用：根据 promo_type 使用不同字段组合
    - SPECIAL:      promo_price
    - DISCOUNT:     discount_rate（DECIMAL(5,4) 精度 4 位，不是金额）
    - FULL_REDUCE:  threshold_amount + reduce_amount
    - FULL_GIFT:    threshold_amount + gift_product_id + gift_qty
    - COMBO:        combo_qty
    """

    __tablename__ = "pro_promotion_item"
    __table_args__ = {"comment": "促销规则明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    promo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pro_promotion.id", ondelete="RESTRICT"), nullable=False, comment="促销ID")
    item_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'PRODUCT'"), comment="适用类型 PRODUCT商品/CATEGORY分类")
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="商品ID或分类ID")
    promo_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="特价（特价促销）")
    # DECIMAL(5,4)：折扣率精度 4 位（0.9800 = 98 折），Rate 别名序列化
    discount_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True, comment="折扣率（折扣促销）")
    threshold_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="门槛金额（满减/满赠）")
    reduce_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="减免金额（满减）")
    # ⚠️ gift_product_id 在脚本末尾的 fk_promoi_product 是外键（指向 prd_product.id）
    gift_product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=True, comment="赠品商品ID（满赠）")
    gift_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True, comment="赠品数量")
    combo_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True, comment="套餐内数量（组合套餐）")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class ProCoupon(Base):
    """优惠券（pro_coupon）。

    ⚠️ `promo_id` 有 FK（fk_coupon_promo → pro_promotion.id），但可空：
       优惠券可单独存在，也可关联到某个促销活动。
    """

    __tablename__ = "pro_coupon"
    __table_args__ = {"comment": "优惠券"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="优惠券ID")
    coupon_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="优惠券名称，如 新客5元券")
    coupon_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="类型 CASH现金券/DISCOUNT折扣券/GIFT赠品券")
    face_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="面值（现金券）")
    discount_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True, comment="折扣率（折扣券）")
    threshold: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="使用门槛")
    total_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="发行总量，0为不限")
    issued_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="已发放数量")
    used_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="已核销数量")
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, comment="有效期开始")
    valid_to: Mapped[date] = mapped_column(Date, nullable=False, comment="有效期结束")
    promo_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pro_promotion.id", ondelete="RESTRICT"), nullable=True, comment="关联促销ID")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class ProCouponRecord(Base):
    """优惠券领用核销记录（pro_coupon_record）。

    ⚠️ `used_trade_id` 无 FK（脚本未声明），阶段 4 收银结算时手动写入 trade_id。
    """

    __tablename__ = "pro_coupon_record"
    __table_args__ = {"comment": "优惠券领用核销记录"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    coupon_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pro_coupon.id", ondelete="RESTRICT"), nullable=False, comment="优惠券ID")
    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mem_member.id", ondelete="RESTRICT"), nullable=False, comment="会员ID")
    use_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'UNUSED'"), comment="状态 UNUSED未使用/USED已核销/EXPIRED已过期")
    used_trade_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="核销的销售单ID（无 FK）")
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="发放时间")
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="核销时间")
