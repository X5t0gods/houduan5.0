"""会员流水数据访问层（阶段 7 交付物）—— 储值流水 + 积分流水。

⚠️ 分层约束：只 flush 不 commit（决策 8）。
⚠️ 充值幂等（spec 5.9）：find_balance_flow_by_request_id 是幂等第一步。
⚠️ mem_balance_flow.request_id + uk_request_id 阶段1已补齐，⛔不再改表。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.mem_models import MemBalanceFlow, MemMember, MemPointFlow
from app.models.sys_models import SysUser

# ---------- 储值流水 ----------


def find_balance_flow_by_request_id(session: Session, request_id: str) -> MemBalanceFlow | None:
    """按 request_id 查储值流水（充值幂等第一步，spec 5.9）。"""
    return session.execute(
        select(MemBalanceFlow).where(MemBalanceFlow.request_id == request_id)
    ).scalar_one_or_none()


def insert_balance_flow(session: Session, **fields) -> MemBalanceFlow:
    f = MemBalanceFlow(**fields)
    session.add(f)
    session.flush()
    return f


def _balance_flow_select() -> Select:
    return (
        select(
            MemBalanceFlow,
            MemMember.real_name.label("member_name"),
            MemMember.member_no.label("member_no"),
            SysUser.real_name.label("operator_name"),
        )
        .outerjoin(MemMember, MemMember.id == MemBalanceFlow.member_id)
        .outerjoin(SysUser, SysUser.id == MemBalanceFlow.operator)
    )


def list_balance_flows(
    session: Session,
    *,
    page: int,
    page_size: int,
    member_id: int | None = None,
    flow_type: str | None = None,
) -> tuple[list[tuple], int]:
    """分页查询储值流水（按 created_at 倒序，含 member_name/operator_name）。"""
    stmt = _balance_flow_select()
    conditions = []
    if member_id is not None:
        conditions.append(MemBalanceFlow.member_id == member_id)
    if flow_type:
        conditions.append(MemBalanceFlow.flow_type == flow_type)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(MemBalanceFlow.created_at.desc(), MemBalanceFlow.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)


# ---------- 积分流水 ----------


def insert_point_flow(session: Session, **fields) -> MemPointFlow:
    f = MemPointFlow(**fields)
    session.add(f)
    session.flush()
    return f


def _point_flow_select() -> Select:
    return (
        select(
            MemPointFlow,
            MemMember.real_name.label("member_name"),
            MemMember.member_no.label("member_no"),
            SysUser.real_name.label("operator_name"),
        )
        .outerjoin(MemMember, MemMember.id == MemPointFlow.member_id)
        .outerjoin(SysUser, SysUser.id == MemPointFlow.operator)
    )


def list_point_flows(
    session: Session,
    *,
    page: int,
    page_size: int,
    member_id: int | None = None,
    flow_type: str | None = None,
) -> tuple[list[tuple], int]:
    """分页查询积分流水（按 created_at 倒序）。"""
    stmt = _point_flow_select()
    conditions = []
    if member_id is not None:
        conditions.append(MemPointFlow.member_id == member_id)
    if flow_type:
        conditions.append(MemPointFlow.flow_type == flow_type)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(MemPointFlow.created_at.desc(), MemPointFlow.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)
