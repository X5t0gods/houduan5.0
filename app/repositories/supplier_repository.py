"""供应商数据访问层（阶段 6 CP2 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
⚠️ 应付余额 balance_payable 只能由收货(+)/退货(-)/付款(-) 变动，用增量表达式（并发安全）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.pur_models import PurPayment, PurReceipt, PurReturn, PurSupplier

# ---------- 供应商 CRUD ----------


def list_suppliers(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: int | None = None,
) -> tuple[list[PurSupplier], int]:
    """分页查询供应商（keyword 匹配名称/编码/联系人）。"""
    stmt: Select = select(PurSupplier)
    conditions = []
    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(or_(
            PurSupplier.supplier_name.like(kw),
            PurSupplier.supplier_code.like(kw),
            PurSupplier.contact_name.like(kw),
        ))
    if status is not None:
        conditions.append(PurSupplier.status == status)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(PurSupplier.id.asc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).scalars().all()), int(total)


def get_supplier_by_id(session: Session, supplier_id: int) -> PurSupplier | None:
    return session.execute(
        select(PurSupplier).where(PurSupplier.id == supplier_id)
    ).scalar_one_or_none()


def get_supplier_by_code(session: Session, supplier_code: str) -> PurSupplier | None:
    return session.execute(
        select(PurSupplier).where(PurSupplier.supplier_code == supplier_code)
    ).scalar_one_or_none()


def insert_supplier(session: Session, **fields) -> PurSupplier:
    s = PurSupplier(**fields)
    session.add(s)
    session.flush()
    return s


def update_supplier(session: Session, supplier_id: int, values: dict) -> None:
    if not values:
        return
    session.execute(update(PurSupplier).where(PurSupplier.id == supplier_id).values(**values))
    session.flush()


def adjust_balance_payable(session: Session, supplier_id: int, delta: Decimal) -> None:
    """调整应付余额（增量表达式，允许为负 —— spec 5.9/5.14）。

    ⚠️ delta 正数=增加应付（收货）；负数=减少应付（退货/付款）。
    ⛔ 不做"余额不足"校验（供应商可能预付/多付，允许负应付）。
    """
    session.execute(
        update(PurSupplier)
        .where(PurSupplier.id == supplier_id)
        .values(balance_payable=PurSupplier.balance_payable + delta)
    )
    session.flush()


# ---------- 付款 ----------


def insert_payment(session: Session, **fields) -> PurPayment:
    p = PurPayment(**fields)
    session.add(p)
    session.flush()
    return p


# ---------- 对账聚合查询（spec 4.4 倒推法） ----------


def sum_receipts_in_range(
    session: Session, supplier_id: int, start: date, end: date
) -> Decimal:
    """区间内收货金额合计（increase）。

    ⚠️ received_at 是 DATETIME，end 要**含当天**（用 < end+1天）。
    ⚠️ 只算未作废的收货单（status != 'VOID'）。
    """
    from datetime import timedelta

    end_exclusive = end + timedelta(days=1)
    result = session.execute(
        select(func.coalesce(func.sum(PurReceipt.total_amount), Decimal("0")))
        .where(
            PurReceipt.supplier_id == supplier_id,
            PurReceipt.status != "VOID",
            PurReceipt.received_at >= start,
            PurReceipt.received_at < end_exclusive,
        )
    ).scalar_one()
    return Decimal(result or 0)


def sum_returns_in_range(
    session: Session, supplier_id: int, start: date, end: date
) -> Decimal:
    """区间内采购退货金额合计（decrease 的一部分，冲减应付）。"""
    from datetime import timedelta

    end_exclusive = end + timedelta(days=1)
    result = session.execute(
        select(func.coalesce(func.sum(PurReturn.total_amount), Decimal("0")))
        .where(
            PurReturn.supplier_id == supplier_id,
            PurReturn.created_at >= start,
            PurReturn.created_at < end_exclusive,
        )
    ).scalar_one()
    return Decimal(result or 0)


def sum_payments_in_range(
    session: Session, supplier_id: int, start: date, end: date
) -> Decimal:
    """区间内付款金额合计（decrease 的一部分）。

    ⚠️ pay_date 是 DATE，直接与 [start, end] 闭区间比较即可（含当天）。
    """
    result = session.execute(
        select(func.coalesce(func.sum(PurPayment.amount), Decimal("0")))
        .where(
            PurPayment.supplier_id == supplier_id,
            PurPayment.pay_date >= start,
            PurPayment.pay_date <= end,
        )
    ).scalar_one()
    return Decimal(result or 0)
