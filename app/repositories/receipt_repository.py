"""采购收货单数据访问层（阶段 6 CP4 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.pur_models import PurReceipt, PurReceiptItem


def insert_receipt(session: Session, **fields) -> PurReceipt:
    r = PurReceipt(**fields)
    session.add(r)
    session.flush()
    return r


def insert_receipt_item(session: Session, **fields) -> PurReceiptItem:
    item = PurReceiptItem(**fields)
    session.add(item)
    session.flush()
    return item


def get_receipt_by_id(session: Session, receipt_id: int) -> PurReceipt | None:
    return session.execute(
        select(PurReceipt).where(PurReceipt.id == receipt_id)
    ).scalar_one_or_none()


def update_receipt_item_batch_no(session: Session, item_id: int, batch_no: str) -> None:
    """回写批次号到收货明细（spec 5.7 步骤 7）。"""
    session.execute(
        update(PurReceiptItem).where(PurReceiptItem.id == item_id).values(batch_no=batch_no)
    )
    session.flush()


def list_receipt_items(session: Session, receipt_id: int) -> list[PurReceiptItem]:
    return list(session.execute(
        select(PurReceiptItem).where(PurReceiptItem.receipt_id == receipt_id)
        .order_by(PurReceiptItem.id)
    ).scalars().all())
