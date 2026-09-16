"""库存流水数据访问层（阶段 5 CP1 交付物）。

⚠️ 只处理**查询**（列表 + 导出）；流水的写入在 inventory_write_repository（阶段 4 已有）。
⚠️ JOIN prd_product（product_name）+ sys_user（operator_name）+ inv_batch（batch_no）。
⚠️ 按 created_at 倒序（最近的流水在前）。
"""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models.inv_models import InvBatch, InvStockFlow
from app.models.prd_models import PrdProduct
from app.models.sys_models import SysUser


def _build_flow_select() -> Select:
    """构造流水查询 JOIN（流水 + 商品名 + 操作人名 + 批次号）。"""
    return (
        select(
            InvStockFlow,
            PrdProduct.product_name,
            SysUser.real_name.label("operator_name"),
            InvBatch.batch_no,
        )
        .outerjoin(PrdProduct, PrdProduct.id == InvStockFlow.product_id)
        .outerjoin(SysUser, SysUser.id == InvStockFlow.operator)
        .outerjoin(InvBatch, InvBatch.id == InvStockFlow.batch_id)
    )


def list_flows(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    flow_type: str | None = None,
    store_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[dict], int]:
    """分页查询库存流水（spec 5.2）。

    Returns:
        (流水字典列表, total)。按 created_at 倒序。
    """
    conditions = _build_conditions(keyword, flow_type, store_id, start_date, end_date)

    base_stmt = _build_flow_select()
    if conditions:
        base_stmt = base_stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(base_stmt.subquery())
    ).scalar_one()

    rows = session.execute(
        base_stmt.order_by(InvStockFlow.created_at.desc(), InvStockFlow.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    return [_row_to_flow_dict(r) for r in rows], int(total)


def list_flows_for_export(
    session: Session,
    *,
    keyword: str | None = None,
    flow_type: str | None = None,
    store_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 10001,
) -> tuple[list[dict], int]:
    """导出用流水查询（spec 5.3）。

    ⚠️ 返回 (行列表, 满足条件的总数)。总数用于判断是否超上限（> 10000 → 2001）。
    ⚠️ 实际只取 limit 行（防止内存爆掉），但 total 是全量计数。
    """
    conditions = _build_conditions(keyword, flow_type, store_id, start_date, end_date)

    base_stmt = _build_flow_select()
    if conditions:
        base_stmt = base_stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(base_stmt.subquery())
    ).scalar_one()

    rows = session.execute(
        base_stmt.order_by(InvStockFlow.created_at.desc(), InvStockFlow.id.desc())
        .limit(limit)
    ).all()

    return [_row_to_flow_dict(r) for r in rows], int(total)


def _build_conditions(
    keyword: str | None,
    flow_type: str | None,
    store_id: int | None,
    start_date: str | None,
    end_date: str | None,
) -> list:
    conditions = []
    if keyword:
        conditions.append(PrdProduct.product_name.like(f"%{keyword.strip()}%"))
    if flow_type:
        conditions.append(InvStockFlow.flow_type == flow_type)
    if store_id is not None:
        conditions.append(InvStockFlow.store_id == store_id)
    if start_date:
        conditions.append(InvStockFlow.created_at >= f"{start_date} 00:00:00")
    if end_date:
        conditions.append(InvStockFlow.created_at <= f"{end_date} 23:59:59")
    return conditions


def _row_to_flow_dict(row: tuple) -> dict:
    """把 JOIN 行转成 StockFlow 字典。"""
    flow, product_name, operator_name, batch_no = row
    return {
        "id": flow.id,
        "store_id": flow.store_id,
        "product_id": flow.product_id,
        "product_name": product_name,
        "flow_type": flow.flow_type,
        "direction": flow.direction,
        "quantity": flow.quantity,
        "before_qty": flow.before_qty,
        "after_qty": flow.after_qty,
        "unit_cost": flow.unit_cost,
        "amount": flow.amount,
        "source_type": flow.source_type,
        "source_no": flow.source_no,
        "batch_no": batch_no,
        "operator_name": operator_name,
        "remark": flow.remark,
        "created_at": flow.created_at,
    }


# 让 lint 知道 or_ 被间接使用
_ = or_
