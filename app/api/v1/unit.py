"""计量单位路由（阶段 3 交付物）—— 1 个接口。

对应 docs/06 §3.2.12：
| 12 | GET | /units | 登录即可（**不加权限码**） |

⚠️ spec 4.12 明确：登录即可访问，不加权限校验。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.response import success
from app.deps import CurrentUser, DbSession
from app.repositories import unit_repository as repo
from app.schemas.product import Unit

router = APIRouter(prefix="/units", tags=["商品管理"])


@router.get(
    "",
    summary="获取计量单位列表",
    description=(
        "返回 `base_unit` 表全部启用记录，按 sort 升序，共 10 条演示数据"
        "（9 个基本单位 + 1 个辅助单位「箱」）。\n\n"
        "⚠️ **登录即可访问，不加权限码**（spec 4.12）—— 商品建档、库存、销售等多个模块都要用单位。\n"
        "⚠️ `conversion_rate` 是 DECIMAL(12,4)，用 `Rate` 别名序列化为 number"
        "（不用 Money 否则会截断成 2 位）。\n"
        "⚠️ spec 1.2 ⑧：前端 `api/product.ts:66` 声明的是 `Array<Record<string, unknown>>`，"
        "直接返回表行即可（含 unit_type/base_unit_id/conversion_rate/sort/status）。"
    ),
)
def list_units(db: DbSession, _: CurrentUser) -> dict[str, Any]:
    """获取计量单位列表。

    Args:
        db: 数据库会话。
        _: 当前登录用户（依赖 get_current_user，仅用于强制登录校验，不使用其值）。

    Returns:
        统一响应体，data = list[Unit]。
    """
    units = repo.list_all_units(db)
    return success([
        Unit(
            id=u.id,
            unit_name=u.unit_name,
            unit_type=u.unit_type,
            base_unit_id=u.base_unit_id,
            conversion_rate=u.conversion_rate,
            sort=u.sort,
            status=u.status,
            created_at=u.created_at,
            updated_at=u.updated_at,
        ).model_dump(mode="json")
        for u in units
    ])
