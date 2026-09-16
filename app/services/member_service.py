"""会员业务编排（阶段 7 交付物）。

CP1：查询/检索/详情/建档/修改/状态/等级
CP2：充值（幂等+分开记账）/流水/积分调整/消费档案/导出

⚠️ 会员账要清：本金 balance 与赠送 gift_balance **分开记账**（spec 5.9）。
⚠️ discount_rate 是 Rate（4位），被阶段4收银用作会员折扣率，改错影响收银金额。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.repositories import member_flow_repository as flow_repo
from app.repositories import member_repository as repo
from app.repositories.auth_repository import write_operation_log
from app.schemas.member import (
    Member,
    MemberCreate,
    MemberLevel,
    MemberLevelUpdate,
    MemberUpdate,
)
from app.utils.money import ZERO, money

logger = get_logger(__name__)

# 会员状态
STATUS_FROZEN = 0
STATUS_NORMAL = 1
STATUS_LOST = 2
_STATUS_ACTION = {0: "冻结会员", 1: "解冻会员", 2: "挂失会员"}


def _today() -> date:
    return datetime.now(ZoneInfo(settings.TZ)).date()


def _sleeping_days(last_consume_at: datetime | None) -> int | None:
    """距今未消费天数（spec 5.1）。last_consume_at 为 NULL → None（前端显示"-"）。"""
    if last_consume_at is None:
        return None
    return (_today() - last_consume_at.date()).days


def _row_to_member(row: tuple, tags: list[str] | None = None) -> Member:
    """(MemMember, level_name, discount_rate) → Member Schema。"""
    m, level_name, discount_rate = row
    return Member(
        id=m.id,
        member_no=m.member_no,
        phone=m.phone,
        real_name=m.real_name,
        gender=m.gender,
        birthday=m.birthday,
        level_id=m.level_id,
        level_name=level_name,
        discount_rate=discount_rate,
        balance=m.balance,
        gift_balance=m.gift_balance,
        points=m.points,
        total_consume=m.total_consume,
        consume_count=m.consume_count,
        last_consume_at=m.last_consume_at,
        source=m.source,
        status=m.status,
        tags=tags,
        sleeping_days=_sleeping_days(m.last_consume_at),
    )


# ---------- 5.1 GET /members ----------


def list_members(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    level_id: int | None = None,
    status: int | None = None,
) -> tuple[list[Member], int]:
    """会员分页查询（spec 5.1）。"""
    rows, total = repo.list_members(
        session, page=page, page_size=page_size,
        keyword=keyword, level_id=level_id, status=status,
    )
    return [_row_to_member(r) for r in rows], total


# ---------- 5.2 POST /members/search ----------


def search_members(session: Session, phone: str) -> list[Member]:
    """收银台快速检索（手机号后4位，最多10条，spec 5.2）。

    ⚠️ 冻结会员也返回（收银台需提示），阶段4结算时拒绝。
    """
    rows = repo.search_members(session, phone, limit=10)
    return [_row_to_member(r) for r in rows]


# ---------- 5.3 GET /members/{id} ----------


def get_member_detail(session: Session, member_id: int) -> Member:
    """会员详情（含标签，spec 5.3）。不存在 → 9004。"""
    row = repo.get_member_with_level(session, member_id)
    if row is None:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, f"会员 id={member_id} 不存在")
    tags = repo.get_member_tags(session, member_id)
    return _row_to_member(row, tags=tags)


# ---------- 5.4 POST /members ----------


def create_member(
    session: Session, params: MemberCreate, *, operator_id: int
) -> Member:
    """会员建档（spec 5.4）。

    校验：phone 必填(2001) → 手机号已存在(9001) → init_balance>0 写 RECHARGE 流水。
    ⚠️ member_no 用 M 取号（今天日期段，与初始2025数据不撞号，无需初始化脚本）。
    ⚠️ 新会员恒最低等级（init_balance 是充值不是消费，不计入 total_consume）。
    """
    phone = (params.phone or "").strip()
    if not phone:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "手机号不能为空")
    if repo.get_member_by_phone(session, phone) is not None:
        raise BusinessError(ErrorCode.PHONE_ALREADY_MEMBER, f"手机号 {phone} 已是会员")

    # 最低等级
    lowest = repo.get_lowest_level(session)
    level_id = lowest.id if lowest else 1

    seq = NoSeqService(session)
    member_no = seq.next_no("M")

    init_balance = money(params.init_balance) if params.init_balance else ZERO

    member = repo.insert_member(
        session,
        member_no=member_no,
        phone=phone,
        real_name=params.real_name,
        gender=params.gender,
        birthday=params.birthday,
        level_id=level_id,
        balance=init_balance,       # 建档充值进本金
        gift_balance=ZERO,
        points=0,
        total_consume=ZERO,         # ⛔ 充值不算消费
        consume_count=0,
        source=params.source,
        status=STATUS_NORMAL,
        remark=params.remark,
    )

    # init_balance > 0 → 写 RECHARGE 流水
    if init_balance > ZERO:
        flow_repo.insert_balance_flow(
            session,
            member_id=member.id,
            flow_type="RECHARGE",
            amount=init_balance,
            gift_amount=ZERO,
            direction=1,
            before_balance=ZERO,
            after_balance=init_balance,
            before_gift=ZERO,
            after_gift=ZERO,
            pay_method=None,
            source_no=member_no,
            operator=operator_id,
            remark="建档充值",
        )

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action="新增会员",
        target_type="mem_member", target_id=member.id,
        after_value={"member_no": member_no, "phone": phone},
        result=1,
    )
    session.commit()
    logger.info(f"新增会员：member_no={member_no}, phone={phone}")
    return get_member_detail(session, member.id)


# ---------- 5.5 PUT /members/{id} ----------


def update_member(
    session: Session, member_id: int, params: MemberUpdate, *, operator_id: int
) -> Member:
    """修改会员（spec 5.5）。

    ⛔ 不允许改 member_no/balance/gift_balance/points/total_consume/consume_count/level_id
       （schema 里就没这些字段，防篡改）。
    ⚠️ 改手机号要查重（9001）。
    """
    member = repo.get_member_by_id(session, member_id)
    if member is None:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, f"会员 id={member_id} 不存在")

    # 改手机号查重
    if params.phone is not None and params.phone != member.phone:
        existing = repo.get_member_by_phone(session, params.phone)
        if existing is not None and existing.id != member_id:
            raise BusinessError(ErrorCode.PHONE_ALREADY_MEMBER, f"手机号 {params.phone} 已被占用")

    values: dict = {}
    for field_name, new_val in [
        ("real_name", params.real_name),
        ("gender", params.gender),
        ("birthday", params.birthday),
        ("phone", params.phone),
        ("remark", params.remark),
    ]:
        if new_val is not None:
            values[field_name] = new_val

    repo.update_member(session, member_id, values)
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action="修改会员",
        target_type="mem_member", target_id=member_id,
        after_value={"changed_fields": list(values.keys())},
        result=1,
    )
    session.commit()
    return get_member_detail(session, member_id)


# ---------- 5.6 PUT /members/{id}/status ----------


def update_member_status(
    session: Session, member_id: int, status: int, *, operator_id: int
) -> None:
    """挂失/冻结（spec 5.6）。status ∈ {0冻结,1正常,2挂失}。"""
    member = repo.get_member_by_id(session, member_id)
    if member is None:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, f"会员 id={member_id} 不存在")
    if status not in (STATUS_FROZEN, STATUS_NORMAL, STATUS_LOST):
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"非法会员状态：{status}")

    repo.update_member(session, member_id, {"status": status})
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action=_STATUS_ACTION.get(status, "改会员状态"),
        target_type="mem_member", target_id=member_id,
        after_value={"status": status},
        result=1,
    )
    session.commit()
    logger.info(f"会员状态变更：id={member_id}, status={status}")


# ---------- 5.7 GET /members/levels + PUT /members/levels/{id} ----------


def list_levels(session: Session) -> list[MemberLevel]:
    """等级列表（按 sort 升序）。"""
    levels = repo.list_levels(session)
    return [
        MemberLevel(
            id=lv.id,
            level_name=lv.level_name,
            level_value=lv.level_value,
            discount_rate=lv.discount_rate,
            upgrade_condition=lv.upgrade_condition,
            condition_value=lv.condition_value,
            benefit_desc=lv.benefit_desc,
            status=lv.status,
        )
        for lv in levels
    ]


def update_level(
    session: Session, level_id: int, params: MemberLevelUpdate, *, operator_id: int
) -> MemberLevel:
    """修改等级（spec 5.7）。

    ⚠️ discount_rate ∈ (0,1]（>1 或 ≤0 都拒绝）；upgrade_condition ∈ {CONSUME,POINTS}。
    ⚠️ 改 discount_rate 会影响阶段4收银金额。
    """
    level = repo.get_level_by_id(session, level_id)
    if level is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"等级 id={level_id} 不存在")

    if params.discount_rate is not None and not (
        Decimal("0") < params.discount_rate <= Decimal("1")
    ):
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            f"折扣率必须在 (0,1] 之间，当前 {params.discount_rate}",
        )
    if (
        params.upgrade_condition is not None
        and params.upgrade_condition not in ("CONSUME", "POINTS")
    ):
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING, f"非法升级依据：{params.upgrade_condition}"
        )

    values: dict = {}
    for field_name, new_val in [
        ("level_name", params.level_name),
        ("discount_rate", params.discount_rate),
        ("upgrade_condition", params.upgrade_condition),
        ("condition_value", params.condition_value),
        ("benefit_desc", params.benefit_desc),
        ("sort", params.sort),
        ("status", params.status),
    ]:
        if new_val is not None:
            values[field_name] = new_val

    repo.update_level(session, level_id, values)
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action="修改会员等级",
        target_type="mem_level", target_id=level_id,
        after_value={"changed_fields": list(values.keys())},
        result=1,
    )
    session.commit()
    updated = repo.get_level_by_id(session, level_id)
    return MemberLevel(
        id=updated.id,
        level_name=updated.level_name,
        level_value=updated.level_value,
        discount_rate=updated.discount_rate,
        upgrade_condition=updated.upgrade_condition,
        condition_value=updated.condition_value,
        benefit_desc=updated.benefit_desc,
        status=updated.status,
    )
