"""库存管理模块的 Pydantic 模型（阶段 5 交付物）。

⚠️ 与前端 `qianduan/src/types/index.ts` 的 StockItem / StockFlow / CheckSheet /
   CheckSheetItem 一字对齐（前端不做字段映射，字段名不符 = 数据不显示）。

⚠️ 类型别名强制使用（决策 4）：
- `Money` (2 位)：stock_amount / amount / profit_loss / total_diff_amount
- `Rate` (6 位)：avg_cost / unit_cost / cost_price（DECIMAL(12,4)）、diff_rate（DECIMAL(8,4)）
- `Qty` (3 位)：quantity / safe_qty / max_qty / book_qty / snapshot_qty / actual_qty / diff_qty
- `DateTimeStr`：created_at / snapshot_at / audited_at / out_at / in_at
- `DateStr`：nearest_expiry / expiry_date

⚠️ 请求模型允许并忽略未知字段（extra='ignore'）：前端表单可能带 product_name 等冗余字段。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, DateStr, DateTimeStr, Money, Qty, Rate

# ---------- 5.1 实时库存 ----------


class StockItem(BaseSchema):
    """库存列表项（对应前端 types/index.ts:179-196 StockItem）。

    ⚠️ `stock_amount = quantity × avg_cost`（量化到分）
    ⚠️ `stock_status` 按优先级 LOW > NEAR_EXPIRY > OVER > NORMAL（spec 4.2）
    ⚠️ `remain_days` 可为 0 或负数（已过期），实时算日期差不依赖 inv_batch.status
    """

    id: int
    store_id: int
    store_name: str | None = None
    product_id: int
    product_code: str | None = None
    product_name: str
    spec: str | None = None
    unit_name: str | None = None
    quantity: Qty
    safe_qty: Qty
    max_qty: Qty | None = None
    avg_cost: Rate  # DECIMAL(12,4) 用 Rate
    stock_amount: Money | None = None
    stock_status: str | None = Field(default="NORMAL", description="NORMAL/LOW/OVER/NEAR_EXPIRY")
    nearest_expiry: DateStr | None = None
    remain_days: int | None = None


# ---------- 5.2 库存流水 ----------


class StockFlow(BaseSchema):
    """库存流水项（对应前端 types/index.ts:198-216 StockFlow）。"""

    id: int
    store_id: int
    product_id: int
    product_name: str | None = None
    flow_type: str
    direction: int
    quantity: Qty
    before_qty: Qty
    after_qty: Qty
    unit_cost: Rate  # DECIMAL(12,4)
    amount: Money
    source_type: str
    source_no: str | None = None
    batch_no: str | None = None
    operator_name: str | None = None
    remark: str | None = None
    created_at: DateTimeStr


# ---------- 5.4 三类预警 ----------


class WarningsResult(BaseSchema):
    """三类预警（spec 5.4）。

    ⚠️ 三个数组是**独立维度**，一个商品可同时出现在 low_stock 与 near_expiry。
    ⚠️ 字段名 low_stock/over_stock/near_expiry 与 STOCK_STATUS 的 LOW/OVER/NEAR_EXPIRY
       大小写不同，各自照原样输出（spec 冲突表 ⑧）。
    ⚠️ 每个数组最多 100 条（不分页）。
    """

    low_stock: list[StockItem] = Field(default_factory=list)
    over_stock: list[StockItem] = Field(default_factory=list)
    near_expiry: list[StockItem] = Field(default_factory=list)


# ---------- 5.5-5.8 盘点 ----------


class CheckSheetItem(BaseSchema):
    """盘点明细（对应前端 types/index.ts:218-232 CheckSheetItem）。"""

    id: int
    product_id: int
    product_code: str | None = None
    product_name: str | None = None
    spec: str | None = None
    book_qty: Qty
    snapshot_qty: Qty
    actual_qty: Qty | None = None
    diff_qty: Qty | None = None
    diff_rate: Rate | None = None  # DECIMAL(8,4)
    diff_reason: str | None = None
    cost_price: Rate  # DECIMAL(12,4)
    profit_loss: Money | None = None


class CheckSheet(BaseSchema):
    """盘点单（对应前端 types/index.ts:234-247 CheckSheet）。"""

    id: int
    check_no: str
    store_id: int
    check_type: str
    check_scope: str | None = None
    snapshot_at: DateTimeStr
    total_diff_amount: Money
    status: str
    created_by_name: str | None = None
    audited_at: DateTimeStr | None = None
    remark: str | None = None
    items: list[CheckSheetItem] | None = None


class CheckCreateRequest(BaseModel):
    """创建盘点单请求（Partial<CheckSheet>，spec 5.5）。"""

    model_config = ConfigDict(extra="ignore")

    check_type: str = Field(default="ALL", description="ALL/PART/CATEGORY/SHELF")
    check_scope: str | None = Field(default=None, max_length=255, description="盘点范围（分类ID/货架ID）")
    store_id: int | None = Field(default=None, description="门店ID（缺省用当前用户门店）")
    remark: str | None = Field(default=None, max_length=255)


class CheckItemInput(BaseModel):
    """录入实盘数量的单项。"""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(description="盘点明细ID")
    actual_qty: Decimal = Field(ge=0, description="实盘数量")
    diff_reason: str | None = Field(default=None, max_length=64, description="差异原因（5个中文值之一）")


class CheckItemsUpdateRequest(BaseModel):
    """录入实盘数量请求（PUT /inventory/checks/{id}/items）。"""

    model_config = ConfigDict(extra="ignore")

    items: list[CheckItemInput] = Field(min_length=1)


class CheckAuditRequest(BaseModel):
    """审核盘点请求（POST /inventory/checks/{id}/audit）。"""

    model_config = ConfigDict(extra="ignore")

    approved: bool = Field(description="true 通过 / false 驳回")
    remark: str | None = Field(default=None, max_length=255)


# ---------- 5.9 报损 ----------


class LossItemIn(BaseModel):
    """报损明细项（前端 loss.vue:18）。"""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    product_name: str | None = None
    quantity: Decimal = Field(gt=0)
    # ⚠️ unit_cost 前端传的只用于表单预览，后端以 inv_stock.avg_cost 为准（spec 5.9）
    unit_cost: Decimal | None = None


class LossCreateRequest(BaseModel):
    """报损登记请求（⛔ items 数组，spec 冲突表 ②）。"""

    model_config = ConfigDict(extra="ignore")

    loss_type: str = Field(description="BREAK/EXPIRE/FRESH/OTHER（短名，spec 冲突表 ①）")
    remark: str | None = Field(default=None, max_length=255)
    store_id: int | None = Field(default=None)
    items: list[LossItemIn] = Field(min_length=1)


class LossItemOut(BaseSchema):
    """报损明细响应。"""

    id: int
    product_id: int
    product_name: str | None = None
    quantity: Qty
    unit_cost: Rate
    amount: Money


class LossOut(BaseSchema):
    """报损单响应（GET /inventory/losses 列表项）。"""

    id: int
    loss_no: str
    store_id: int
    loss_type: str
    total_amount: Money
    reason: str | None = None
    status: str
    created_by: int
    created_by_name: str | None = None
    item_count: int = 0
    created_at: DateTimeStr


# ---------- 5.10 调拨 ----------


class TransferItemIn(BaseModel):
    """调拨明细项（前端 transfer.vue:19）。"""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal | None = None


class TransferCreateRequest(BaseModel):
    """调拨申请请求（⛔ items 数组，spec 冲突表 ②）。"""

    model_config = ConfigDict(extra="ignore")

    from_store_id: int
    to_store_id: int
    remark: str | None = Field(default=None, max_length=255)
    items: list[TransferItemIn] = Field(min_length=1)


class TransferItemOut(BaseSchema):
    """调拨明细响应。"""

    id: int
    product_id: int
    product_name: str | None = None
    quantity: Qty
    unit_cost: Rate
    amount: Money


class TransferOut(BaseSchema):
    """调拨单响应（GET /inventory/transfers 列表项）。"""

    id: int
    transfer_no: str
    from_store_id: int
    to_store_id: int
    from_store: str | None = None  # 门店名（JOIN base_store）
    to_store: str | None = None
    status: str
    item_count: int = 0
    created_by: int
    created_by_name: str | None = None
    out_at: DateTimeStr | None = None
    in_at: DateTimeStr | None = None
    remark: str | None = None
    created_at: DateTimeStr
