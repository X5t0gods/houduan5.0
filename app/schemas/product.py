"""商品/分类/单位模块的 Pydantic 模型（阶段 3 交付物）。

⚠️ 字段类型别名强制使用（spec 二.4，Pydantic v2 默认把 Decimal 序列化成字符串）：
- `Money` (2 位)：purchase_price / sale_price / member_price / min_price
- `Rate` (6 位)：avg_cost / conversion_rate —— **avg_cost 是 DECIMAL(12,4)，不能用 Money**（会截断成 2 位）
- `Qty` (3 位)：stock_qty
- `DateTimeStr`：created_at / updated_at
- `Flag` (int)：所有 TINYINT 状态位（is_weight / is_perishable / status 等）

⚠️ 请求模型必须**允许并忽略未知字段**（Pydantic 默认 extra='ignore'）：
- 前端 `edit.vue` 会提交 `init_stock` / `safe_stock`（spec 1.2 ②，文档外字段，本阶段忽略）
- 前端 `category.vue` 停用分类时会把整个 node 塞回来（含 `children` 数组，spec 1.2 ⑨）
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, DateTimeStr, Flag, Money, Qty, Rate

# ---------- 商品响应模型 ----------


class Product(BaseSchema):
    """商品档案响应模型（列表与详情共用主体，详情额外挂 barcodes）。

    ⚠️ 与 docs/06 §2.3 Product Schema 相比多两个字段（spec 1.2 ⑦）：
    - `purchase_unit_id`：采购单位 ID（建表脚本有，Schema 漏了）
    - `shelf_id`：默认货架 ID（同上）
    两者可为 null。
    """

    id: int = Field(description="商品ID")
    product_code: str = Field(description="商品编码，P+6位流水")
    product_name: str = Field(description="商品名称")
    category_id: int = Field(description="所属末级分类ID")
    # ⚠️ category_name 由后端 JOIN prd_category 得到，前端直接展示
    # 必须能取到三级分类名（Mock 的 flatMap(children) 有缺陷，spec 1.2 ⑩）
    category_name: str | None = Field(default=None, description="分类名称（JOIN 得到）")
    spu_id: int | None = Field(default=None, description="所属SPU ID")
    brand: str | None = Field(default=None, description="品牌")
    spec: str | None = Field(default=None, description="规格，如 330ml")
    unit_id: int = Field(description="基本单位ID")
    unit_name: str | None = Field(default=None, description="单位名称（JOIN 得到）")
    # spec 1.2 ⑦：建表脚本有但 Schema 漏的两个字段
    purchase_unit_id: int | None = Field(default=None, description="采购单位ID")
    shelf_id: int | None = Field(default=None, description="默认陈列货架ID")

    purchase_price: Money = Field(description="参考进价")
    sale_price: Money = Field(description="零售价")
    member_price: Money | None = Field(default=None, description="会员价")
    min_price: Money | None = Field(default=None, description="最低售价（改价下限）")
    # ⚠️ spec 1.2 ⑥：avg_cost 是 DECIMAL(12,4)，必须用 Rate（6 位）而非 Money（2 位）
    avg_cost: Rate = Field(description="移动加权平均成本（DECIMAL(12,4)）")

    is_weight: Flag = Field(description="是否称重商品 1是 0否")
    is_perishable: Flag = Field(description="是否保质期商品 1是 0否")
    shelf_life_days: int | None = Field(default=None, description="保质期天数")
    is_allow_return: Flag = Field(description="是否允许退货 1是 0否")
    # ⚠️ spec 1.2 ⑤：文档写"是否允许打折"，建表脚本注释是"是否参与促销"，以脚本为准
    is_allow_discount: Flag = Field(description="是否参与促销 1是 0否")
    is_allow_point: Flag = Field(description="是否参与积分 1是 0否")

    image_url: str | None = Field(default=None, description="商品图片地址")
    # ⚠️ stock_qty 来自 LEFT JOIN inv_stock + COALESCE(0)，新建商品无库存记录时返 0
    stock_qty: Qty = Field(default=Decimal("0"), description="当前库存数量（本门店）")
    status: Flag = Field(description="状态 1在售 0停用")
    remark: str | None = Field(default=None, description="备注")
    created_at: DateTimeStr = Field(description="创建时间")
    updated_at: DateTimeStr = Field(description="更新时间")


class ProductDetail(Product):
    """商品详情（额外含 barcodes 数组）。

    ⚠️ barcodes 按 barcode_type 升序：主条码（type=1）在前，附加条码（type=2）在后。
    """

    barcodes: list[str] = Field(default_factory=list, description="条码数组（主条码在前）")


# ---------- 商品请求模型 ----------


class ProductCreate(BaseModel):
    """新增商品请求（POST /products）。

    ⚠️ 前端 `{...form}` 会原样提交，包含 `init_stock` / `safe_stock`。
       阶段 3 曾忽略这两个字段，**阶段 5 已补齐**（spec 5.11）：
       init_stock>0 时写 inv_stock + inv_stock_flow + （保质期商品）inv_batch。

    ⚠️ `product_code` 可选：不传由后端 `sys_no_seq` 自动生成（P+6位）；
       传了则校验唯一性（阶段 3 联调时前端可能会预填）。
    """

    model_config = ConfigDict(extra="ignore")

    # 必填
    product_name: str = Field(min_length=1, max_length=64, description="商品名称")
    category_id: int = Field(description="所属末级分类ID")
    unit_id: int = Field(description="基本单位ID")
    purchase_price: Decimal = Field(ge=0, description="参考进价")
    sale_price: Decimal = Field(ge=0, description="零售价")

    # 可选
    product_code: str | None = Field(default=None, max_length=20, description="商品编码（不传则后端生成）")
    spu_id: int | None = Field(default=None, description="所属SPU ID")
    brand: str | None = Field(default=None, max_length=32, description="品牌")
    spec: str | None = Field(default=None, max_length=32, description="规格")
    purchase_unit_id: int | None = Field(default=None, description="采购单位ID")
    shelf_id: int | None = Field(default=None, description="默认陈列货架ID")
    member_price: Decimal | None = Field(default=None, ge=0, description="会员价")
    min_price: Decimal | None = Field(default=None, ge=0, description="最低售价")
    is_weight: Flag = Field(default=0, description="是否称重商品 1是 0否")
    is_perishable: Flag = Field(default=0, description="是否保质期商品 1是 0否")
    shelf_life_days: int | None = Field(default=None, ge=0, description="保质期天数（is_perishable=1 时必填）")
    is_allow_return: Flag = Field(default=1, description="是否允许退货")
    is_allow_discount: Flag = Field(default=1, description="是否参与促销")
    is_allow_point: Flag = Field(default=1, description="是否参与积分")
    image_url: str | None = Field(default=None, max_length=255, description="商品图片地址")
    remark: str | None = Field(default=None, max_length=255, description="备注")

    # 条码：优先取 barcodes 数组，为空时退回 barcode 单值（前端两种提交都可能出现）
    barcodes: list[str] = Field(default_factory=list, description="条码数组（一品多码）")
    barcode: str | None = Field(default=None, max_length=32, description="单值条码（冗余字段，barcodes 为空时用）")

    # 期初库存（阶段 5 补齐，spec 5.11）：仅**新增**时生效，编辑时忽略
    # ⚠️ 前端 edit.vue 会提交这两个字段；init_stock>0 时写 inv_stock + inv_stock_flow + (保质期商品)inv_batch
    init_stock: Decimal | None = Field(default=None, ge=0, description="期初库存（仅新增时生效，编辑忽略）")
    safe_stock: Decimal | None = Field(default=None, ge=0, description="安全库存（写入 inv_stock.safe_qty）")


class ProductUpdate(BaseModel):
    """修改商品请求（PUT /products/{id}）。

    ⚠️ 所有字段可选（partial update）；同样容忍 init_stock / safe_stock 等未知字段。
    ⚠️ 条码用 diff 方式更新（spec 4.4）：现有集合 A、提交集合 B，
       B-A INSERT / A-B DELETE / A∩B 保持不变（不动 created_at）。
    """

    model_config = ConfigDict(extra="ignore")

    product_name: str | None = Field(default=None, min_length=1, max_length=64)
    category_id: int | None = Field(default=None)
    unit_id: int | None = Field(default=None)
    purchase_price: Decimal | None = Field(default=None, ge=0)
    sale_price: Decimal | None = Field(default=None, ge=0)
    product_code: str | None = Field(default=None, max_length=20)
    spu_id: int | None = Field(default=None)
    brand: str | None = Field(default=None, max_length=32)
    spec: str | None = Field(default=None, max_length=32)
    purchase_unit_id: int | None = Field(default=None)
    shelf_id: int | None = Field(default=None)
    member_price: Decimal | None = Field(default=None, ge=0)
    min_price: Decimal | None = Field(default=None, ge=0)
    is_weight: Flag | None = Field(default=None)
    is_perishable: Flag | None = Field(default=None)
    shelf_life_days: int | None = Field(default=None, ge=0)
    is_allow_return: Flag | None = Field(default=None)
    is_allow_discount: Flag | None = Field(default=None)
    is_allow_point: Flag | None = Field(default=None)
    image_url: str | None = Field(default=None, max_length=255)
    remark: str | None = Field(default=None, max_length=255)
    barcodes: list[str] | None = Field(default=None, description="条码数组；None 表示不改，[] 表示清空")
    barcode: str | None = Field(default=None, max_length=32)


class ProductStatusUpdate(BaseModel):
    """停用/启用商品请求（PUT /products/{id}/status）。"""

    status: Flag = Field(description="1=在售 / 0=停用")


# ---------- 分类模型 ----------


class Category(BaseSchema):
    """分类节点（GET /categories 返回树形结构）。

    ⚠️ children 必须是 list（叶子节点为 `[]` 而非 `null`），否则前端 el-tree 会异常。
    """

    id: int = Field(description="分类ID")
    parent_id: int = Field(description="父分类ID，0=顶级")
    category_code: str = Field(description="分类编码")
    category_name: str = Field(description="分类名称")
    level: int = Field(description="层级 1/2/3")
    sort: int = Field(description="排序")
    status: Flag = Field(description="状态 1启用 0停用")
    children: list[Category] = Field(default_factory=list, description="子分类（叶子为 []）")
    created_at: DateTimeStr | None = Field(default=None, description="创建时间")
    updated_at: DateTimeStr | None = Field(default=None, description="更新时间")


class CategoryCreate(BaseModel):
    """新增分类请求（POST /categories）。

    ⚠️ 必须容忍未知字段（前端可能塞 children 等）。
    ⚠️ level 由后端根据 parent_id 自动计算，不接受前端传入。
    """

    model_config = ConfigDict(extra="ignore")

    category_code: str = Field(min_length=1, max_length=20, description="分类编码")
    category_name: str = Field(min_length=1, max_length=64, description="分类名称")
    parent_id: int = Field(default=0, ge=0, description="父分类ID，0=顶级")
    sort: int = Field(default=0, ge=0, description="排序")
    status: Flag = Field(default=1, description="状态 1启用 0停用")


class CategoryUpdate(BaseModel):
    """修改分类请求（PUT /categories/{id}）。

    ⚠️ 必须容忍 children 等未知字段（spec 1.2 ⑨：`category.vue:112` 是
       `updateCategory(node.id, { ...node, status: 0 })`，node 带 children）。
    ⚠️ 禁止把父分类改成自己的子孙（spec 4.9-4.11，会把树搞成环）。
    """

    model_config = ConfigDict(extra="ignore")

    category_code: str | None = Field(default=None, min_length=1, max_length=20)
    category_name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = Field(default=None, ge=0)
    sort: int | None = Field(default=None, ge=0)
    status: Flag | None = Field(default=None)


# ---------- 单位模型 ----------


class Unit(BaseSchema):
    """计量单位（GET /units 直接返回 base_unit 表行）。

    ⚠️ spec 1.2 ⑧：前端 `api/product.ts:66` 声明的是 `Array<Record<string, unknown>>`，
       直接返回表行即可（含 sort / unit_type / base_unit_id / conversion_rate）。
    ⚠️ conversion_rate 是 DECIMAL(12,4)，用 Rate 别名（不能用 Money）。
    """

    id: int = Field(description="单位ID")
    unit_name: str = Field(description="单位名称，如 瓶/箱/斤")
    unit_type: Flag = Field(description="单位类型 1基本单位 2辅助单位")
    base_unit_id: int | None = Field(default=None, description="所属基本单位ID")
    conversion_rate: Rate = Field(description="换算系数（DECIMAL(12,4)）")
    sort: int = Field(description="排序")
    status: Flag = Field(description="状态 1启用 0停用")
    created_at: DateTimeStr | None = Field(default=None, description="创建时间")
    updated_at: DateTimeStr | None = Field(default=None, description="更新时间")


# ---------- 导入结果 ----------


class ImportResult(BaseSchema):
    """Excel 批量导入响应（POST /products/import 的 data）。

    ⚠️ errors 格式固定为 `第 {行号} 行：{原因}`，行号从表头之后第 1 行数据算起
       （即 Excel 的第 2 行）。
    """

    total: int = Field(description="总行数")
    success: int = Field(description="成功条数")
    failed: int = Field(description="失败条数")
    errors: list[str] = Field(default_factory=list, description="逐行错误说明")


# ---------- Category 自引用需要 model_rebuild ----------
Category.model_rebuild()


# ---------- 导入 Excel 的列顺序常量（spec 4.13） ----------
# 前端没有模板下载接口，此列顺序作为约定写死在导入解析逻辑里，交付说明中明示
IMPORT_COLUMN_ORDER: list[str] = [
    "product_name",      # A：商品名称（必填）
    "category_name",     # B：分类名称（必填，按名称匹配）
    "unit_name",         # C：单位名称（必填，按名称匹配）
    "product_code",      # D：商品编码（可选，不填自动生成）
    "spec",              # E：规格（可选）
    "barcodes",          # F：条码（可选，多个用 , 或 、 分隔）
    "purchase_price",    # G：进价（必填）
    "sale_price",        # H：售价（必填）
    "member_price",      # I：会员价（可选）
    "is_weight",         # J：是否称重 1/0（可选）
    "shelf_life_days",   # K：保质期天数（可选）
    "remark",            # L：备注（可选）
]

# 导入行数上限（超出返回 2001）
IMPORT_MAX_ROWS: int = 2000
