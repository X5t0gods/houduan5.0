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
| 8 | POST | /sales/holds        | pos:use | 否 |
| 9 | GET  | /sales/holds        | pos:use | 否 |
| 10| POST | /sales/holds/{holdNo}/resume | pos:use | 否 |
| 11| DELETE| /sales/holds/{holdNo} | pos:use | 否 |
| 12| POST | /sales/trades/{id}/receipt | pos:use | 否 |
| 13| GET  | /sales/sessions/current | pos:session | 否 |
| 14| POST | /sales/sessions/close | pos:session | 否 |

⚠️ 路由顺序至关重要：
   - `/sales/trades/return` 必须在 `/sales/trades/{id}` 之前（否则 "return" 被解析为 id）
   - `/sales/trades/{id}/void` 和 `/sales/trades/{id}/receipt` 放最后
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm, resolve_store_filter
from app.schemas.sale import (
    CartCalcRequest,
    CartResolveRequest,
    HoldCreateRequest,
    HoldOut,
    SaleReturnCreateRequest,
    SessionCloseRequest,
    TradeCreateRequest,
    VoidTradeRequest,
)
from app.services import cart_service, sale_service

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
        "- `point_deduct` 恒为 0"
    ),
)
def calc_cart(
    params: CartCalcRequest,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """促销试算。"""
    result = cart_service.calc_cart(db, params)
    return success(result.model_dump(mode="json"))


# ---------- 3. POST /sales/trades ----------


@router.post(
    "/trades",
    summary="结算（幂等）",
    description=(
        "**本阶段最核心接口**。前端传 items + payments + request_id，\n"
        "后端重算全部优惠（促销 + 会员折扣 + 积分抵扣）并校验支付金额。\n\n"
        "⚠️ 幂等：同一 request_id 重复提交返回首次结果（不重复扣库存/扣款）"
    ),
)
def create_trade(
    params: TradeCreateRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /sales/trades —— 结算事务 T1。"""
    store_id = current_user.store_id or 1
    result = sale_service.settle_trade(
        db, params,
        store_id=store_id,
        cashier_id=current_user.user_id,
        cashier_name=current_user.real_name,
    )
    return success(result.model_dump(mode="json"))


# ---------- 6. POST /sales/trades/return ----------
# ⚠️ 必须在 /trades/{trade_id} 之前声明，否则 "return" 被当作 trade_id


@router.post(
    "/trades/return",
    summary="销售退货",
    description="退货回补库存、退款给会员（只退本金不退赠送）、扣回积分。",
)
def return_trade(
    params: SaleReturnCreateRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /sales/trades/return —— 退货。"""
    store_id = current_user.store_id or 1
    result = sale_service.return_trade(
        db, params,
        store_id=store_id,
        operator_id=current_user.user_id,
        operator_name=current_user.real_name,
    )
    return success(result.model_dump(mode="json"))


# ---------- 4. GET /sales/trades ----------


@router.get(
    "/trades",
    summary="销售单列表",
)
def list_trades(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_USE))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query()] = None,
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /sales/trades —— 分页查询销售单。"""
    store_filter = resolve_store_filter(current_user)
    items, total = sale_service.list_trades(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        status=status,
        store_id=store_filter,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


# ---------- 5. GET /sales/trades/{trade_id} ----------


@router.get(
    "/trades/{trade_id}",
    summary="销售单详情",
)
def get_trade_detail(
    trade_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """GET /sales/trades/{id} —— 详情（含 items + payments）。"""
    result = sale_service.get_trade_detail(db, trade_id)
    return success(result.model_dump(mode="json"))


# ---------- 7. POST /sales/trades/{trade_id}/void ----------


@router.post(
    "/trades/{trade_id}/void",
    summary="作废销售单",
)
def void_trade(
    trade_id: int,
    params: VoidTradeRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /sales/trades/{id}/void —— 作废。"""
    store_id = current_user.store_id or 1
    sale_service.void_trade(
        db, trade_id, params,
        store_id=store_id,
        operator_id=current_user.user_id,
    )
    return success(None)


# ---------- 12. POST /sales/trades/{trade_id}/receipt ----------


@router.post(
    "/trades/{trade_id}/receipt",
    summary="获取小票",
)
def get_receipt(
    trade_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /sales/trades/{id}/receipt —— 小票（print_count +1）。"""
    result = sale_service.generate_receipt(db, trade_id)
    return success(result.model_dump(mode="json"))


# ---------- 8. POST /sales/holds ----------


@router.post(
    "/holds",
    summary="挂单",
)
def create_hold(
    params: HoldCreateRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /sales/holds —— 挂单。"""
    hold_no = sale_service.create_hold(
        db,
        pos_id=params.pos_id,
        store_id=current_user.store_id or 1,
        member_id=params.member_id,
        cart_json=params.cart_json,
        item_count=params.item_count,
        amount=params.amount,
        operator_id=current_user.user_id,
    )
    return success({"hold_no": hold_no})


# ---------- 9. GET /sales/holds ----------


@router.get(
    "/holds",
    summary="挂单列表",
)
def list_holds(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_USE))],
    pos_id: Annotated[int, Query()] = 1,
) -> dict[str, Any]:
    """GET /sales/holds —— 只返回 status='HOLDING' 的挂单。"""
    holds = sale_service.list_holds(db, pos_id, current_user.store_id)
    items = [
        HoldOut(
            id=h.id,
            hold_no=h.hold_no,
            store_id=h.store_id,
            pos_id=h.pos_id,
            member_id=h.member_id,
            cart_json=(
                h.cart_json if isinstance(h.cart_json, str)
                else __import__("json").dumps(h.cart_json, ensure_ascii=False)
            ),
            item_count=h.item_count,
            amount=h.amount,
            status=h.status,
            created_at=h.created_at,
        ).model_dump(mode="json")
        for h in holds
    ]
    return success(items)


# ---------- 10. POST /sales/holds/{hold_no}/resume ----------


@router.post(
    "/holds/{hold_no}/resume",
    summary="取单",
)
def resume_hold(
    hold_no: str,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /sales/holds/{holdNo}/resume —— 取单（状态置 RESUMED）。"""
    result = sale_service.resume_hold(db, hold_no)
    return success(result)


# ---------- 11. DELETE /sales/holds/{hold_no} ----------


@router.delete(
    "/holds/{hold_no}",
    summary="取消挂单",
)
def cancel_hold(
    hold_no: str,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """DELETE /sales/holds/{holdNo} —— 软删除（置 CANCELED）。"""
    sale_service.cancel_hold(db, hold_no)
    return success(None)


# ---------- 13. GET /sales/sessions/current ----------


@router.get(
    "/sessions/current",
    summary="获取当前班次",
    description="没有则自动开班（JB 取号，init_cash=0）。",
)
def get_current_session(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_SESSION))],
    pos_id: Annotated[int, Query()] = 1,
) -> dict[str, Any]:
    """GET /sales/sessions/current —— 当前班次（无则自动开班）。"""
    result = sale_service.get_or_open_session(
        db,
        pos_id=pos_id,
        store_id=current_user.store_id or 1,
        cashier_id=current_user.user_id,
    )
    return success(result)


# ---------- 14. POST /sales/sessions/close ----------


@router.post(
    "/sessions/close",
    summary="交班",
)
def close_session(
    params: SessionCloseRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.POS_SESSION))],
) -> dict[str, Any]:
    """POST /sales/sessions/close —— 交班。"""
    result = sale_service.close_session(
        db, params, operator_id=current_user.user_id,
    )
    return success(result)
