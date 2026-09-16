"""采购退货单数据访问层（阶段 6 CP4 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8）。
⚠️ 采购退货创建即生效（spec 冲突表 ⑧：pur_return.status 默认 FINISHED，无审核字段）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pur_models import PurReturn, PurReturnItem


def insert_return(session: Session, **fields) -> PurReturn:
    r = PurReturn(**fields)
    session.add(r)
    session.flush()
    return r


def insert_return_item(session: Session, **fields) -> PurReturnItem:
    item = PurReturnItem(**fields)
    session.add(item)
    session.flush()
    return item


def get_return_by_id(session: Session, return_id: int) -> PurReturn | None:
    return session.execute(
        select(PurReturn).where(PurReturn.id == return_id)
    ).scalar_one_or_none()
