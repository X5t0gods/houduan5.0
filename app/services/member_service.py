"""会员业务编排（阶段 7 交付物）。

CP1：查询/检索/详情/建档/修改/状态/等级
CP2：充值（幂等+分开记账）/流水/积分调整/消费档案/导出

⚠️ 会员账要清：本金 balance 与赠送 gift_balance **分开记账**（spec 5.9）。
⚠️ discount_rate 是 Rate（4位），被阶段4收银用作会员折扣率，改错影响收银金额。
"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.core.sys_config_reader import get_config
from app.repositories import member_flow_repository as flow_repo
from app.repositories import member_repository as repo
from app.repositories.auth_repository import write_operation_log
from app.schemas.member import (
    BalanceFlow,
    FavoriteCategory,
    Member,
    MemberCreate,
    MemberLevel,
    MemberLevelUpdate,
    MemberProfile,
    MemberUpdate,
    PointAdjustParams,
    PointAdjustResult,
    PointFlow,
    RechargeParams,
    RechargeResult,
    TopProduct,
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


# ---------- 5.9 POST /members/{id}/recharge（幂等 + 分开记账） ----------


def recharge(
    session: Session, member_id: int, params: RechargeParams, *, operator_id: int
) -> RechargeResult:
    """会员充值（spec 5.9，本阶段核心）。

    ⚠️ 幂等三步（同阶段4结算）：
    1. 按 request_id 查 mem_balance_flow，命中直接返回首次结果
    2. 未命中走正常流程
    3. 依赖 uk_request_id 兵底：捕 IntegrityError 后重查返回首次结果

    ⚠️ 分开记账（文档 1871 行）：写 2 条流水 RECHARGE（本金）+ GIFT（赠送），
       ⛔ 只有第一条带 request_id（两条都带会撞 uk_request_id 报 500）。
    """
    amount = money(params.amount or ZERO)
    gift_amount = money(params.gift_amount or ZERO)
    request_id = params.request_id

    # ---- 幂等第一步：查已有 ----
    if request_id:
        existing = flow_repo.find_balance_flow_by_request_id(session, request_id)
        if existing is not None:
            m = repo.get_member_by_id(session, existing.member_id)
            logger.info(f"充值幂等命中：request_id={request_id}")
            return RechargeResult(balance=m.balance, gift_balance=m.gift_balance)

    # ---- 校验 ----
    if amount <= ZERO and gift_amount <= ZERO:
        raise BusinessError(ErrorCode.AMOUNT_INVALID, "充值金额与赠送金额不能同时为 0")
    member = repo.get_member_by_id(session, member_id)
    if member is None:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, f"会员 id={member_id} 不存在")
    if member.status != STATUS_NORMAL:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, "冻结/挂失会员不允许充值")
    # 赠送比例限制（spec 4.4）：amount>0 且 gift/amount > 阈值 → 9003
    if amount > ZERO:
        gift_ratio_limit = Decimal(str(get_config(session, "mem.gift_ratio_limit", "0.50")))
        if gift_amount / amount > gift_ratio_limit:
            raise BusinessError(
                ErrorCode.GIFT_RATIO_EXCEED,
                f"赠送比例({gift_amount / amount:.0%})超过上限({gift_ratio_limit:.0%})",
            )

    # ---- 事务：加余额 + 分开记账 ----
    before_balance = member.balance
    before_gift = member.gift_balance
    after_balance = money(before_balance + amount)
    after_gift = money(before_gift + gift_amount)

    # 先更新余额（增量表达式）
    repo.update_member(session, member_id, {
        "balance": after_balance,
        "gift_balance": after_gift,
    })

    seq = NoSeqService(session)
    recharge_no = seq.next_no("CZ")

    request_id_used = False
    # 本金流水 RECHARGE
    if amount > ZERO:
        flow_repo.insert_balance_flow(
            session,
            member_id=member_id,
            flow_type="RECHARGE",
            amount=amount,
            gift_amount=ZERO,
            direction=1,
            before_balance=before_balance,
            after_balance=after_balance,
            before_gift=before_gift,
            after_gift=before_gift,  # 本条不改赠送
            pay_method=params.pay_method,
            source_no=recharge_no,
            request_id=request_id if request_id else None,
            operator=operator_id,
            remark=params.remark,
        )
        request_id_used = bool(request_id)
    # 赠送流水 GIFT
    if gift_amount > ZERO:
        flow_repo.insert_balance_flow(
            session,
            member_id=member_id,
            flow_type="GIFT",
            amount=ZERO,
            gift_amount=gift_amount,
            direction=1,
            before_balance=after_balance,  # 本金已变
            after_balance=after_balance,
            before_gift=before_gift,
            after_gift=after_gift,
            pay_method=params.pay_method,
            source_no=recharge_no,
            # ⛔ 只有第一条带 request_id；若本金为0（纯赠送）则本条带
            request_id=request_id if (request_id and not request_id_used) else None,
            operator=operator_id,
            remark=params.remark,
        )

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action="会员充值",
        target_type="mem_member", target_id=member_id,
        after_value={"recharge_no": recharge_no, "amount": str(amount), "gift": str(gift_amount)},
        result=1,
    )

    # ---- 幂等第三步：commit 时 IntegrityError（uk_request_id）兵底 ----
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if request_id:
            existing = flow_repo.find_balance_flow_by_request_id(session, request_id)
            if existing is not None:
                m = repo.get_member_by_id(session, existing.member_id)
                logger.info(f"充值幂等兵底（IntegrityError）：request_id={request_id}")
                return RechargeResult(balance=m.balance, gift_balance=m.gift_balance)
        raise

    logger.info(f"会员充值：member_id={member_id}, amount={amount}, gift={gift_amount}")
    return RechargeResult(balance=after_balance, gift_balance=after_gift)


# ---------- 5.10 / 5.11 会员流水 ----------


def list_balance_flows(
    session: Session, *, page: int = 1, page_size: int = 20,
    member_id: int | None = None, flow_type: str | None = None,
) -> tuple[list[BalanceFlow], int]:
    """储值流水列表（spec 5.10）。"""
    rows, total = flow_repo.list_balance_flows(
        session, page=page, page_size=page_size, member_id=member_id, flow_type=flow_type,
    )
    result = [
        BalanceFlow(
            id=f.id, member_id=f.member_id, member_name=mname, member_no=mno,
            flow_type=f.flow_type, amount=f.amount, gift_amount=f.gift_amount,
            direction=f.direction, before_balance=f.before_balance,
            after_balance=f.after_balance, pay_method=f.pay_method,
            source_no=f.source_no, operator_name=oname, remark=f.remark,
            created_at=f.created_at,
        )
        for f, mname, mno, oname in rows
    ]
    return result, total


def list_point_flows(
    session: Session, *, page: int = 1, page_size: int = 20,
    member_id: int | None = None, flow_type: str | None = None,
) -> tuple[list[PointFlow], int]:
    """积分流水列表（spec 5.11）。"""
    rows, total = flow_repo.list_point_flows(
        session, page=page, page_size=page_size, member_id=member_id, flow_type=flow_type,
    )
    result = [
        PointFlow(
            id=f.id, member_id=f.member_id, member_name=mname, member_no=mno,
            flow_type=f.flow_type, points=f.points, direction=f.direction,
            before_points=f.before_points, after_points=f.after_points,
            source_no=f.source_no, operator_name=oname, remark=f.remark,
            created_at=f.created_at,
        )
        for f, mname, mno, oname in rows
    ]
    return result, total


# ---------- 5.12 POST /members/{id}/points/adjust ----------


def adjust_points(
    session: Session, member_id: int, params: PointAdjustParams, *, operator_id: int
) -> PointAdjustResult:
    """积分手工调整（spec 5.12）。

    ⚠️ points 正数增加/负数扣减；reason 必填（2001）；扣减后不能为负（9002）。
    """
    if not params.reason or not params.reason.strip():
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "积分调整必须填写原因")
    if params.points == 0:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "调整积分不能为 0")

    member = repo.get_member_by_id(session, member_id)
    if member is None:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, f"会员 id={member_id} 不存在")

    before_points = member.points
    after_points = before_points + params.points
    if after_points < 0:
        raise BusinessError(ErrorCode.AMOUNT_INVALID, f"积分不足（当前 {before_points}）")

    repo.update_member(session, member_id, {"points": after_points})
    flow_repo.insert_point_flow(
        session,
        member_id=member_id,
        flow_type="ADJUST",
        points=abs(params.points),
        direction=1 if params.points > 0 else -1,
        before_points=before_points,
        after_points=after_points,
        source_no=None,
        operator=operator_id,
        remark=params.reason,
    )
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action="积分调整",
        target_type="mem_member", target_id=member_id,
        after_value={"points_delta": params.points, "reason": params.reason},
        result=1,
    )
    session.commit()
    return PointAdjustResult(points=after_points)


# ---------- 5.13 GET /members/{id}/profile ----------


def get_member_profile(session: Session, member_id: int) -> MemberProfile:
    """会员消费档案（spec 5.13）。

    ⚠️ avg_price = total_consume / consume_count（count=0 → 0，不除零）。
    ⚠️ favorite_categories/top_products 从 sal_trade_item 聚合（只统计 status!=VOID）。
    ⚠️ 当前 sal_trade 无初始数据 → 数组为空（正常）。
    """
    from sqlalchemy import func, select

    from app.models.sal_models import SalTrade, SalTradeItem

    member = repo.get_member_by_id(session, member_id)
    if member is None:
        raise BusinessError(ErrorCode.MEMBER_NOT_FOUND, f"会员 id={member_id} 不存在")

    # avg_price（除零保护）
    if member.consume_count and member.consume_count > 0:
        avg_price = money(member.total_consume / Decimal(member.consume_count))
    else:
        avg_price = ZERO

    # favorite_categories TOP5：sal_trade_item 无 category_id，需 JOIN prd_product 取分类
    cat_rows = _query_favorite_categories(session, member_id)
    total_for_ratio = sum((r[1] for r in cat_rows), ZERO) if cat_rows else ZERO
    favorite = [
        FavoriteCategory(
            name=name,
            ratio=float(money(amt / total_for_ratio)) if total_for_ratio > ZERO else 0.0,
        )
        for name, amt in cat_rows
    ]

    # top_products TOP10：按数量（sal_trade_item 有 product_name 快照）
    prod_rows = session.execute(
        select(SalTradeItem.product_name, func.sum(SalTradeItem.quantity).label("qty"))
        .join(SalTrade, SalTrade.id == SalTradeItem.trade_id)
        .where(SalTrade.member_id == member_id, SalTrade.status != "VOID")
        .group_by(SalTradeItem.product_name)
        .order_by(func.sum(SalTradeItem.quantity).desc())
        .limit(10)
    ).all()
    top_products = [
        TopProduct(name=name, qty=float(qty)) for name, qty in prod_rows
    ]

    return MemberProfile(
        member_id=member_id,
        member_no=member.member_no,
        real_name=member.real_name,
        total_consume=member.total_consume,
        consume_count=member.consume_count,
        avg_price=avg_price,
        favorite_categories=favorite,
        top_products=top_products,
    )


def _query_favorite_categories(session: Session, member_id: int) -> list[tuple]:
    """偏好品类 TOP5：sal_trade_item JOIN sal_trade JOIN prd_product JOIN prd_category。"""
    from sqlalchemy import func, select

    from app.models.prd_models import PrdCategory, PrdProduct
    from app.models.sal_models import SalTrade, SalTradeItem

    return list(session.execute(
        select(PrdCategory.category_name, func.sum(SalTradeItem.amount).label("amt"))
        .join(SalTrade, SalTrade.id == SalTradeItem.trade_id)
        .join(PrdProduct, PrdProduct.id == SalTradeItem.product_id)
        .join(PrdCategory, PrdCategory.id == PrdProduct.category_id)
        .where(SalTrade.member_id == member_id, SalTrade.status != "VOID")
        .group_by(PrdCategory.category_name)
        .order_by(func.sum(SalTradeItem.amount).desc())
        .limit(5)
    ).all())


# ---------- 5.14 GET /members/export ----------


def _mask_phone(phone: str) -> str:
    """手机号脱敏：中间 4 位打码（138****8866）。"""
    if not phone or len(phone) < 7:
        return phone or ""
    return phone[:3] + "****" + phone[-4:]


_EXPORT_HEADERS = [
    "会员卡号", "姓名", "手机号", "等级", "本金余额", "赠送余额",
    "积分", "累计消费", "消费笔数", "最近消费", "状态",
]
_EXPORT_MAX_ROWS = 10000
_STATUS_NAMES = {0: "冻结", 1: "正常", 2: "挂失"}


def export_members(
    session: Session, *, operator_id: int,
    keyword: str | None = None, level_id: int | None = None,
) -> tuple[bytes, str]:
    """会员导出（spec 5.14）：xlsx 文件流 + 手机号脱敏 + 记操作日志。

    ⚠️ 返回 (xlsx 字节流, ASCII 文件名)。行数上限 10000。
    """
    rows, total = repo.list_members(
        session, page=1, page_size=_EXPORT_MAX_ROWS, keyword=keyword, level_id=level_id,
    )
    if total > _EXPORT_MAX_ROWS:
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            f"导出数据超上限（{total} > {_EXPORT_MAX_ROWS}），请缩小范围",
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "会员"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4472C4")
    center = Alignment(horizontal="center", vertical="center")
    ws.append(_EXPORT_HEADERS)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center

    for m, level_name, _discount in rows:
        ws.append([
            m.member_no,
            m.real_name or "",
            _mask_phone(m.phone),  # ⛔ 脱敏
            level_name or "",
            float(m.balance),
            float(m.gift_balance),
            m.points,
            float(m.total_consume),
            m.consume_count,
            m.last_consume_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_consume_at else "",
            _STATUS_NAMES.get(m.status, str(m.status)),
        ])

    widths = [16, 12, 16, 12, 12, 12, 10, 14, 10, 20, 8]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    # ⛔ 导出行为记入操作日志（文档 1913 行）
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="会员", action="导出会员",
        target_type="mem_member", target_id=None,
        after_value={"count": total},
        result=1,
    )
    session.commit()

    today = datetime.now(ZoneInfo(settings.TZ)).strftime("%Y%m%d")
    return buf.getvalue(), f"members_{today}.xlsx"
