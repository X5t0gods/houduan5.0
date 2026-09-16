"""会员路由（阶段 7 交付物）—— 14 个接口。

对应 docs/06 §3.6：
| 1 | GET  | /members                     | member:list   |
| 2 | POST | /members/search              | pos:use       |
| 3 | GET  | /members/{id}                | member:detail |
| 4 | POST | /members                     | member:list   |
| 5 | PUT  | /members/{id}                | member:list   |
| 6 | PUT  | /members/{id}/status         | member:list   |
| 7 | GET  | /members/levels              | member:level  |
| 8 | PUT  | /members/levels/{id}         | member:level  |
| 9 | POST | /members/{id}/recharge       | member:list（幂等）|
| 10| GET  | /members/balance-flows       | member:detail |
| 11| GET  | /members/point-flows         | member:detail |
| 12| POST | /members/{id}/points/adjust  | member:level  |
| 13| GET  | /members/{id}/profile        | member:detail |
| 14| GET  | /members/export              | member:list   |

⚠️ 路由顺序至关重要：静态段 /members/search、/members/levels、/members/export、
   /members/balance-flows、/members/point-flows 必须在 /members/{id} 之前声明，
   否则 "search"/"levels"/"export" 会被当作 member_id 解析（int 转换失败 422）。

⚠️ CP1 实现 #1-#8；#9-#14 由 CP2 补。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm
from app.schemas.member import (
    MemberCreate,
    MemberLevelUpdate,
    MemberSearchRequest,
    MemberStatusUpdate,
    MemberUpdate,
    PointAdjustParams,
    RechargeParams,
)
from app.services import member_service

router = APIRouter(prefix="/members", tags=["会员管理"])

# xlsx 标准 MIME（导出用）
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------- 1. GET /members ----------


@router.get("", summary="会员分页查询")
def list_members(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.MEMBER_LIST))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(description="手机号/姓名/卡号")] = None,
    level_id: Annotated[int | None, Query()] = None,
    status: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    """GET /members —— 会员列表（含 level_name/discount_rate/sleeping_days）。"""
    items, total = member_service.list_members(
        db, page=page, page_size=page_size, keyword=keyword, level_id=level_id, status=status,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 4. POST /members ----------


@router.post("", summary="会员建档")
def create_member(
    params: MemberCreate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LIST))],
) -> dict[str, Any]:
    """POST /members —— 会员建档（M 取号，手机号查重 9001）。"""
    result = member_service.create_member(db, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 2. POST /members/search ----------


@router.post(
    "/search",
    summary="会员快速检索（收银台）",
    description="手机号后4位模糊匹配，最多10条；冻结会员也返回（收银台提示用）。",
)
def search_members(
    params: MemberSearchRequest,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.POS_USE))],
) -> dict[str, Any]:
    """POST /members/search —— 收银台会员检索。"""
    items = member_service.search_members(db, params.phone)
    return success([it.model_dump(mode="json") for it in items])


# ---------- 7. GET /members/levels ----------


@router.get("/levels", summary="会员等级列表")
def list_levels(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.MEMBER_LEVEL))],
) -> dict[str, Any]:
    """GET /members/levels —— 等级列表（按 sort 升序）。"""
    items = member_service.list_levels(db)
    return success([it.model_dump(mode="json") for it in items])


# ---------- 8. PUT /members/levels/{level_id} ----------


@router.put(
    "/levels/{level_id}",
    summary="修改会员等级",
    description="discount_rate ∈ (0,1]；⚠️改折扣率会影响阶段4收银金额。",
)
def update_level(
    level_id: int,
    params: MemberLevelUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LEVEL))],
) -> dict[str, Any]:
    """PUT /members/levels/{id} —— 修改等级。"""
    result = member_service.update_level(db, level_id, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 10. GET /members/balance-flows ----------
# ⚠️ 静态段，必须在 /members/{member_id} 之前


@router.get("/balance-flows", summary="储值流水")
def list_balance_flows(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.MEMBER_DETAIL))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    member_id: Annotated[int | None, Query()] = None,
    flow_type: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /members/balance-flows —— 储值流水（含 member_name/operator_name）。"""
    items, total = member_service.list_balance_flows(
        db, page=page, page_size=page_size, member_id=member_id, flow_type=flow_type,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 11. GET /members/point-flows ----------


@router.get("/point-flows", summary="积分流水")
def list_point_flows(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.MEMBER_DETAIL))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    member_id: Annotated[int | None, Query()] = None,
    flow_type: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /members/point-flows —— 积分流水。"""
    items, total = member_service.list_point_flows(
        db, page=page, page_size=page_size, member_id=member_id, flow_type=flow_type,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 14. GET /members/export ----------


@router.get(
    "/export",
    summary="会员导出",
    description="xlsx 文件流（⛔不包统一响应体）；手机号脱敏 138****8866；导出记操作日志。",
    response_class=Response,
)
def export_members(
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LIST))],
    keyword: Annotated[str | None, Query()] = None,
    level_id: Annotated[int | None, Query()] = None,
) -> Response:
    """GET /members/export —— 导出 xlsx（不走统一响应体）。"""
    content, filename = member_service.export_members(
        db, operator_id=current_user.user_id, keyword=keyword, level_id=level_id,
    )
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------- 3. GET /members/{member_id} ----------


@router.get("/{member_id}", summary="会员详情")
def get_member_detail(
    member_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.MEMBER_DETAIL))],
) -> dict[str, Any]:
    """GET /members/{id} —— 详情（含标签）。不存在 → 9004。"""
    result = member_service.get_member_detail(db, member_id)
    return success(result.model_dump(mode="json"))


# ---------- 5. PUT /members/{member_id} ----------


@router.put(
    "/{member_id}",
    summary="修改会员",
    description=(
        "⛔不允许改 member_no/balance/gift_balance/points/"
        "total_consume/level_id（防篡改）。"
    ),
)
def update_member(
    member_id: int,
    params: MemberUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LIST))],
) -> dict[str, Any]:
    """PUT /members/{id} —— 修改会员。"""
    result = member_service.update_member(db, member_id, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 6. PUT /members/{member_id}/status ----------


@router.put("/{member_id}/status", summary="挂失/冻结会员")
def update_member_status(
    member_id: int,
    params: MemberStatusUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LIST))],
) -> dict[str, Any]:
    """PUT /members/{id}/status —— 状态变更（0冻结/1正常/2挂失）。"""
    member_service.update_member_status(
        db, member_id, params.status, operator_id=current_user.user_id,
    )
    return success(None)


# ---------- 9. POST /members/{member_id}/recharge ----------


@router.post(
    "/{member_id}/recharge",
    summary="会员充值（幂等 + 分开记账）",
    description=(
        "**幂等三步**：同 request_id 重提返回首次结果（不重复加钱）。\n\n"
        "⚠️ 分开记账：写 RECHARGE（本金）+ GIFT（赠送）2 条流水，⛔只第一条带 request_id；\n"
        "赠送比例>mem.gift_ratio_limit(0.50)→9003；金额均0→9002；冻结会员→拒绝"
    ),
)
def recharge(
    member_id: int,
    params: RechargeParams,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LIST))],
) -> dict[str, Any]:
    """POST /members/{id}/recharge —— 会员充值。"""
    result = member_service.recharge(db, member_id, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 12. POST /members/{member_id}/points/adjust ----------


@router.post(
    "/{member_id}/points/adjust",
    summary="积分手工调整",
    description="points 正增负减；reason 必填(2001)；扣减后不能为负(9002)。权限 member:level。",
)
def adjust_points(
    member_id: int,
    params: PointAdjustParams,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.MEMBER_LEVEL))],
) -> dict[str, Any]:
    """POST /members/{id}/points/adjust —— 积分调整。"""
    result = member_service.adjust_points(db, member_id, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 13. GET /members/{member_id}/profile ----------


@router.get(
    "/{member_id}/profile",
    summary="会员消费档案",
    description="avg_price=total_consume/consume_count（count=0→0不除零）；偏好品类TOP5+常购TOP10。",
)
def get_member_profile(
    member_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.MEMBER_DETAIL))],
) -> dict[str, Any]:
    """GET /members/{id}/profile —— 消费档案。"""
    result = member_service.get_member_profile(db, member_id)
    return success(result.model_dump(mode="json"))
