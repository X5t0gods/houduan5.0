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

from app.schemas.base import BaseSchema, Flag, Money, Qty, Rate

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
