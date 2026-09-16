"""报损数据访问层（阶段 5 CP3 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8）。
⚠️ 报损创建即生效（spec 冲突表 ⑥：inv_loss 无 audited_by 字段，无审核入口）。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.inv_models import InvLoss, InvLossItem
from app.models.sys_models import SysUser


def insert_loss(session: Session, **fields) -> InvLoss:
    loss = InvLoss(**fields)
    session.add(loss)
    session.flush()
    return loss


def insert_loss_item(session: Session, **fields) -> InvLossItem:
    item = InvLossItem(**fields)
    session.add(item)
    session.flush()
    return item


def list_losses(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[tuple], int]:
    """分页查询报损单，返回 (行列表, total)。每行 = (InvLoss, created_by_name, item_count)。"""
    # item_count 子查询
    item_count_sq = (
        select(func.count(InvLossItem.id))
        .where(InvLossItem.loss_id == InvLoss.id)
        .correlate(InvLoss)
        .scalar_subquery()
    )
    stmt: Select = (
        select(
            InvLoss,
            SysUser.real_name.label("created_by_name"),
            item_count_sq.label("item_count"),
        )
        .outerjoin(SysUser, SysUser.id == InvLoss.created_by)
    )
    conditions = []
    if status:
        conditions.append(InvLoss.status == status)
    if store_id is not None:
        conditions.append(InvLoss.store_id == store_id)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(InvLoss.id.desc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)
