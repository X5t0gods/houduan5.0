"""供应商路由（阶段 6 CP2 交付物）—— 5 个接口。

对应 docs/06 §3.4：
| 10 | GET  | /suppliers                        | pur:supplier |
| 11 | POST | /suppliers                        | pur:supplier |
| 12 | PUT  | /suppliers/{id}                   | pur:supplier |
| 13 | GET  | /suppliers/{id}/statement         | pur:supplier |
| 14 | POST | /suppliers/payments               | pur:supplier |

⚠️ 路由顺序：`/suppliers/payments`（POST）与 `/suppliers/{id}`（PUT）方法不同无冲突；
   `/suppliers/{id}/statement` 是子路径无歧义。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm
from app.schemas.purchase import PaymentParams, SupplierCreate, SupplierUpdate
from app.services import supplier_service

router = APIRouter(prefix="/suppliers", tags=["供应商管理"])


# ---------- 10. GET /suppliers ----------


@router.get("", summary="供应商列表")
def list_suppliers(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PUR_SUPPLIER))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(description="名称/编码/联系人")] = None,
    status: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    """GET /suppliers —— 供应商分页列表。"""
    items, total = supplier_service.list_suppliers(
        db, page=page, page_size=page_size, keyword=keyword, status=status,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 14. POST /suppliers/payments ----------
# ⚠️ 放在 /suppliers/{id} 之前，避免 "payments" 被当作 supplier_id 解析


@router.post(
    "/payments",
    summary="付款登记",
    description="冲减应付余额（允许为负）；pay_no 用 FK 取号。amount<=0→9002，非法方式→2001。",
)
def create_payment(
    params: PaymentParams,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_SUPPLIER))],
) -> dict[str, Any]:
    """POST /suppliers/payments —— 付款登记。"""
    result = supplier_service.create_payment(db, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 11. POST /suppliers ----------


@router.post(
    "",
    summary="新增供应商",
    description="supplier_code 后端自动生成（S 取号）；balance_payable 强制 0（不接受前端传入）。",
)
def create_supplier(
    params: SupplierCreate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_SUPPLIER))],
) -> dict[str, Any]:
    """POST /suppliers —— 新增供应商。"""
    result = supplier_service.create_supplier(db, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 13. GET /suppliers/{supplier_id}/statement ----------


@router.get(
    "/{supplier_id}/statement",
    summary="供应商对账",
    description=(
        "倒推法（spec 4.4）：end=当前应付余额，increase=区间收货，\n"
        "decrease=区间退货+付款，begin=end-increase+decrease。\n"
        "⚠️ begin 是倒推值非历史累计（初始数据应付余额无对应历史单据）。"
    ),
)
def get_statement(
    supplier_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PUR_SUPPLIER))],
    start_date: Annotated[date, Query(description="起始日期 YYYY-MM-DD")],
    end_date: Annotated[date, Query(description="结束日期 YYYY-MM-DD（含当天）")],
) -> dict[str, Any]:
    """GET /suppliers/{id}/statement —— 对账单。"""
    from app.core.error_codes import ErrorCode
    from app.core.exceptions import BusinessError

    if start_date > end_date:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "起始日期不能晚于结束日期")
    result = supplier_service.get_statement(db, supplier_id, start_date, end_date)
    return success(result.model_dump(mode="json"))


# ---------- 12. PUT /suppliers/{supplier_id} ----------


@router.put(
    "/{supplier_id}",
    summary="修改供应商",
    description="⛔不允许改 supplier_code 与 balance_payable；支持停用（status=0），无 DELETE。",
)
def update_supplier(
    supplier_id: int,
    params: SupplierUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PUR_SUPPLIER))],
) -> dict[str, Any]:
    """PUT /suppliers/{id} —— 修改供应商。"""
    result = supplier_service.update_supplier(
        db, supplier_id, params, operator_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))
