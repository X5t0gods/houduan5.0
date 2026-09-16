"""商品路由（阶段 3 交付物）—— 8 个接口。

对应 docs/06 §3.2.1-3.2.7 + 3.2.13：
| # | 方法 | 路径 | 权限 |
| 1 | GET    | /products                    | goods:product:list |
| 2 | GET    | /products/{id}               | goods:product:list |
| 3 | POST   | /products                    | goods:product:edit |
| 4 | PUT    | /products/{id}               | goods:product:edit |
| 5 | PUT    | /products/{id}/status        | goods:product:edit |
| 6 | DELETE | /products/{id}               | goods:product:edit |
| 7 | GET    | /products/barcode/{barcode}  | pos:use |
| 13| POST   | /products/import             | goods:product:edit |

⚠️ 路由顺序至关重要：`/products/barcode/{barcode}` 必须在 `/products/{id}` 之前声明，
   否则 FastAPI 会把 "barcode" 当作 product_id 尝试解析为 int 失败。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, require_perm
from app.schemas.common import PageResult
from app.schemas.product import (
    ImportResult,
    Product,
    ProductCreate,
    ProductDetail,
    ProductStatusUpdate,
    ProductUpdate,
)
from app.services import product_service

router = APIRouter(prefix="/products", tags=["商品管理"])


# ---------- 1. GET /products ----------


@router.get(
    "",
    summary="商品分页查询",
    description=(
        "分页查询商品列表。\n\n"
        "**Query 参数**：\n"
        "- `page`（默认 1）、`page_size`（默认 20，≤100）\n"
        "- `keyword`：模糊匹配 **商品名称 / 商品编码 / 条码**（三选一命中即可）\n"
        "- `category_id`：可选，按分类过滤\n\n"
        "⚠️ 关键实现（spec 4.1）：\n"
        "- 条码搜索**用 EXISTS 子查询**，不用 JOIN —— 商品 14 有 2 个条码，"
        "JOIN 会让它在列表里出现两次、total 虚高\n"
        "- `stock_qty` 从 `inv_stock` 用 **LEFT JOIN + COALESCE(quantity, 0)**，"
        "不用 INNER JOIN（新建商品无库存记录会整条消失）\n"
        "- `category_name` / `unit_name` 由后端 JOIN 得到（前端不再查一次）\n\n"
        "排序：按 id 倒序（最新建的在前）。"
    ),
)
def list_products(
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_LIST))],
    page: Annotated[int, Query(ge=1, description="页码，从1开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
    keyword: Annotated[str | None, Query(description="模糊匹配名称/编码/条码")] = None,
    category_id: Annotated[int | None, Query(description="按分类过滤")] = None,
) -> dict[str, Any]:
    """分页查询商品。

    Args:
        db: 数据库会话。
        current_user: 当前登录用户（require_perm 依赖）。
        page: 页码。
        page_size: 每页条数（≤100）。
        keyword: 模糊匹配关键字。
        category_id: 分类过滤。

    Returns:
        统一响应体，data = PageResult[Product]。
    """
    store_id = current_user.store_id or 0
    products, total = product_service.list_products(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        category_id=category_id,
        store_id=store_id,
    )
    page_result = PageResult[Product](
        # Pydantic 用 items=... 构造，序列化时自动改回 list（阶段 0 已修复的 alias）
        items=[p.model_dump(mode="json") for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )
    return success(page_result.model_dump(mode="json"))


# ---------- 7. GET /products/barcode/{barcode} ----------
# ⚠️ 必须在 /products/{product_id} 之前声明，否则 "barcode" 会被当作 int 解析失败


@router.get(
    "/barcode/{barcode}",
    summary="按条码查商品（建档查重）",
    description=(
        "按条码查商品，**用于商品建档时的条码查重**。\n\n"
        "⚠️ 关键约定（spec 4.7 + 1.2 ④）：\n"
        "- 权限：`pos:use`\n"
        "- **找不到时返回 `code=0` + `data=null`**（不是错误码 6001）\n"
        "  查重场景下'找不到'是**正常结果**（说明条码可用），不该弹错误\n"
        "  6001 留给阶段 4 的 `/sales/cart/resolve`\n"
        "- 商品**已停用**（status=0）时**照样返回**（查重不是收银）\n"
        "- 支持主条码与附加条码"
    ),
)
def get_product_by_barcode(
    barcode: str,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """按条码查商品。

    Args:
        barcode: 条码字符串。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = Product 或 null（找不到时）。
    """
    store_id = current_user.store_id or 0
    product = product_service.get_by_barcode(db, barcode, store_id=store_id)
    return success(product.model_dump(mode="json") if product else None)


# ---------- 13. POST /products/import ----------
# ⚠️ 也必须在 /products/{product_id} 之前声明


@router.post(
    "/import",
    summary="Excel 批量导入商品",
    description=(
        "上传 `.xlsx` 文件批量导入商品。**逐行校验 + 逐行提交**，"
        "某行失败不影响其他行（部分成功即可用）。\n\n"
        "**Excel 列顺序**（表头在第 1 行，数据从第 2 行开始）：\n"
        "| 列 | 字段 | 必填 |\n"
        "|---|---|---|\n"
        "| A | 商品名称 | ✅ |\n"
        "| B | 分类名称 | ✅（按名称匹配）|\n"
        "| C | 单位名称 | ✅（按名称匹配）|\n"
        "| D | 商品编码 | 否（不填自动生成 P+6位）|\n"
        "| E | 规格 | 否 |\n"
        "| F | 条码 | 否（多个用 `,` 或 `、` 分隔）|\n"
        "| G | 进价 | ✅ |\n"
        "| H | 售价 | ✅ |\n"
        "| I | 会员价 | 否 |\n"
        "| J | 是否称重（1/0）| 否 |\n"
        "| K | 保质期天数 | 否 |\n"
        "| L | 备注 | 否 |\n\n"
        "⚠️ 约束（spec 4.13）：\n"
        "- **只支持 `.xlsx`**（不支持 .xls 老格式，xlrd 2.x 已不支持 xlsx，openpyxl 反向不兼容）\n"
        "- **≤ 2000 行**，超出返回 2001\n"
        "- **批内重复预扫描**：第 3 行和第 7 行同编码 → 两行都失败（不能一行成功一行撞库）\n"
        "- errors 格式固定为 `第 {行号} 行：{原因}`，行号从数据第 1 行开始（即 Excel 第 2 行）"
    ),
)
def import_products(
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_EDIT))],
    file: Annotated[UploadFile, File(description=".xlsx 文件")],
) -> dict[str, Any]:
    """Excel 批量导入商品。

    Args:
        db: 数据库会话。
        current_user: 当前登录用户。
        file: 上传的 .xlsx 文件（multipart/form-data，字段名固定为 `file`）。

    Returns:
        统一响应体，data = ImportResult(total, success, failed, errors)。
    """
    file_bytes = file.file.read()
    result = product_service.import_products(
        db,
        file_bytes=file_bytes,
        filename=file.filename or "unknown.xlsx",
        operator_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))


# ---------- 2. GET /products/{id} ----------


@router.get(
    "/{product_id}",
    summary="商品详情",
    description=(
        "按 ID 查商品详情，返回 `ProductDetail`（比列表多 `barcodes` 数组）。\n\n"
        "- `barcodes` 按 `barcode_type` 升序：**主条码（type=1）在前**，附加条码在后\n"
        "- 同样含 `category_name` / `unit_name` / `stock_qty`\n"
        "- 不存在 → `2004`"
    ),
)
def get_product(
    product_id: int,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_LIST))],
) -> dict[str, Any]:
    """获取商品详情。

    Args:
        product_id: 商品 ID。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = ProductDetail。

    Raises:
        BusinessError(2004): 商品不存在。
    """
    store_id = current_user.store_id or 0
    detail = product_service.get_product_detail(db, product_id, store_id=store_id)
    return success(detail.model_dump(mode="json"))


# ---------- 3. POST /products ----------


@router.post(
    "",
    summary="新增商品",
    description=(
        "新增商品建档。\n\n"
        "**字段处理**（spec 4.3）：\n"
        "- `product_code`：**不传则后端自动生成**（走 sys_no_seq 的 P 键，格式 P+6位）\n"
        "- `barcodes` 优先，为空时退回 `barcode` 单值；第一个是主条码，其余是附加条码\n"
        "- ⚠️ **`init_stock` / `safe_stock` 会被忽略**（Pydantic extra='ignore'，spec 1.2 ②）\n"
        "  期初入库属阶段 5，本阶段不落库不写流水\n\n"
        "**校验顺序**（先报先返回）：\n"
        "1. 必填：product_name / category_id / unit_id → `2001`\n"
        "2. category_id 不存在 → `2005`\n"
        "3. product_code 或任一条码已存在 → `2002`\n"
        "4. `sale_price < purchase_price` → `2003`（⛔ 前端不会拦，后端必须拦，spec 1.2 ①）\n"
        "5. `is_perishable=1` 但 shelf_life_days 缺失 → `2001`\n\n"
        "⚠️ 文档写的\"purchase_price ≤ member_price ≤ sale_price\"是**建议**不是强制，"
        "不对 member_price 做校验（初始数据里合法情况很多）。"
    ),
)
def create_product(
    params: ProductCreate,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_EDIT))],
) -> dict[str, Any]:
    """新增商品。

    Args:
        params: 商品创建参数（Pydantic extra='ignore'，容忍未知字段）。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = ProductDetail（含新分配的 id/product_code）。
    """
    detail = product_service.create_product(db, params, operator_id=current_user.user_id)
    return success(detail.model_dump(mode="json"))


# ---------- 4. PUT /products/{id} ----------


@router.put(
    "/{product_id}",
    summary="修改商品",
    description=(
        "修改商品（partial update）。校验规则同新增，但**排除自身**。\n\n"
        "⚠️ 关键实现（spec 4.4）：\n"
        "- **条码用 diff 方式更新**：现有 A、提交 B，B-A INSERT / A-B DELETE / "
        "A∩B **保持不变**（不动 created_at）\n"
        "  ⛔ 不全量删了重建，会丢失 created_at 且制造无谓的删除/插入\n"
        "- **价格变更写 `prd_price_history`**：三个价（进价/售价/会员价）"
        "**哪个变了单独写一条**，`change_type` 分别是 `'进价'`/`'售价'`/`'会员价'`\n"
        "  ⛔ 三个价都没变时**不写**（否则改个备注也刷一条垃圾记录）"
    ),
)
def update_product(
    product_id: int,
    params: ProductUpdate,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_EDIT))],
) -> dict[str, Any]:
    """修改商品。

    Args:
        product_id: 商品 ID。
        params: 修改参数（partial，Pydantic extra='ignore'）。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = ProductDetail。
    """
    detail = product_service.update_product(db, product_id, params, current_user.user_id)
    return success(detail.model_dump(mode="json"))


# ---------- 5. PUT /products/{id}/status ----------


@router.put(
    "/{product_id}/status",
    summary="停用/启用商品",
    description=(
        "只改状态位（`1` 在售 / `0` 停用），**不做其他业务判断**。\n\n"
        "⚠️ 停用后收银台扫码返回 6002 是**阶段 4** 的事，本阶段不实现。"
    ),
)
def update_product_status(
    product_id: int,
    params: ProductStatusUpdate,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_EDIT))],
) -> dict[str, Any]:
    """停用/启用商品。

    Args:
        product_id: 商品 ID。
        params: `{status: 0 或 1}`。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = null。
    """
    product_service.update_status(db, product_id, params.status, current_user.user_id)
    return success(None)


# ---------- 6. DELETE /products/{id} ----------


@router.delete(
    "/{product_id}",
    summary="删除商品",
    description=(
        "**仅当商品无任何业务流水时允许删除**，否则返回 `2007`。\n\n"
        "⚠️ 三个必须注意的点（spec 4.6）：\n"
        "1. **判定范围**：11 张业务流水表任一有记录即拒绝\n"
        "   （sal_trade_item / sal_return_item / pur_order_item / pur_receipt_item / "
        "pur_return_item / inv_stock / inv_stock_flow / inv_batch / inv_check_item / "
        "inv_loss_item / inv_transfer_item）\n"
        "2. **必须先删子表**：18 张表外键引用 prd_product(id) 且全部 RESTRICT，"
        "不先删子表会直接抛外键约束错误（500）\n"
        "3. **20 个演示商品一个都删不掉**：全部有 inv_stock + inv_stock_flow 记录，"
        "点删除必然返回 2007 —— 这是预期行为，要验证成功路径请先新建再删\n\n"
        "**本阶段清理范围**：prd_barcode / prd_product_tag / prd_price_history / "
        "pur_supplier_product\n"
        "**待办**（阶段 6/9 补）：pro_promotion_item.gift_product_id / "
        "bi_recommend_cache.product_id"
    ),
)
def delete_product(
    product_id: int,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_EDIT))],
) -> dict[str, Any]:
    """删除商品。

    Args:
        product_id: 商品 ID。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = null。
    """
    product_service.delete_product(db, product_id, current_user.user_id)
    return success(None)


# 让 lint 知道这些 import 是被间接使用的
_ = (ImportResult, ProductDetail)
