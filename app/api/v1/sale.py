"""销售与收银路由（阶段 4 交付物）—— 14 个接口。

对应 docs/06 §3.5：
| # | 方法 | 路径 | 权限码 | 幂等 |
| 1 | POST | /sales/cart/resolve | pos:use | 否 |
| 2 | POST | /sales/cart/calc    | pos:use | 否 |
| 3 | POST | /sales/trades       | pos:use | **是** |
| 4 | GET  | /sales/trades       | pos:use | 否 |
| 5 | GET  | /sales/trades/{id}  | pos:use | 否 |
| 6 | POST | /sales/trades/return | pos:use | 否 |
| 7 | POST | /sales/trades/{id}/void | pos:use | 否 |
| 8-11 | ... | /sales/holds* | pos:use | 否 |
| 12 | POST | /sales/trades/{id}/receipt | pos:use | 否 |
| 13 | GET  | /sales/sessions/current | pos:session | 否 |
| 14 | POST | /sales/sessions/close | pos:session | 否 |

⚠️ CP2 只实现 #1 与 #2；#3-#14 由 CP3/CP4 补齐。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, require_perm
from app.schemas.sale import CartCalcRequest, CartResolveRequest
from app.services import cart_service

router = APIRouter(prefix="/sales", tags=["销售与收银"])


# ---------- 1. POST /sales/cart/resolve ----------


@router.post(
    "/cart/resolve",
    summary="条码/编码解析",
    description=(
        "收银台扫码入口。按条码或商品编码解析商品信息，返回给前端加入购物车。\n\n"
        "⚠️ 匹配顺序（走唯一索引，P95 ≤ 200ms）：\n"
        "1. 先查 `prd_barcode.barcode`（含附加条码，`uk_barcode` 唯一索引）\n"
        "2. 未命中再查 `prd_product.product_code`（`uk_product_code` 唯一索引）\n\n"
        "⚠️ 关键约定：\n"
        "- **找不到 → `code=0` + `data=null`**（不是 6001；spec 三 ④）\n"
        "- 商品已停用（`status=0`）→ `6002`（该商品已停用）\n"
        "- 与阶段 3 的 `GET /products/barcode/{code}` 不同：那是**建档查重**，"
        "停用商品也返；本接口是**收银扫码**，停用商品必须拒绝"
    ),
)
def resolve_cart(
    params: CartResolveRequest,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """按条码或商品编码解析商品。

    Args:
        params: `{code: "..."}`。
        db: 数据库会话。
        current_user: 当前登录用户（用 store_id 查库存）。

    Returns:
        统一响应体，data = CartResolveResult 或 null（找不到时）。
    """
    store_id = current_user.store_id or 0
    result = cart_service.resolve_product(db, params.code, store_id=store_id)
    return success(result.model_dump(mode="json") if result else None)


# ---------- 2. POST /sales/cart/calc ----------


@router.post(
    "/cart/calc",
    summary="促销试算",
    description=(
        "输入购物车明细，返回命中的促销与优惠总额。**不落库**，纯计算。\n\n"
        "⚠️ 关键约定（spec 5.4）：\n"
        "- `discount_amount` **不含会员折扣、不含积分抵扣**\n"
        "- 会员折扣由前端 `stores/pos.ts:57` 单独计算与展示（后端返回会重复扣减）\n"
        "- `point_deduct` 恒为 0\n\n"
        "⚠️ 促销引擎与前端 Mock `promotion-engine.ts` **逐行对拍**（阶段 4 CP1 已验证）：\n"
        "- MEMBER 类型促销**不参与**计算（前端已单独处理会员折扣）\n"
        "- CATEGORY 类型促销**递归匹配父分类**（商品挂三级、促销挂二级也能命中）\n"
        "- COMBO 组合价**共享**（不累加多条明细的 promo_price）\n"
        "- 满减基数 = totalAmount - accumulated（已生效优惠累计）\n"
        "- `is_stackable=0` 的促销互斥（命中一个后其余不可叠加促销跳过）"
    ),
)
def calc_cart(
    params: CartCalcRequest,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """促销试算。

    Args:
        params: `{items: CartItem[], member_id?: number}`。
        db: 数据库会话。

    Returns:
        统一响应体，data = CartCalcResult。
    """
    result = cart_service.calc_cart(db, params)
    return success(result.model_dump(mode="json"))
