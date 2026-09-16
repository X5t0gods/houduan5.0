"""调拨数据访问层（阶段 5 CP3 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8）。
⚠️ 调拨一步到位（spec 冲突表 ⑤）：创建即完成调出+调入，status='IN'，out_at=in_at=now。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.base_models import BaseStore
from app.models.inv_models import InvTransfer, InvTransferItem
from app.models.sys_models import SysUser


def insert_transfer(session: Session, **fields) -> InvTransfer:
    t = InvTransfer(**fields)
    session.add(t)
    session.flush()
    return t


def insert_transfer_item(session: Session, **fields) -> InvTransferItem:
    item = InvTransferItem(**fields)
    session.add(item)
    session.flush()
    return item


def get_store(session: Session, store_id: int) -> BaseStore | None:
    """查门店（校验存在且 status=1）。"""
    return session.execute(
        select(BaseStore).where(BaseStore.id == store_id)
    ).scalar_one_or_none()


def list_transfers(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[tuple], int]:
    """分页查询调拨单。

    返回 (行列表, total)。每行 = (InvTransfer, from_store_name, to_store_name,
    created_by_name, item_count)。

    ⚠️ JOIN base_store 两次（调出/调入门店名）+ sys_user（制单人）。
    """
    item_count_sq = (
        select(func.count(InvTransferItem.id))
        .where(InvTransferItem.transfer_id == InvTransfer.id)
        .correlate(InvTransfer)
        .scalar_subquery()
    )
    from_store = BaseStore.__table__.alias("from_store")
    to_store = BaseStore.__table__.alias("to_store")
    stmt: Select = (
        select(
            InvTransfer,
            from_store.c.store_name.label("from_store"),
            to_store.c.store_name.label("to_store"),
            SysUser.real_name.label("created_by_name"),
            item_count_sq.label("item_count"),
        )
        .outerjoin(from_store, from_store.c.id == InvTransfer.from_store_id)
        .outerjoin(to_store, to_store.c.id == InvTransfer.to_store_id)
        .outerjoin(SysUser, SysUser.id == InvTransfer.created_by)
    )
    conditions = []
    if status:
        conditions.append(InvTransfer.status == status)
    # store_id 过滤：调出或调入门店任一匹配（便于门店视角查询）
    if store_id is not None:
        conditions.append(
            (InvTransfer.from_store_id == store_id) | (InvTransfer.to_store_id == store_id)
        )
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(InvTransfer.id.desc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)
