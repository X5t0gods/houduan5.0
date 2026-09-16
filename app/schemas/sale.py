"""销售与收银模块的 Pydantic 模型（阶段 4 交付物）。

⚠️ 与前端 `qianduan/src/types/index.ts` 的 CartItem / Trade / TradeItem / SaleSession
   等接口一字对齐（前端不做字段映射，字段名不符 = 数据不显示）。

⚠️ 金额/数量/时间字段强制用类型别名（决策 4）：
- Money (2 位)：所有金额字段
- Rate (6 位)：cost_price / avg_cost（DECIMAL(12,4)）
- Qty (3 位)：quantity / stock_qty
- DateTimeStr：trade_time / created_at 等
- Flag (int)：is_weight 等 TINYINT
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, DateTimeStr, Flag, Money, Qty, Rate

# ---------- Cart 相关（收银台） ----------


class CartResolveRequest(BaseModel):
    """条码解析请求（POST /sales/cart/resolve）。"""

    code: str = Field(min_length=1, max_length=32, description="条码或商品编码")


class CartResolveResult(BaseSchema):
    """条码解析响应（对应 Mock 的返回结构 + 前端 CartItem 需要的字段）。

    ⚠️ 字段与 `qianduan/src/mock/index.ts:645-658` 完全一致：
    product_id / product_code / product_name / spec / barcode / unit_name /
    origin_price / unit_price / member_price / cost_price / is_weight / stock_qty
    """

    product_id: int = Field(description="商品ID")
    product_code: str = Field(description="商品编码")
    product_name: str = Field(description="商品名称")
    spec: str | None = Field(default=None, description="规格")
    barcode: str = Field(description="匹配到的条码（若通过 product_code 命中则回显 code）")
    unit_name: str | None = Field(default=None, description="单位名称")
    origin_price: Money = Field(description="原价（sale_price，用于展示划线价）")
    unit_price: Money = Field(description="成交单价（默认 = origin_price，收银员可改）")
    member_price: Money | None = Field(default=None, description="会员价")
    # ⚠️ cost_price 是 DECIMAL(12,4)，用 Rate 别名（不能用 Money 会截断到 2 位）
    cost_price: Rate = Field(description="成本价（inv_stock.avg_cost，快照用）")
    is_weight: Flag = Field(description="是否称重 1是 0否")
    stock_qty: Qty = Field(description="当前库存（本门店）")


class CartItem(BaseModel):
    """购物车项（前端 CartItem 类型，POST /sales/cart/calc 与 POST /sales/trades 都用）。

    ⚠️ 与 `qianduan/src/types/index.ts:425-447` 完全对齐。
    ⚠️ 允许未知字段（extra='ignore'），前端可能传 uid 等本地字段。
    """

    model_config = ConfigDict(extra="ignore")

    product_id: int = Field(description="商品ID")
    quantity: Decimal = Field(gt=0, description="数量")
    unit_price: Decimal | None = Field(default=None, ge=0, description="成交单价（不传则用商品档案 sale_price）")
    # 以下字段前端会传但后端不用于计算（只是回显或快照）
    uid: str | None = Field(default=None, description="前端行唯一标识")
    product_code: str | None = None
    product_name: str | None = None
    spec: str | None = None
    unit_name: str | None = None
    barcode: str | None = None
    origin_price: Decimal | None = None
    cost_price: Decimal | None = None
    is_weight: int | None = None
    discount_amount: Decimal | None = Field(default=None, description="行优惠（前端传入，参与结算校验）")
    promo_id: int | None = None
    promo_type: str | None = None
    amount: Decimal | None = None


class CartCalcRequest(BaseModel):
    """促销试算请求（POST /sales/cart/calc）。"""

    model_config = ConfigDict(extra="ignore")

    items: list[CartItem] = Field(min_length=1, description="购物车明细")
    member_id: int | None = Field(default=None, description="会员ID（可选，影响会员专享促销）")


class PromoDetailOut(BaseSchema):
    """促销命中详情（对应 promotion_service.PromoDetail 的对外输出）。"""

    promo_id: int
    promo_name: str
    promo_type: str
    amount: Money
    desc: str = ""


class CartCalcResult(BaseSchema):
    """促销试算响应（与前端 calcPromotion 返回类型对齐）。

    ⚠️ `discount_amount` **不含会员折扣、不含积分抵扣**（spec 5.4）。
       会员折扣由前端 stores/pos.ts:57 单独计算与展示，后端不返回避免重复扣减。
    """

    total_amount: Money = Field(description="商品总金额（优惠前）")
    discount_amount: Money = Field(description="促销优惠合计（不含会员折扣）")
    point_deduct: Money = Field(description="积分抵扣（calc 恒为 0）")
    receivable: Money = Field(description="应收（total - discount，>=0）")
    details: list[PromoDetailOut] = Field(default_factory=list, description="命中促销明细")


# ---------- 结算与交易 ----------


class TradePaymentIn(BaseModel):
    """支付明细（结算请求）。"""

    model_config = ConfigDict(extra="ignore")

    pay_method: str = Field(description="支付方式 CASH/WECHAT/ALIPAY/CARD/BALANCE/POINTS/OTHER")
    amount: Decimal = Field(ge=0, description="支付金额")
    pay_no: str | None = Field(default=None, max_length=64, description="第三方支付流水号")


class TradeCreateRequest(BaseModel):
    """结算请求（POST /sales/trades）—— **幂等**。

    ⚠️ request_id 由前端自动生成（request.ts:48 IDEMPOTENT_PATHS），
       后端靠 sal_trade.uk_request_id 唯一索引兑住并发重复提交。
    ⚠️ 请求体里 **没有** discount_amount / receivable 字段（spec 三 ④），
       后端必须自己重算全部优惠（促销 + 会员折扣），绝不能信前端传的值。
    """

    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(min_length=1, max_length=64, description="幂等键（前端 UUID）")
    # ⚠️ pos_id 前端当前未传（views/pos/index.vue:217 的 submitTrade 漏了此字段），
    # 后端兼容默认值 1（演示环境只有 1 个收银台）。前端修复后可去掉 default。
    pos_id: int = Field(default=1, description="收银台 ID")
    session_id: int | None = Field(default=None, description="班次号 ID（可选，缺省自动取当前 OPEN 班次）")
    member_id: int | None = Field(default=None, description="会员 ID（非会员为空）")
    items: list[CartItem] = Field(min_length=1, description="商品明细")
    payments: list[TradePaymentIn] = Field(min_length=1, description="支付明细（混合支付）")
    use_points: int | None = Field(default=0, ge=0, description="使用积分数（前端未接线，预期 0）")
    round_amount: Decimal | None = Field(default=Decimal("0"), ge=0, description="抹零金额")
    remark: str | None = Field(default=None, max_length=255)


class TradeCreateResult(BaseSchema):
    """结算响应（最小集，spec 6.3）。"""

    id: int
    trade_no: str
    receivable: Money
    received: Money
    change_amount: Money
    trade_time: DateTimeStr
    item_count: int = Field(default=0, description="商品行数（额外方便前端）")


class TradeItemOut(BaseSchema):
    """销售单明细（对应前端 TradeItem）。"""

    id: int
    trade_id: int
    product_id: int
    product_name: str
    spec: str | None = None
    unit_name: str | None = None
    quantity: Qty
    unit_price: Money
    origin_price: Money | None = None
    cost_price: Rate  # ⚠️ DECIMAL(12,4) 用 Rate
    discount_amount: Money
    amount: Money
    batch_id: int | None = None
    promo_id: int | None = None
    promo_type: str | None = None


class TradePaymentOut(BaseSchema):
    """销售支付明细（对应前端 TradePayment）。"""

    pay_method: str
    amount: Money
    pay_no: str | None = None
    pay_status: str | None = None


class TradeOut(BaseSchema):
    """销售单完整信息（对应前端 Trade）。"""

    id: int
    trade_no: str
    request_id: str
    store_id: int
    pos_id: int
    cashier_id: int
    cashier_name: str | None = None
    member_id: int | None = None
    member_name: str | None = None
    member_no: str | None = None
    total_qty: Qty
    total_amount: Money
    discount_amount: Money
    point_deduct: Money
    receivable: Money
    received: Money
    change_amount: Money
    cost_amount: Money
    gross_profit: Money
    round_amount: Money
    status: str
    trade_time: DateTimeStr
    items: list[TradeItemOut] | None = None
    payments: list[TradePaymentOut] | None = None


# ---------- 退货 ----------


class SaleReturnItemIn(BaseModel):
    """退货明细项。"""

    product_id: int
    quantity: Decimal = Field(gt=0)


class SaleReturnCreateRequest(BaseModel):
    """退货请求（POST /sales/trades/return）。"""

    model_config = ConfigDict(extra="ignore")

    source_trade_id: int
    items: list[SaleReturnItemIn] = Field(min_length=1)
    refund_method: str = Field(description="退款方式 CASH/WECHAT/ALIPAY/CARD/BALANCE")
    reason: str = Field(min_length=1, max_length=255)


class SaleReturnOut(BaseSchema):
    """退货单响应（对应前端 SaleReturn）。"""

    id: int
    return_no: str
    source_trade_id: int
    source_trade_no: str | None = None
    store_id: int
    member_id: int | None = None
    total_amount: Money
    cost_amount: Money
    refund_method: str
    refund_status: str
    reason: str | None = None
    operator_name: str | None = None
    created_at: DateTimeStr


class VoidTradeRequest(BaseModel):
    """作废请求（POST /sales/trades/{id}/void）。"""

    reason: str = Field(min_length=1, max_length=255)


class ReceiptResult(BaseSchema):
    """小票打印响应。"""

    print_data: str = Field(description="纯文本小票（等宽字体排版）")
    print_count: int = Field(description="累计打印次数")


# ---------- 挂单 ----------


class HoldCreateRequest(BaseModel):
    """挂单请求。"""

    model_config = ConfigDict(extra="ignore")

    pos_id: int
    member_id: int | None = None
    cart_json: str = Field(description="购物车快照 JSON 字符串")
    item_count: int = Field(ge=0)
    amount: Decimal = Field(ge=0)


class HoldCreateResult(BaseSchema):
    hold_no: str


class HoldOut(BaseSchema):
    """挂单详情。"""

    id: int
    hold_no: str
    store_id: int
    pos_id: int
    member_id: int | None = None
    cart_json: str
    item_count: int
    amount: Money
    status: str
    created_at: DateTimeStr


# ---------- 班次 ----------


class SaleSessionOut(BaseSchema):
    """班次响应（对应前端 SaleSession）。"""

    id: int
    session_no: str
    store_id: int
    pos_id: int
    pos_name: str | None = None
    cashier_id: int
    cashier_name: str | None = None
    open_time: DateTimeStr
    close_time: DateTimeStr | None = None
    init_cash: Money
    sale_amount: Money
    sale_count: int
    return_amount: Money
    void_amount: Money
    cash_amount: Money
    wechat_amount: Money
    alipay_amount: Money
    card_amount: Money
    balance_amount: Money
    actual_cash: Money | None = None
    # ⚠️ spec 三 ⑧：字段名是 cash_diff，不是 Mock 里的 diff_amount
    cash_diff: Money | None = None
    diff_remark: str | None = None
    status: str


class SessionCloseRequest(BaseModel):
    """交班请求。"""

    model_config = ConfigDict(extra="ignore")

    session_id: int
    actual_cash: Decimal = Field(ge=0)
    diff_remark: str | None = Field(default=None, max_length=255)
