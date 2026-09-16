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

from fastapi import APIRouter, Depends, Query, Response

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm, resolve_store_filter
from app.schemas.inventory import (
    CheckAuditRequest,
    CheckCreateRequest,
    CheckItemsUpdateRequest,
    LossCreateRequest,
    TransferCreateRequest,
)
from app.services import check_service, inventory_service, loss_service, transfer_service

router = APIRouter(prefix="/inventory", tags=["库存管理"])

# xlsx 标准 MIME（文档写的 application/vnd.ms-excel 是笔误，spec 5.3）
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


# ---------- 3. GET /inventory/flows/export ----------


@router.get(
    "/flows/export",
    summary="库存流水导出",
    description=(
        "返回 **xlsx 文件流**，⛔ **不包统一响应体**\n\n"
        "（前端 request.ts:88 检测到无 code 字段直接透传 blob）。\n"
        "⚠️ 行数上限 10000，超出返回 2001；文件名用 ASCII（避免下载乱码）"
    ),
    response_class=Response,
)
def export_flows(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_FLOW))],
    keyword: Annotated[str | None, Query()] = None,
    flow_type: Annotated[str | None, Query()] = None,
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
) -> Response:
    """GET /inventory/flows/export —— 导出 xlsx（不走统一响应体）。"""
    store_filter = resolve_store_filter(current_user)
    content, filename = inventory_service.export_flows(
        db,
        keyword=keyword,
        flow_type=flow_type,
        store_id=store_filter,
        start_date=start_date,
        end_date=end_date,
    )
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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


# ---------- 5. GET /inventory/checks ----------


@router.get(
    "/checks",
    summary="盘点单列表",
)
def list_checks(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_CHECK))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /inventory/checks —— 盘点单列表。"""
    store_filter = resolve_store_filter(current_user)
    items, total = check_service.list_checks(
        db, page=page, page_size=page_size, status=status, store_id=store_filter,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 7. POST /inventory/checks ----------


@router.post(
    "/checks",
    summary="创建盘点单（冻结快照）",
    description=(
        "取号 PD → 写主表 → 批量生成明细（book_qty=snapshot_qty=当前 quantity）。\n\n"
        "⚠️ check_type=ALL 全盘 / CATEGORY 按分类（递归子分类）/ SHELF 按货架 / PART 退化为全盘"
    ),
)
def create_check(
    params: CheckCreateRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_CHECK))],
) -> dict[str, Any]:
    """POST /inventory/checks —— 创建盘点单。"""
    store_id = current_user.store_id or 1
    result = check_service.create_check(
        db, params, store_id=store_id, user_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))


# ---------- 6. GET /inventory/checks/{check_id} ----------


@router.get(
    "/checks/{check_id}",
    summary="盘点单详情",
)
def get_check_detail(
    check_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.INV_CHECK))],
) -> dict[str, Any]:
    """GET /inventory/checks/{id} —— 详情（含明细）。"""
    result = check_service.get_check_detail(db, check_id)
    return success(result.model_dump(mode="json"))


# ---------- 8. PUT /inventory/checks/{check_id}/items ----------


@router.put(
    "/checks/{check_id}/items",
    summary="录入实盘数量",
    description="服务端重算 diff_qty/diff_rate/profit_loss（不信任前端）。只 DRAFT 可录入。",
)
def update_check_items(
    check_id: int,
    params: CheckItemsUpdateRequest,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.INV_CHECK))],
) -> dict[str, Any]:
    """PUT /inventory/checks/{id}/items —— 录入实盘。"""
    check_service.update_check_items(db, check_id, params.items)
    return success(None)


# ---------- 9. POST /inventory/checks/{check_id}/submit ----------


@router.post(
    "/checks/{check_id}/submit",
    summary="提交盘点审核",
    description="actual_qty 未填按 snapshot 补齐；diff_qty≠0 必须有 diff_reason（否则 5001）。",
)
def submit_check(
    check_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.INV_CHECK))],
) -> dict[str, Any]:
    """POST /inventory/checks/{id}/submit —— 提交审核。"""
    check_service.submit_check(db, check_id)
    return success(None)


# ---------- 10. POST /inventory/checks/{check_id}/audit ----------


@router.post(
    "/checks/{check_id}/audit",
    summary="审核盘点（生成调整流水）",
    description=(
        "approved=true → 增量调整库存 + 写 CHECK_ADJUST 流水（⛔ 不是覆盖）；\n"
        "approved=false → 驳回回 DRAFT。审核后库存为负则拒绝（绝不落负库存）。"
    ),
)
def audit_check(
    check_id: int,
    params: CheckAuditRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_CHECK))],
) -> dict[str, Any]:
    """POST /inventory/checks/{id}/audit —— 审核盘点。"""
    check_service.audit_check(db, check_id, params, auditor_id=current_user.user_id)
    return success(None)


# ---------- 11. GET /inventory/losses ----------


@router.get(
    "/losses",
    summary="报损单列表",
)
def list_losses(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_LOSS))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /inventory/losses —— 报损单列表（含 created_by_name + item_count）。"""
    store_filter = resolve_store_filter(current_user)
    items, total = loss_service.list_losses(
        db, page=page, page_size=page_size, status=status, store_id=store_filter,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 12. POST /inventory/losses ----------


@router.post(
    "/losses",
    summary="报损登记（创建即生效）",
    description=(
        "⛔ 请求体是 **items 数组**（spec 冲突表 ②）；\n"
        "loss_type 用短名 BREAK/EXPIRE/FRESH/OTHER。\n\n"
        "⚠️ 创建即扣库存 + 写 LOSS_OUT 流水 + status=FINISHED（无审核环节，spec 冲突表 ⑥）；\n"
        "unit_cost 以 inv_stock.avg_cost 为准（不信前端传值）"
    ),
)
def create_loss(
    params: LossCreateRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_LOSS))],
) -> dict[str, Any]:
    """POST /inventory/losses —— 报损登记。"""
    store_id = current_user.store_id or 1
    result = loss_service.create_loss(
        db, params, store_id=store_id, user_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))


# ---------- 13. GET /inventory/transfers ----------


@router.get(
    "/transfers",
    summary="调拨单列表",
)
def list_transfers(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_TRANSFER))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /inventory/transfers —— 调拨单列表（含 from_store/to_store 门店名 + item_count）。"""
    store_filter = resolve_store_filter(current_user)
    items, total = transfer_service.list_transfers(
        db, page=page, page_size=page_size, status=status, store_id=store_filter,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 14. POST /inventory/transfers ----------


@router.post(
    "/transfers",
    summary="调拨申请（一步到位）",
    description=(
        "⛔ 请求体是 **items 数组**（spec 冲突表 ②）。\n\n"
        "⚠️ 创建即完成调出+调入（spec 冲突表 ⑤ 补充设计）：\n"
        "调出门店扣库存写 TRANSFER_OUT、调入门店 upsert 写 TRANSFER_IN，status=IN。\n"
        "校验：from≠to、2001；门店不存在/停业、2001；调出库存不足、6003"
    ),
)
def create_transfer(
    params: TransferCreateRequest,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.INV_TRANSFER))],
) -> dict[str, Any]:
    """POST /inventory/transfers —— 调拨申请。"""
    result = transfer_service.create_transfer(
        db, params, user_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))
