"""采购订单路由（阶段 6 交付物）—— 9 个采购接口。

对应 docs/06 §3.4：
| 1 | GET  | /purchases                 | pur:order:list   |
| 2 | GET  | /purchases/{id}            | pur:order:list   |
| 3 | POST | /purchases                 | pur:order:create |
| 4 | PUT  | /purchases/{id}            | pur:order:create |
| 5 | POST | /purchases/{id}/audit      | pur:order:create |
| 6 | POST | /purchases/{id}/void       | pur:order:create |
| 7 | POST | /purchases/{id}/receive    | pur:receipt      |
| 8 | POST | /purchases/direct-receive  | pur:receipt      |
| 9 | POST | /purchases/returns         | pur:receipt      |

⚠️ 路由顺序至关重要：`/purchases/direct-receive` 与 `/purchases/returns`（CP4）
   必须在 `/purchases/{id}` 之前声明，否则会被当作 order_id 解析。

⚠️ CP3 只实现 #1-#6；#7-#9 由 CP4 补。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm, resolve_store_filter
from app.schemas.purchase import (
    AuditRequest,
    PurchaseOrderCreate,
    PurchaseOrderUpdate,
    VoidRequest,
)
from app.services import purchase_service

router = APIRouter(prefix="/purchases", tags=["采购管理"])


# ---------- 3. POST /purchases ----------


@router.post(
    "",
    summary="创建采购订单",
    description=(
        "一律产生 DRAFT（spec 冲突表 ⑦：不接受 status 参数）。source_type 可选缺省 MANUAL。\n\n"
        "⚠️ 前端会多传 product_name/spec/unit_name（纯展示）→ extra=ignore 容忍"
    ),
)
def create_order(
    params: PurchaseOrderCreate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_ORDER_CREATE))],
) -> dict[str, Any]:
    """POST /purchases —— 创建采购订单。"""
    store_id = current_user.store_id or 1
    result = purchase_service.create_order(
        db, params, store_id=store_id, operator_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))


# ---------- 1. GET /purchases ----------


@router.get("", summary="采购订单列表")
def list_orders(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_ORDER_LIST))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query(description="6个状态枚举之一")] = None,
    supplier_id: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    """GET /purchases —— 采购订单分页列表（含 supplier_name + created_by_name）。"""
    store_filter = resolve_store_filter(current_user)
    items, total = purchase_service.list_orders(
        db, page=page, page_size=page_size,
        status=status, supplier_id=supplier_id, store_id=store_filter,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 2. GET /purchases/{order_id} ----------


@router.get("/{order_id}", summary="采购订单详情")
def get_order_detail(
    order_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PUR_ORDER_LIST))],
) -> dict[str, Any]:
    """GET /purchases/{id} —— 详情（items 含 JOIN 出的 product_code/name/spec/unit_name）。"""
    result = purchase_service.get_order_detail(db, order_id)
    return success(result.model_dump(mode="json"))


# ---------- 4. PUT /purchases/{order_id} ----------


@router.put(
    "/{order_id}",
    summary="修改采购订单",
    description="⛔只有 DRAFT 可修改（否则 4004）；明细全量替换；已收货拒绝修改。",
)
def update_order(
    order_id: int,
    params: PurchaseOrderUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_ORDER_CREATE))],
) -> dict[str, Any]:
    """PUT /purchases/{id} —— 修改采购订单。"""
    result = purchase_service.update_order(
        db, order_id, params, operator_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))


# ---------- 5. POST /purchases/{order_id}/audit ----------


@router.post(
    "/{order_id}/audit",
    summary="审核采购订单",
    description="仅 DRAFT 可审核；approved=true→AUDITED，false→保持 DRAFT + reject_reason。",
)
def audit_order(
    order_id: int,
    params: AuditRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_ORDER_CREATE))],
) -> dict[str, Any]:
    """POST /purchases/{id}/audit —— 审核。"""
    purchase_service.audit_order(db, order_id, params, operator_id=current_user.user_id)
    return success(None)


# ---------- 6. POST /purchases/{order_id}/void ----------


@router.post(
    "/{order_id}/void",
    summary="作废采购订单",
    description="⛔只有 DRAFT 可作废（否则 4004）；已收货不可能回 DRAFT，无需回补库存。",
)
def void_order(
    order_id: int,
    params: VoidRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_ORDER_CREATE))],
) -> dict[str, Any]:
    """POST /purchases/{id}/void —— 作废。"""
    purchase_service.void_order(db, order_id, params, operator_id=current_user.user_id)
    return success(None)
