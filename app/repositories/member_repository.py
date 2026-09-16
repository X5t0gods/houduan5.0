"""会员数据访问层（阶段 7 CP1 交付物）—— 会员 + 等级。

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
⚠️ JOIN mem_level 取 level_name + discount_rate（收银台会员折扣用，spec 5.1/5.2）。
"""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.mem_models import MemLevel, MemMember, MemMemberTag, MemTag

# ---------- 会员查询 ----------


def _member_select() -> Select:
    """会员 + 等级名 + 折扣率的 JOIN。"""
    return (
        select(
            MemMember,
            MemLevel.level_name.label("level_name"),
            MemLevel.discount_rate.label("discount_rate"),
        )
        .outerjoin(MemLevel, MemLevel.id == MemMember.level_id)
    )


def list_members(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    level_id: int | None = None,
    status: int | None = None,
) -> tuple[list[tuple], int]:
    """分页查询会员（keyword 匹配 手机号/姓名/卡号）。

    返回 ([(MemMember, level_name, discount_rate), ...], total)。
    """
    stmt = _member_select()
    conditions = []
    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(or_(
            MemMember.phone.like(kw),
            MemMember.real_name.like(kw),
            MemMember.member_no.like(kw),
        ))
    if level_id is not None:
        conditions.append(MemMember.level_id == level_id)
    if status is not None:
        conditions.append(MemMember.status == status)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(MemMember.id.asc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)


def search_members(session: Session, phone: str, limit: int = 10) -> list[tuple]:
    """收银台快速检索（手机号后4位模糊匹配，最多 limit 条，spec 5.2）。

    ⚠️ LIKE '%xxxx%' 用不上索引（idx_phone_suffix 是普通索引，前缀通配失效）。
       数据量小无妨；将来量大需改存"手机号后4位"冗余列。
    ⚠️ 冻结会员也返回（收银台需提示"已冻结"），但阶段4结算时拒绝。
    """
    kw = f"%{phone.strip()}%"
    return list(session.execute(
        _member_select()
        .where(MemMember.phone.like(kw))
        .order_by(MemMember.id.asc())
        .limit(limit)
    ).all())


def get_member_with_level(session: Session, member_id: int) -> tuple | None:
    """按 ID 查会员（含 level_name + discount_rate）。"""
    return session.execute(
        _member_select().where(MemMember.id == member_id)
    ).fetchone()


def get_member_by_id(session: Session, member_id: int) -> MemMember | None:
    return session.execute(
        select(MemMember).where(MemMember.id == member_id)
    ).scalar_one_or_none()


def get_member_by_phone(session: Session, phone: str) -> MemMember | None:
    return session.execute(
        select(MemMember).where(MemMember.phone == phone)
    ).scalar_one_or_none()


def insert_member(session: Session, **fields) -> MemMember:
    m = MemMember(**fields)
    session.add(m)
    session.flush()
    return m


def update_member(session: Session, member_id: int, values: dict) -> None:
    if not values:
        return
    session.execute(update(MemMember).where(MemMember.id == member_id).values(**values))
    session.flush()


def get_member_tags(session: Session, member_id: int) -> list[str]:
    """读取会员标签名（只读，spec 2.3：本阶段不打标）。"""
    rows = session.execute(
        select(MemTag.tag_name)
        .join(MemMemberTag, MemMemberTag.tag_id == MemTag.id)
        .where(MemMemberTag.member_id == member_id)
    ).scalars().all()
    return list(rows)


# ---------- 会员等级 ----------


def list_levels(session: Session) -> list[MemLevel]:
    """等级列表（按 sort 升序，spec 5.7）。"""
    return list(session.execute(
        select(MemLevel).order_by(MemLevel.sort.asc(), MemLevel.id.asc())
    ).scalars().all())


def get_level_by_id(session: Session, level_id: int) -> MemLevel | None:
    return session.execute(
        select(MemLevel).where(MemLevel.id == level_id)
    ).scalar_one_or_none()


def update_level(session: Session, level_id: int, values: dict) -> None:
    if not values:
        return
    session.execute(update(MemLevel).where(MemLevel.id == level_id).values(**values))
    session.flush()


def get_lowest_level(session: Session) -> MemLevel | None:
    """取最低等级（新会员建档用，level_value 最小）。"""
    return session.execute(
        select(MemLevel).order_by(MemLevel.level_value.asc()).limit(1)
    ).scalar_one_or_none()
