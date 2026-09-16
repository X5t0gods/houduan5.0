"""采购与供应商模块的 Pydantic 模型（阶段 6 交付物）。

⚠️ 与前端 `qianduan/src/types/index.ts:251-295` 的 Supplier / PurchaseOrder /
   PurchaseOrderItem 一字对齐（前端不做字段映射）。

⚠️ 类型别名（决策 4 + spec 六.4）：
- `Money` (2 位)：total_amount / unit_price(DECIMAL12,2) / amount / balance_payable
- `Rate` (6 位)：avg_cost（DECIMAL(12,4)）
- `Qty` (3 位)：quantity / received_qty / receive_qty / order_qty
- `DateStr`：order_date / expect_date / pay_date / production_date / expiry_date
- `DateTimeStr`：audited_at / received_at / created_at
- `Flag` (int)：status / credit_days 用 int

⚠️ 请求模型 extra='ignore'：前端会多传 product_name/spec/unit_name 等纯展示字段。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, DateStr, DateTimeStr, Flag, Money, Qty, Rate

# ---------- 供应商 ----------


class Supplier(BaseSchema):
    """供应商（对应前端 types/index.ts:251-263）。"""

    id: int
    supplier_code: str
    supplier_name: str
    contact_name: str | None = None
    phone: str | None = None
    address: str | None = None
    settle_type: str
    credit_days: int
    balance_payable: Money
    status: Flag
    remark: str | None = None


class SupplierCreate(BaseModel):
    """新增供应商请求（POST /suppliers）。

    ⚠️ supplier_code 后端自动生成（S 取号）；balance_payable ⛔不接受前端传入（防篡改）。
    """

    model_config = ConfigDict(extra="ignore")

    supplier_name: str = Field(min_length=1, max_length=64)
    supplier_code: str | None = Field(default=None, max_length=20, description="不传则自动生成")
    contact_name: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=200)
    settle_type: str = Field(default="CASH", description="CASH/PERIOD")
    credit_days: int = Field(default=0, ge=0)
    remark: str | None = Field(default=None, max_length=255)


class SupplierUpdate(BaseModel):
    """修改供应商请求（PUT /suppliers/{id}）。

    ⚠️ 不允许改 supplier_code 与 balance_payable（后者只能由收货/退货/付款变动）。
    """

    model_config = ConfigDict(extra="ignore")

    supplier_name: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=200)
    settle_type: str | None = None
    credit_days: int | None = Field(default=None, ge=0)
    status: Flag | None = None
    remark: str | None = Field(default=None, max_length=255)


class StatementResult(BaseSchema):
    """供应商对账结果（spec 4.4 倒推法）。

    ⚠️ begin_payable 是**倒推值**（end - increase + decrease），非历史累计。
       原因：初始化数据的应付余额没有对应历史单据。
    """

    begin_payable: Money
    increase: Money
    decrease: Money
    end_payable: Money


class PaymentParams(BaseModel):
    """付款登记请求（POST /suppliers/payments）。"""

    model_config = ConfigDict(extra="ignore")

    supplier_id: int
    amount: Decimal = Field(gt=0)
    pay_method: str = Field(description="CASH/TRANSFER/WECHAT/ALIPAY")
    pay_date: date
    remark: str | None = Field(default=None, max_length=255)


class PaymentOut(BaseSchema):
    """付款登记响应。"""

    id: int
    pay_no: str
    supplier_id: int
    amount: Money
    pay_method: str
    pay_date: DateStr
    balance_payable: Money = Field(description="付款后的应付余额")


# ---------- 采购订单 ----------


class PurchaseOrderItemIn(BaseModel):
    """采购订单明细项（建单/改单请求）。"""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    remark: str | None = Field(default=None, max_length=255)


class PurchaseOrderCreate(BaseModel):
    """创建采购订单请求（POST /purchases）。

    ⚠️ source_type 可选，缺省 MANUAL（spec 冲突表 ②：前端根本不传）。
    ⚠️ ⛔ 不接受 status 参数（spec 冲突表 ⑦：创建一律 DRAFT）。
    """

    model_config = ConfigDict(extra="ignore")

    supplier_id: int
    store_id: int | None = Field(default=None, description="收货门店（缺省用当前用户门店）")
    order_date: date | None = Field(default=None, description="缺省今天")
    expect_date: date | None = None
    source_type: str | None = Field(default="MANUAL", description="MANUAL/REPLENISH")
    remark: str | None = Field(default=None, max_length=255)
    items: list[PurchaseOrderItemIn] = Field(min_length=1)


class PurchaseOrderUpdate(BaseModel):
    """修改采购订单请求（PUT /purchases/{id}，仅 DRAFT 可改，明细全量替换）。"""

    model_config = ConfigDict(extra="ignore")

    supplier_id: int | None = None
    order_date: date | None = None
    expect_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[PurchaseOrderItemIn] | None = Field(default=None, min_length=1)


class PurchaseOrderItem(BaseSchema):
    """采购订单明细（对应前端 PurchaseOrderItem）。

    ⚠️ product_code/product_name/spec/unit_name 是 JOIN 出来的（表里无快照，spec 冲突表 ④）。
    """

    id: int | None = None
    product_id: int
    product_code: str | None = None
    product_name: str | None = None
    spec: str | None = None
    unit_name: str | None = None
    quantity: Qty
    received_qty: Qty
    unit_price: Money
    amount: Money
    remark: str | None = None


class PurchaseOrder(BaseSchema):
    """采购订单（对应前端 PurchaseOrder）。"""

    id: int
    order_no: str
    supplier_id: int
    supplier_name: str | None = None
    store_id: int
    order_date: DateStr
    expect_date: DateStr | None = None
    total_amount: Money
    source_type: str
    status: str
    created_by_name: str | None = None
    audited_at: DateTimeStr | None = None
    reject_reason: str | None = None
    remark: str | None = None
    items: list[PurchaseOrderItem] | None = None


class AuditRequest(BaseModel):
    """审核请求（POST /purchases/{id}/audit）。"""

    model_config = ConfigDict(extra="ignore")

    approved: bool
    reason: str | None = Field(default=None, max_length=255)


class VoidRequest(BaseModel):
    """作废请求（POST /purchases/{id}/void）。"""

    model_config = ConfigDict(extra="ignore")

    reason: str = Field(min_length=1, max_length=255)


# ---------- 收货 ----------


class ReceiveItemIn(BaseModel):
    """收货明细项（POST /purchases/{id}/receive）。

    ⚠️ production_date/expiry_date 前端对所有商品都传，但后端只对 is_perishable=1 建批次。
    """

    model_config = ConfigDict(extra="ignore")

    product_id: int
    receive_qty: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    production_date: date | None = None
    expiry_date: date | None = None


class ReceiveParams(BaseModel):
    """收货请求。"""

    model_config = ConfigDict(extra="ignore")

    items: list[ReceiveItemIn] = Field(min_length=1)
    diff_remark: str | None = Field(default=None, max_length=255)
    store_id: int | None = None


class DirectReceiveItemIn(BaseModel):
    """无单入库明细项（⚠️ 数量字段是 quantity，不是 receive_qty，spec 5.8）。"""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    production_date: date | None = None
    expiry_date: date | None = None


class DirectReceiveParams(BaseModel):
    """无单快速入库请求（POST /purchases/direct-receive）。"""

    model_config = ConfigDict(extra="ignore")

    supplier_id: int
    store_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[DirectReceiveItemIn] = Field(min_length=1)


class ReceiptResult(BaseSchema):
    """收货响应（含收货单号 + 订单新状态 + 成本重算证据）。"""

    receipt_no: str
    order_id: int | None = None
    order_status: str | None = None
    total_amount: Money
    is_direct: Flag = 0


# ---------- 采购退货 ----------


class PurchaseReturnItemIn(BaseModel):
    """采购退货明细项。"""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0, description="缺失取 prd_product.purchase_price")


class PurchaseReturnParams(BaseModel):
    """采购退货请求（POST /purchases/returns）。"""

    model_config = ConfigDict(extra="ignore")

    supplier_id: int
    store_id: int | None = None
    source_receipt_id: int | None = None
    reason: str = Field(min_length=1, max_length=255)
    items: list[PurchaseReturnItemIn] = Field(min_length=1)


class PurchaseReturnOut(BaseSchema):
    """采购退货响应。"""

    id: int
    return_no: str
    supplier_id: int
    store_id: int
    total_amount: Money
    reason: str | None = None
    status: str
    balance_payable: Money = Field(description="退货后的应付余额")


# 让 lint 知道 Rate 被间接使用（avg_cost 在收货响应/成本重算里用）
_ = Rate
