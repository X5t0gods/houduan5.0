"""促销路由（阶段 7 交付物）—— 12 个促销接口 + 优惠券。

对应 docs/06 §3.7：
| 1 | GET    | /promotions                | promo:list   |
| 2 | GET    | /promotions/{id}           | promo:list   |
| 3 | POST   | /promotions                | promo:edit   |
| 4 | PUT    | /promotions/{id}           | promo:edit   |
| 5 | PUT    | /promotions/{id}/status    | promo:edit   |
| 6 | DELETE | /promotions/{id}           | promo:edit   |
| 7 | POST   | /promotions/simulate       | promo:edit   |
| 8 | GET    | /promotions/{id}/effect    | promo:list   |
| 9 | GET    | /coupons                   | promo:coupon |
| 10| POST   | /coupons                   | promo:coupon |
| 11| POST   | /promotions/from-rule      | bi:analysis  |
| 12| POST   | /promotions/match          | pos:use      |

⚠️ 路由顺序：/promotions/simulate、/promotions/from-rule、/promotions/match（静态段）
   必须在 /promotions/{id} 之前声明。

⚠️ CP3 实现 #1-#8；#9-#12（优惠券/from-rule/match）由 CP4 补。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, UserContext, require_perm
from app.schemas.promotion import (
    PromotionCreate,
    PromotionStatusUpdate,
    PromotionUpdate,
    SimulateRequest,
)
from app.services import promotion_service

router = APIRouter(prefix="/promotions", tags=["促销管理"])


# ---------- 3. POST /promotions ----------


@router.post(
    "",
    summary="创建促销",
    description=(
        "⚠️ 全场类促销（FULL_REDUCE/FULL_GIFT/COUPON/MEMBER）前端提交空 items，\n"
        "后端用顶层规则字段构造 target_id=0 全场哨兵明细（否则阶段4满减失效，spec 三生死线）。\n"
        "priority 缺省按类型自动填；MEMBER 强制 is_member_only=1；start_time='' → NULL"
    ),
)
def create_promotion(
    params: PromotionCreate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PROMO_EDIT))],
) -> dict[str, Any]:
    """POST /promotions —— 创建促销。"""
    result = promotion_service.create_promotion(db, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


# ---------- 1. GET /promotions ----------


@router.get("", summary="促销列表")
def list_promotions(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PROMO_LIST))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(description="促销名称")] = None,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """GET /promotions —— 促销列表（含 product_count 覆盖商品数）。"""
    items, total = promotion_service.list_promotions(
        db, page=page, page_size=page_size, keyword=keyword, status=status,
    )
    return success({
        "list": [it.model_dump(mode="json") for it in items],
        "total": total, "page": page, "page_size": page_size,
    })


# ---------- 7. POST /promotions/simulate ----------
# ⚠️ 静态段，必须在 /promotions/{promo_id} 之前


@router.post(
    "/simulate",
    summary="促销价格试算",
    description="复用阶段4引擎，⛔不传价格（取 sale_price），排除 MEMBER 类型。",
)
def simulate(
    params: SimulateRequest,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PROMO_EDIT))],
) -> dict[str, Any]:
    """POST /promotions/simulate —— 试算。"""
    result = promotion_service.simulate(db, params)
    return success(result.model_dump(mode="json"))


# ---------- 2. GET /promotions/{promo_id} ----------


@router.get("/{promo_id}", summary="促销详情")
def get_promotion_detail(
    promo_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PROMO_LIST))],
) -> dict[str, Any]:
    """GET /promotions/{id} —— 详情（含 items 全部字段）。"""
    result = promotion_service.get_promotion_detail(db, promo_id)
    return success(result.model_dump(mode="json"))


# ---------- 8. GET /promotions/{promo_id}/effect ----------


@router.get(
    "/{promo_id}/effect",
    summary="促销效果分析",
    description=(
        "trigger_count/discount_total + 命中该促销的销售额/毛利 + compared 对比数组。\n\n"
        "⚠️ compared 是**估算口径**（with=当天命中/without=当天未命中），非严格 A/B 实验"
    ),
)
def get_promotion_effect(
    promo_id: int,
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.PROMO_LIST))],
) -> dict[str, Any]:
    """GET /promotions/{id}/effect —— 效果分析（无数据返空数组不报错）。"""
    result = promotion_service.get_promotion_effect(db, promo_id)
    return success(result)


# ---------- 4. PUT /promotions/{promo_id} ----------


@router.put(
    "/{promo_id}",
    summary="修改促销",
    description="明细全量替换；⛔trigger_count>0 时改规则→2008（只可改名称/有效期/备注）。",
)
def update_promotion(
    promo_id: int,
    params: PromotionUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PROMO_EDIT))],
) -> dict[str, Any]:
    """PUT /promotions/{id} —— 修改促销。"""
    result = promotion_service.update_promotion(
        db, promo_id, params, operator_id=current_user.user_id,
    )
    return success(result.model_dump(mode="json"))


# ---------- 5. PUT /promotions/{promo_id}/status ----------


@router.put("/{promo_id}/status", summary="启停促销")
def update_promotion_status(
    promo_id: int,
    params: PromotionStatusUpdate,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PROMO_EDIT))],
) -> dict[str, Any]:
    """PUT /promotions/{id}/status —— 启停（RUNNING/STOPPED，其他→2001）。"""
    promotion_service.update_promotion_status(
        db, promo_id, params, operator_id=current_user.user_id,
    )
    return success(None)


# ---------- 6. DELETE /promotions/{promo_id} ----------


@router.delete(
    "/{promo_id}",
    summary="删除促销",
    description="⛔trigger_count>0 → 2008（请改为停用）；允许删除时先删明细再删主表。",
)
def delete_promotion(
    promo_id: int,
    db: DbSession,
    current_user: Annotated[UserContext, Depends(require_perm(Perm.PROMO_EDIT))],
) -> dict[str, Any]:
    """DELETE /promotions/{id} —— 删除促销。"""
    promotion_service.delete_promotion(db, promo_id, operator_id=current_user.user_id)
    return success(None)
