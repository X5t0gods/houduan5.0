"""销售数据访问层（阶段 4 CP3+CP4 交付物）。

包含：销售单、明细、支付、退货、挂单、班次的数据访问。
⚠️ 分层约束：只 flush 不 commit（决策 8）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.mem_models import MemBalanceFlow, MemMember, MemPointFlow
from app.models.prd_models import PrdProduct
from app.models.sal_models import (
    SalHold,
    SalReturn,
    SalReturnItem,
    SalSession,
    SalTrade,
    SalTradeItem,
    SalTradePayment,
)
from app.models.sys_models import SysUser

# ---------- 销售单 ----------


def find_trade_by_request_id(session: Session, request_id: str) -> SalTrade | None:
    """按 request_id 查销售单（幂等的第一步）。"""
    return session.execute(
        select(SalTrade).where(SalTrade.request_id == request_id)
    ).scalar_one_or_none()


def get_trade_by_id(session: Session, trade_id: int) -> SalTrade | None:
    return session.execute(
        select(SalTrade).where(SalTrade.id == trade_id)
    ).scalar_one_or_none()


def insert_trade(session: Session, **fields) -> SalTrade:
    trade = SalTrade(**fields)
    session.add(trade)
    session.flush()
    return trade


def insert_trade_item(session: Session, **fields) -> SalTradeItem:
    item = SalTradeItem(**fields)
    session.add(item)
    session.flush()
    return item


def insert_trade_payment(session: Session, **fields) -> SalTradePayment:
    pay = SalTradePayment(**fields)
    session.add(pay)
    session.flush()
    return pay


def list_trade_items(session: Session, trade_id: int) -> list[SalTradeItem]:
    return list(session.execute(
        select(SalTradeItem).where(SalTradeItem.trade_id == trade_id).order_by(SalTradeItem.id)
    ).scalars().all())


def list_trade_payments(session: Session, trade_id: int) -> list[SalTradePayment]:
    return list(session.execute(
        select(SalTradePayment).where(SalTradePayment.trade_id == trade_id)
    ).scalars().all())


def list_trades(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[tuple], int]:
    """分页查询销售单，返回列表 + total。

    返回列表每项：(SalTrade, cashier_name, member_name, member_no)。
    """
    stmt: Select = (
        select(
            SalTrade,
            SysUser.real_name.label("cashier_name"),
            MemMember.real_name.label("member_name"),
            MemMember.member_no.label("member_no"),
        )
        .outerjoin(SysUser, SysUser.id == SalTrade.cashier_id)
        .outerjoin(MemMember, MemMember.id == SalTrade.member_id)
    )
    conditions = []
    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(or_(
            SalTrade.trade_no.like(kw),
            MemMember.real_name.like(kw),
            MemMember.phone.like(kw),
        ))
    if start_date:
        conditions.append(SalTrade.trade_time >= f"{start_date} 00:00:00")
    if end_date:
        conditions.append(SalTrade.trade_time <= f"{end_date} 23:59:59")
    if status:
        conditions.append(SalTrade.status == status)
    if store_id is not None:
        conditions.append(SalTrade.store_id == store_id)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    stmt = stmt.order_by(SalTrade.id.desc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)


def update_trade_status(session: Session, trade_id: int, status: str) -> None:
    session.execute(update(SalTrade).where(SalTrade.id == trade_id).values(status=status))
    session.flush()


def increment_print_count(session: Session, trade_id: int) -> int:
    """print_count +1 并返回新值（小票重打）。"""
    session.execute(
        update(SalTrade).where(SalTrade.id == trade_id)
        .values(print_count=SalTrade.print_count + 1)
    )
    session.flush()
    return session.execute(
        select(SalTrade.print_count).where(SalTrade.id == trade_id)
    ).scalar_one()


# ---------- 退货 ----------


def insert_return(session: Session, **fields) -> SalReturn:
    r = SalReturn(**fields)
    session.add(r)
    session.flush()
    return r


def insert_return_item(session: Session, **fields) -> SalReturnItem:
    ri = SalReturnItem(**fields)
    session.add(ri)
    session.flush()
    return ri


def sum_returned_quantity(session: Session, trade_id: int, product_id: int) -> Decimal:
    """统计某销售单某商品**已退数量**（防重复退货，spec 6.6）。"""
    result = session.execute(
        select(func.coalesce(func.sum(SalReturnItem.quantity), Decimal("0")))
        .join(SalReturn, SalReturn.id == SalReturnItem.return_id)
        .where(
            SalReturn.source_trade_id == trade_id,
            SalReturnItem.product_id == product_id,
            SalReturn.refund_status != "FAILED",  # 失败退款不算
        )
    ).scalar_one()
    return Decimal(result or 0)


# ---------- 挂单 ----------


def insert_hold(session: Session, **fields) -> SalHold:
    h = SalHold(**fields)
    session.add(h)
    session.flush()
    return h


def get_hold_by_no(session: Session, hold_no: str) -> SalHold | None:
    return session.execute(
        select(SalHold).where(SalHold.hold_no == hold_no)
    ).scalar_one_or_none()


def list_holds(session: Session, pos_id: int, store_id: int | None = None) -> list[SalHold]:
    stmt = select(SalHold).where(
        SalHold.pos_id == pos_id,
        SalHold.status == "HOLDING",
    )
    if store_id is not None:
        stmt = stmt.where(SalHold.store_id == store_id)
    return list(session.execute(stmt.order_by(SalHold.id.desc())).scalars().all())


def update_hold_status(session: Session, hold_id: int, status: str) -> None:
    session.execute(update(SalHold).where(SalHold.id == hold_id).values(status=status))
    session.flush()


# ---------- 班次 ----------


def get_current_session(session: Session, pos_id: int) -> SalSession | None:
    """取某收银台当前 OPEN 状态的班次。"""
    return session.execute(
        select(SalSession).where(
            SalSession.pos_id == pos_id,
            SalSession.status == "OPEN",
        ).order_by(SalSession.id.desc()).limit(1)
    ).scalar_one_or_none()


def get_session_by_id(session: Session, session_id: int) -> SalSession | None:
    return session.execute(
        select(SalSession).where(SalSession.id == session_id)
    ).scalar_one_or_none()


def insert_session(session: Session, **fields) -> SalSession:
    s = SalSession(**fields)
    session.add(s)
    session.flush()
    return s


def update_session_fields(session: Session, session_id: int, values: dict[str, Any]) -> None:
    if not values:
        return
    session.execute(update(SalSession).where(SalSession.id == session_id).values(**values))
    session.flush()


def increment_session_sale(
    session: Session,
    session_id: int,
    *,
    amount: Decimal,
    pay_method_amounts: dict[str, Decimal],
) -> None:
    """累加班次的销售金额、笔数、各支付方式金额（结算时调用）。

    ⚠️ 用条件 UPDATE 保证并发安全。
    """
    values: dict[str, Any] = {
        "sale_amount": SalSession.sale_amount + amount,
        "sale_count": SalSession.sale_count + 1,
    }
    # 支付方式 → 班次字段映射
    field_map = {
        "CASH": "cash_amount",
        "WECHAT": "wechat_amount",
        "ALIPAY": "alipay_amount",
        "CARD": "card_amount",
        "BALANCE": "balance_amount",
    }
    for method, amt in pay_method_amounts.items():
        col = field_map.get(method)
        if col:
            values[col] = getattr(SalSession, col) + amt
    session.execute(update(SalSession).where(SalSession.id == session_id).values(**values))
    session.flush()


# ---------- 会员处理（结算与退货都用） ----------


def get_member_for_update(session: Session, member_id: int) -> MemMember | None:
    """读取会员（用 SELECT ... FOR UPDATE 加行锁，防止并发扣款超支）。"""
    return session.execute(
        select(MemMember).where(MemMember.id == member_id).with_for_update()
    ).scalar_one_or_none()


def update_member_balance(
    session: Session, member_id: int, *,
    balance_delta: Decimal, gift_delta: Decimal,
    points_delta: int = 0,
    total_consume_delta: Decimal = Decimal("0"),
    consume_count_delta: int = 0,
    last_consume_at: datetime | None = None,
    level_id: int | None = None,
) -> None:
    """更新会员余额/积分/统计（用增量表达式，并发安全）。"""
    values: dict[str, Any] = {
        "balance": MemMember.balance + balance_delta,
        "gift_balance": MemMember.gift_balance + gift_delta,
    }
    if points_delta:
        values["points"] = MemMember.points + points_delta
    if total_consume_delta:
        values["total_consume"] = MemMember.total_consume + total_consume_delta
    if consume_count_delta:
        values["consume_count"] = MemMember.consume_count + consume_count_delta
    if last_consume_at is not None:
        values["last_consume_at"] = last_consume_at
    if level_id is not None:
        values["level_id"] = level_id
    session.execute(update(MemMember).where(MemMember.id == member_id).values(**values))
    session.flush()


def insert_balance_flow(session: Session, **fields) -> MemBalanceFlow:
    f = MemBalanceFlow(**fields)
    session.add(f)
    session.flush()
    return f


def insert_point_flow(session: Session, **fields) -> MemPointFlow:
    f = MemPointFlow(**fields)
    session.add(f)
    session.flush()
    return f


# ---------- 商品与用户辅助查询 ----------


def get_product(session: Session, product_id: int) -> PrdProduct | None:
    return session.execute(
        select(PrdProduct).where(PrdProduct.id == product_id)
    ).scalar_one_or_none()


def get_user_real_name(session: Session, user_id: int) -> str | None:
    return session.execute(
        select(SysUser.real_name).where(SysUser.id == user_id)
    ).scalar_one_or_none()


# 让 lint 知道 and_ 是被间接使用的
_ = and_
