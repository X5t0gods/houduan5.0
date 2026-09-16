"""促销模块的 Pydantic 模型（阶段 7 交付物）。

⚠️ 与前端 `qianduan/src/types/index.ts:373-420` 的 Promotion / PromotionItem / Coupon 对齐。

⚠️ 头号问题（spec 三）：全场类促销（FULL_REDUCE/FULL_GIFT/COUPON/MEMBER）前端提交
   **空 items[]**，规则值只在**顶层字段**。PromotionCreate 必须同时接受：
   - 顶层主表字段：promo_name/promo_type/priority/start_date/end_date/week_days/
     start_time/end_time/is_member_only/is_stackable/status/remark
   - 顶层"规则快捷字段"（主表没有）：target_ids/promo_price/discount_rate/
     threshold_amount/reduce_amount/gift_product_id/gift_qty
   - 明细 items[]
   → service 层据此构造 target_id=0 全场哨兵明细（否则阶段4满减失效）。

⚠️ start_time/end_time 前端是 'HH:mm' 或空串 ''，用 str 接收，service 转 NULL/time。
⚠️ discount_rate 是 Rate（DECIMAL5,4）；金额是 Money；日期 DateStr。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, DateStr, Flag, Money, Qty, Rate

# ---------- 促销 ----------


class PromotionItem(BaseSchema):
    """促销明细（对应前端 PromotionItem）。"""

    id: int | None = None
    promo_id: int | None = None
    item_type: str = Field(description="PRODUCT/CATEGORY")
    target_id: int = Field(description="商品ID/分类ID/0(全场哨兵)")
    target_name: str | None = None
    promo_price: Money | None = None
    discount_rate: Rate | None = None  # DECIMAL(5,4)
    threshold_amount: Money | None = None
    reduce_amount: Money | None = None
    gift_product_id: int | None = None
    gift_qty: Qty | None = None
    combo_qty: Qty | None = None


class Promotion(BaseSchema):
    """促销（对应前端 Promotion + product_count 合成字段）。"""

    id: int
    promo_name: str
    promo_type: str
    priority: int
    start_date: DateStr
    end_date: DateStr
    week_days: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    is_member_only: Flag
    is_stackable: Flag
    trigger_count: int
    discount_total: Money
    status: str
    created_by_name: str | None = None
    remark: str | None = None
    # 合成字段（spec 冲突表 ④）：实际覆盖商品数
    product_count: int | None = None
    items: list[PromotionItem] | None = None


class PromotionItemIn(BaseModel):
    """促销明细项（创建/修改请求的 items[]）。"""

    model_config = ConfigDict(extra="ignore")

    item_type: str = Field(default="PRODUCT", description="PRODUCT/CATEGORY")
    target_id: int
    promo_price: Decimal | None = None
    discount_rate: Decimal | None = None
    threshold_amount: Decimal | None = None
    reduce_amount: Decimal | None = None
    gift_product_id: int | None = None
    gift_qty: Decimal | None = None
    combo_qty: Decimal | None = None


class PromotionCreate(BaseModel):
    """创建促销请求（POST /promotions）。

    ⚠️ 同时接受顶层规则快捷字段 + items[]（spec 三）。
    ⚠️ start_time/end_time 是 'HH:mm' 或 ''（空串→NULL），用 str 接收。
    ⚠️ target_ids 是前端商品选择器字段，后端不落库（容忍）。
    """

    model_config = ConfigDict(extra="ignore")

    # 主表字段
    promo_name: str = Field(min_length=1, max_length=64)
    promo_type: str = Field(description="SPECIAL/DISCOUNT/FULL_REDUCE/FULL_GIFT/COMBO/COUPON/MEMBER")
    priority: int | None = Field(default=None, description="缺省按类型自动填(1-7)")
    start_date: date
    end_date: date
    week_days: str | None = Field(default=None, max_length=16)
    start_time: str | None = Field(default=None, description="HH:mm 或 空串")
    end_time: str | None = Field(default=None, description="HH:mm 或 空串")
    is_member_only: Flag = 0
    is_stackable: Flag = 1
    status: str | None = Field(default=None, description="缺省 DRAFT")
    remark: str | None = Field(default=None, max_length=255)

    # 顶层"规则快捷字段"（主表没有，用于全场类促销构造 target_id=0 明细）
    target_ids: list[int] | None = Field(default=None, description="前端商品选择器（不落库）")
    promo_price: Decimal | None = None
    discount_rate: Decimal | None = None
    threshold_amount: Decimal | None = None
    reduce_amount: Decimal | None = None
    gift_product_id: int | None = None
    gift_qty: Decimal | None = None

    # 明细
    items: list[PromotionItemIn] | None = Field(default=None)


class PromotionUpdate(BaseModel):
    """修改促销请求（PUT /promotions/{id}，明细全量替换）。

    ⚠️ trigger_count>0 时只允许改名称/有效期/备注，改规则→2008（spec 5.18）。
    """

    model_config = ConfigDict(extra="ignore")

    promo_name: str | None = Field(default=None, max_length=64)
    promo_type: str | None = None
    priority: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    week_days: str | None = Field(default=None, max_length=16)
    start_time: str | None = None
    end_time: str | None = None
    is_member_only: Flag | None = None
    is_stackable: Flag | None = None
    remark: str | None = Field(default=None, max_length=255)
    # 规则快捷字段 + items（改规则时用）
    target_ids: list[int] | None = None
    promo_price: Decimal | None = None
    discount_rate: Decimal | None = None
    threshold_amount: Decimal | None = None
    reduce_amount: Decimal | None = None
    gift_product_id: int | None = None
    gift_qty: Decimal | None = None
    items: list[PromotionItemIn] | None = None


class PromotionStatusUpdate(BaseModel):
    """启停促销（PUT /promotions/{id}/status）。"""

    model_config = ConfigDict(extra="ignore")

    status: str = Field(description="RUNNING/STOPPED")


# ---------- 试算 / 匹配 / 效果 ----------


class SimulateItemIn(BaseModel):
    """试算购物车项（⛔不传价格，后端取 sale_price）。"""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    quantity: Decimal = Field(gt=0)


class SimulateRequest(BaseModel):
    """促销试算请求（POST /promotions/simulate）。"""

    model_config = ConfigDict(extra="ignore")

    items: list[SimulateItemIn] = Field(min_length=1)
    member_id: int | None = None


class SimulateDetail(BaseSchema):
    promo_id: int
    promo_name: str
    promo_type: str
    amount: Money
    desc: str = ""


class SimulateResult(BaseSchema):
    """试算结果（复用阶段4引擎，排除 MEMBER）。"""

    total_amount: Money
    discount_amount: Money
    receivable: Money
    details: list[SimulateDetail] = Field(default_factory=list)


class MatchResultItem(BaseSchema):
    """促销匹配结果项（POST /promotions/match，只返命中列表不含汇总）。"""

    promo_id: int
    promo_name: str
    promo_type: str
    amount: Money


class ComparedPoint(BaseSchema):
    """效果对比点（估算口径，spec 5.22）。"""

    date: str
    with_promo: Money
    without_promo: Money


class PromotionEffect(BaseSchema):
    """促销效果分析（GET /promotions/{id}/effect）。"""

    promo_id: int
    trigger_count: int
    discount_total: Money
    sale_amount: Money
    gross_profit: Money
    compared: list[ComparedPoint] = Field(default_factory=list)


class RuleFromPromoRequest(BaseModel):
    """由关联规则生成促销（POST /promotions/from-rule，依赖阶段9 bi_rule）。"""

    model_config = ConfigDict(extra="ignore")

    rule_id: int
    promo_name: str = Field(min_length=1, max_length=64)
    promo_type: str = Field(default="COMBO")
    promo_price: Decimal | None = None
    start_date: date
    end_date: date


# ---------- 优惠券 ----------


class Coupon(BaseSchema):
    """优惠券（对应前端 Coupon + remaining_qty）。"""

    id: int
    coupon_name: str
    coupon_type: str
    face_value: Money
    discount_rate: Rate | None = None
    threshold: Money
    total_qty: int
    issued_qty: int
    used_qty: int
    remaining_qty: int | None = Field(default=None, description="total_qty=0(不限)→null")
    valid_from: DateStr
    valid_to: DateStr
    promo_id: int | None = None
    status: Flag


class CouponCreate(BaseModel):
    """创建优惠券请求（POST /coupons）。"""

    model_config = ConfigDict(extra="ignore")

    coupon_name: str = Field(min_length=1, max_length=64)
    coupon_type: str = Field(description="CASH/DISCOUNT/GIFT")
    face_value: Decimal | None = Field(default=None, ge=0)
    discount_rate: Decimal | None = None
    threshold: Decimal = Field(default=Decimal("0"), ge=0)
    total_qty: int = Field(default=0, ge=0)
    valid_from: date
    valid_to: date
    promo_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)
