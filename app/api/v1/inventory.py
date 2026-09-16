"""库存管理路由（阶段 5 交付物）—— 14 个接口。

对应 docs/06 §3.3：
| # | 方法 | 路径 | 权限码 |
| 1 | GET  | /inventory                    | inv:stock:list |
| 2 | GET  | /inventory/flows              | inv:flow |
| 3 | GET  | /inventory/flows/export       | inv:flow |
| 4 | GET  | /inventory/warnings           | inv:stock:list |
| 5 | GET  | /inventory/checks             | inv:check |
| 6 | GET  | /inventory/checks/{id}        | inv:check |
| 7 | POST | /inventory/checks             | inv:check |
| 8 | PUT  | /inventory/checks/{id}/items  | inv:check |
| 9 | POST | /inventory/checks/{id}/submit | inv:check |
| 10| POST | /inventory/checks/{id}/audit  | inv:check |
| 11| GET  | /inventory/losses             | inv:loss |
| 12| POST | /inventory/losses             | inv:loss |
| 13| GET  | /inventory/transfers          | inv:transfer |
| 14| POST | /inventory/transfers          | inv:transfer |

⚠️ 路由顺序：`/inventory/flows/export` 必须在 `/inventory/flows` 之后但作为独立路径无冲突；
   `/inventory/checks/{id}/xxx` 子路径无歧义。

⚠️ CP1 只实现 #1/#2/#4；#3 由 CP2 补，#5-#14 由 CP2/CP3 补。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm, resolve_store_filter
from app.services import inventory_service

router = APIRouter(prefix="/inventory", tags=["库存管理"])


# ---------- 1. GET /inventory ----------


@router.get(
    "",
    summary="实时库存查询",
    description=(
        "分页查询库存，含 `stock_amount`（quantity×avg_cost）、`stock_status`、`remain_days`。\n\n"
        "⚠️ `stock_status` 优先级：LOW > NEAR_EXPIRY > OVER > NORMAL（spec 4.2）\n"
        "⚠️ 门店过滤：非 admin 只看本门店；admin（data_scope=ALL）看全部"
    ),
)
def list_inventory(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_STOCK_LIST))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(description="商品名称/编码")] = None,
    stock_status: Annotated[str | None, Query(description="NORMAL/LOW/OVER/NEAR_EXPIRY")] = None,
) -> dict[str, Any]:
    """GET /inventory —— 实时库存查询。"""
    store_filter = resolve_store_filter(current_user)
    items, total = inventory_service.list_stock(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        stock_status=stock_status,
        store_id=store_filter,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


# ---------- 2. GET /inventory/flows ----------


@router.get(
    "/flows",
    summary="库存流水台账",
    description="分页查询库存流水，按发生时间倒序，含商品名/操作人名/批次号。",
)
def list_flows(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_FLOW))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(description="商品名称")] = None,
    flow_type: Annotated[str | None, Query(description="流水类型（9个值之一）")] = None,
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /inventory/flows —— 库存流水台账。"""
    store_filter = resolve_store_filter(current_user)
    items, total = inventory_service.list_flows(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        flow_type=flow_type,
        store_id=store_filter,
        start_date=start_date,
        end_date=end_date,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


# ---------- 4. GET /inventory/warnings ----------


@router.get(
    "/warnings",
    summary="三类库存预警",
    description=(
        "返回 low_stock / over_stock / near_expiry 三个独立数组。\n\n"
        "⚠️ 一个商品可同时出现在多个数组（独立维度，不互斥）；每个数组最多 100 条（不分页）"
    ),
)
def get_warnings(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_STOCK_LIST))],
    store_id: Annotated[int | None, Query(description="门店ID（缺省用当前用户门店）")] = None,
) -> dict[str, Any]:
    """GET /inventory/warnings —— 三类预警。"""
    # store_id 显式传则用传入值，否则用 data_scope 解析
    effective_store = store_id if store_id is not None else resolve_store_filter(current_user)
    result = inventory_service.get_warnings(db, effective_store)
    return success(result.model_dump(mode="json"))
