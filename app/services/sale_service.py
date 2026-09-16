"""销售业务编排（阶段 4 CP3+CP4 交付物）—— **本阶段最核心最复杂的模块**。

职责：
- 结算事务 T1（POST /sales/trades）：幂等三步 + 8 步校验 + 9 步写入
- 销售单查询（GET /sales/trades、GET /sales/trades/{id}）
- 退货（POST /sales/trades/return）
- 作废（POST /sales/trades/{id}/void）
- 小票（POST /sales/trades/{id}/receipt）
- 挂单 CRUD（POST/GET/POST/DELETE /sales/holds）
- 班次（GET /sales/sessions/current、POST /sales/sessions/close）

⚠️ 头号风险（spec 验收 11 生死线）：
   前端把 `payments[0].amount` 设成它自己算的 `receivable`（views/pos/index.vue:217），
   后端结算时**重新算一遍**，若与前端不一致 → 7001 → 收银台完全不可用。
   因此本文件必须**完整复刻前端公式**（stores/pos.ts:39-73）：
   ```
   totalAmount      = Σ(unit_price × quantity)
   itemDiscount     = Σ(item.discount_amount)           ← 前端 CartItem 传入的行优惠
   promoDiscount    = calc 返回的 details[].amount 合计  ← 后端促销引擎
   memberDiscount   = rate >= 1 ? 0 : (totalAmount - itemDiscount - promoDiscount) × (1 - rate)
   pointDeduct      = use_points × point_deduct_rate（cap 不超 point_deduct_limit × receivable）
   discountAmount   = itemDiscount + promoDiscount + memberDiscount + pointDeduct
   receivable       = max(0, totalAmount - discountAmount - roundAmount)
   ```

⚠️ 金额量化一律用 `app.utils.money.money()`（ROUND_HALF_UP），
   与前端 `Math.round(n*100)/100` 严格一致。⛔ 禁止 Python 内置 round()。
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.core.sys_config_reader import get_config
from app.models.mem_models import MemLevel, MemMember
from app.models.prd_models import PrdProduct
from app.models.pro_models import ProPromotion
from app.models.sal_models import SalSession, SalTrade
from app.repositories import inventory_write_repository as inv_repo
from app.repositories import sale_repository as repo
from app.schemas.sale import (
    ReceiptResult,
    SaleReturnCreateRequest,
    SaleReturnOut,
    SessionCloseRequest,
    TradeCreateRequest,
    TradeCreateResult,
    TradeItemOut,
    TradeOut,
    TradePaymentIn,
    TradePaymentOut,
    VoidTradeRequest,
)
from app.services.promotion_service import CalcResult, EngineItem, calc_promotion
from app.utils.money import ZERO, money, money_max_zero

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ)).replace(tzinfo=None)


# ---------- 配置项快捷读取（sys_config） ----------


class _Cfg:
    """结算相关 sys_config key（spec 2.3 表，运行时读取不许硬编码）。"""

    POINT_RATE = "mem.point_rate"                    # 每消费 1 元获得 N 积分
    POINT_DEDUCT_RATE = "mem.point_deduct_rate"      # 每积分抵扣 N 元
    POINT_DEDUCT_LIMIT = "mem.point_deduct_limit"    # 积分抵扣上限占应收比例
    PROMO_MAX_RATE = "sal.promo_max_rate"            # 整单优惠上限比例
    RETURN_LIMIT_DAYS = "sal.return_limit_days"      # 退货期限天数
    POS_RECEIPT_TITLE = "pos.receipt_title"          # 小票抬头


# ================================================================
# 6.3 POST /sales/trades —— 结算事务 T1（本阶段最核心）
# ================================================================


def settle_trade(
    session: Session,
    params: TradeCreateRequest,
    *,
    store_id: int,
    cashier_id: int,
    cashier_name: str | None = None,
) -> TradeCreateResult:
    """结算主入口（幂等 + 校验 + T1 事务）。

    ⚠️ 幂等三步（spec 6.3，缺一不可）：
    1. 先按 request_id 查 sal_trade，命中直接返回首次结果
    2. 未命中走正常流程
    3. 依赖 uk_request_id 唯一索引做最后兜底：
       捕获 IntegrityError 后重新查询并返回首次结果（防并发双写）
    """
    # ---- 幂等第一步：查已有 ----
    existing = repo.find_trade_by_request_id(session, params.request_id)
    if existing is not None:
        logger.info(f"幂等命中：request_id={params.request_id} → trade_no={existing.trade_no}")
        return _build_settle_result(existing)

    # ---- 校验（8 步） ----
    _validate_settle_params(session, params, store_id=store_id)

    # ---- T1 事务（9 步）----
    now = _now()
    seq = NoSeqService(session)
    trade_no = seq.next_no("XS")

    # 重算全部优惠（促销 + 会员折扣 + 积分抵扣）
    calc = _recalc_receivable(session, params, now=now)

    # 校验支付总额 == receivable（容差 ±0.01）
    _validate_payment_amount(params.payments, calc.receivable)

    # 校验整单优惠不超过 promo_max_rate
    _validate_promo_max_rate(session, calc)

    # 步骤 1-2：写 sal_trade 主表
    total_qty = sum(it.quantity for it in params.items)
    trade = repo.insert_trade(
        session,
        trade_no=trade_no,
        request_id=params.request_id,
        store_id=store_id,
        pos_id=params.pos_id,
        session_id=params.session_id,
        cashier_id=cashier_id,
        member_id=params.member_id,
        total_qty=total_qty,
        total_amount=calc.total_amount,
        discount_amount=calc.total_discount,
        point_deduct=calc.point_deduct,
        receivable=calc.receivable,
        received=calc.received,
        change_amount=calc.change_amount,
        cost_amount=calc.cost_amount,
        gross_profit=money(calc.receivable - calc.cost_amount),
        round_amount=money(params.round_amount or ZERO),
        status="FINISHED",
        trade_time=now,
        remark=params.remark,
    )

    # 步骤 3：写 sal_trade_item（冗余快照 + cost_price 锁定历史毛利）
    for idx, item in enumerate(params.items):
        line = calc.item_details[idx]
        repo.insert_trade_item(
            session,
            trade_id=trade.id,
            product_id=item.product_id,
            product_name=line["product_name"],
            spec=line["spec"],
            unit_name=line["unit_name"],
            quantity=item.quantity,
            unit_price=line["unit_price"],
            origin_price=line["origin_price"],
            cost_price=line["cost_price"],
            discount_amount=line["discount_amount"],
            amount=line["amount"],
            promo_id=line.get("promo_id"),
            promo_type=line.get("promo_type"),
        )

    # 步骤 4：写 sal_trade_payment
    for pay in params.payments:
        repo.insert_trade_payment(
            session,
            trade_id=trade.id,
            pay_method=pay.pay_method,
            amount=money(pay.amount),
            pay_no=pay.pay_no,
            pay_status="SUCCESS",
        )

    # 步骤 5-6：扣库存（条件更新防超卖）+ 写 inv_stock_flow
    for idx, item in enumerate(params.items):
        line = calc.item_details[idx]
        ok, before_qty, after_qty, avg_cost = inv_repo.deduct_stock(
            session,
            store_id=store_id,
            product_id=item.product_id,
            quantity=item.quantity,
        )
        if not ok:
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH_SALE,
                f"商品「{line['product_name']}」库存不足",
            )
        inv_repo.write_stock_flow(
            session,
            store_id=store_id,
            product_id=item.product_id,
            flow_type="SALE_OUT",
            direction=-1,
            quantity=item.quantity,
            before_qty=before_qty,
            after_qty=after_qty,
            unit_cost=line["cost_price"],
            amount=money(line["cost_price"] * item.quantity),
            source_type="SALE",
            source_no=trade_no,
            operator=cashier_id,
        )

    # 步骤 7：会员处理
    if params.member_id:
        _handle_member_settlement(
            session,
            member_id=params.member_id,
            receivable=calc.receivable,
            payments=params.payments,
            use_points=params.use_points or 0,
            point_deduct=calc.point_deduct,
            trade_no=trade_no,
            now=now,
            operator=cashier_id,
        )

    # 步骤 8：更新促销统计
    _update_promo_stats(session, calc.promo_details)

    # 步骤 9：更新班次累计
    if params.session_id:
        pay_method_amounts: dict[str, Decimal] = {}
        for pay in params.payments:
            pay_method_amounts[pay.pay_method] = (
                pay_method_amounts.get(pay.pay_method, ZERO) + money(pay.amount)
            )
        repo.increment_session_sale(
            session,
            params.session_id,
            amount=calc.receivable,
            pay_method_amounts=pay_method_amounts,
        )

    # ---- 幂等第三步：commit 时若 IntegrityError（uk_request_id 冲突）则重查 ----
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = repo.find_trade_by_request_id(session, params.request_id)
        if existing is not None:
            logger.info(f"幂等兜底（IntegrityError）：request_id={params.request_id}")
            return _build_settle_result(existing)
        raise  # 非 request_id 冲突，原样抛出

    logger.info(f"结算成功：trade_no={trade_no}, receivable={calc.receivable}")
    return _build_settle_result(trade)


# ---------- 结算内部辅助 ----------


class _SettleCalc:
    """结算计算中间结果（不对外暴露）。"""

    def __init__(self) -> None:
        self.total_amount: Decimal = ZERO
        self.item_discount: Decimal = ZERO       # 行优惠合计（前端 CartItem.discount_amount）
        self.promo_discount: Decimal = ZERO      # 促销优惠合计
        self.member_discount: Decimal = ZERO     # 会员折扣
        self.point_deduct: Decimal = ZERO        # 积分抵扣
        self.round_amount: Decimal = ZERO        # 抹零
        self.total_discount: Decimal = ZERO      # 优惠合计（写 sal_trade.discount_amount）
        self.receivable: Decimal = ZERO
        self.received: Decimal = ZERO
        self.change_amount: Decimal = ZERO
        self.cost_amount: Decimal = ZERO
        self.item_details: list[dict] = []       # 每行的快照信息
        self.promo_details: list[tuple[int, Decimal]] = []  # (promo_id, amount)


def _recalc_receivable(
    session: Session,
    params: TradeCreateRequest,
    *,
    now: datetime,
) -> _SettleCalc:
    """后端重算全部优惠（spec 三 ④：绝不能只信前端传的值）。

    公式逐字复刻 stores/pos.ts:39-73。
    """
    calc = _SettleCalc()

    # 1. 读取商品信息（批量，避免 N+1）
    product_ids = [it.product_id for it in params.items]
    products = _get_products_map(session, product_ids)

    # 2. 计算 totalAmount 与行快照
    for item in params.items:
        prod = products.get(item.product_id)
        if prod is None:
            raise BusinessError(ErrorCode.PRODUCT_DISABLED, f"商品ID={item.product_id}不存在")
        unit_price = money(item.unit_price) if item.unit_price else money(prod.sale_price)
        origin_price = money(prod.sale_price)
        amount = money(unit_price * item.quantity)
        discount_amount = money(item.discount_amount) if item.discount_amount else ZERO
        cost_price = prod.avg_cost or Decimal("0.0000")

        calc.total_amount += amount
        calc.item_discount += discount_amount
        calc.item_details.append({
            "product_name": prod.product_name,
            "spec": prod.spec,
            "unit_name": None,  # 下面补
            "unit_price": unit_price,
            "origin_price": origin_price,
            "cost_price": cost_price,
            "discount_amount": discount_amount,
            "amount": money(amount - discount_amount),
            "promo_id": item.promo_id,
            "promo_type": item.promo_type,
        })

    # 补 unit_name（从 base_unit 表批量查）
    unit_ids = [products[pid].unit_id for pid in product_ids if pid in products]
    unit_map = _get_unit_map(session, set(unit_ids))
    for idx, item in enumerate(params.items):
        prod = products.get(item.product_id)
        if prod:
            calc.item_details[idx]["unit_name"] = unit_map.get(prod.unit_id)

    calc.total_amount = money(calc.total_amount)
    calc.item_discount = money(calc.item_discount)

    # 3. 促销优惠（调引擎，与 calc 接口一样）
    engine_items = [
        EngineItem(
            product_id=it.product_id,
            quantity=it.quantity,
            unit_price=it.unit_price,
        )
        for it in params.items
    ]
    is_member = False
    member: MemMember | None = None
    if params.member_id:
        member = repo.get_member_for_update(session, params.member_id)
        if member is None:
            raise BusinessError(ErrorCode.AMOUNT_INVALID, "会员不存在")
        is_member = member.status == 1

    promo_result: CalcResult = calc_promotion(session, engine_items, is_member=is_member, now=now)
    calc.promo_discount = promo_result.discount_amount
    calc.promo_details = [(d.promo_id, d.amount) for d in promo_result.details]

    # 4. 会员折扣（spec 三 ②：取自 mem_level.discount_rate，不是 MEMBER 促销）
    #    公式：rate >= 1 ? 0 : (totalAmount - itemDiscount - promoDiscount) × (1 - rate)
    if member and member.level_id:
        level = session.get(MemLevel, member.level_id)
        if level:
            rate = level.discount_rate
            if rate < Decimal("1"):
                base_for_member = calc.total_amount - calc.item_discount - calc.promo_discount
                calc.member_discount = money(base_for_member * (Decimal("1") - rate))

    # 5. 积分抵扣
    use_points = params.use_points or 0
    if use_points > 0 and member:
        point_deduct_rate = Decimal(str(get_config(session, _Cfg.POINT_DEDUCT_RATE, "0.01")))
        point_deduct_limit = Decimal(str(get_config(session, _Cfg.POINT_DEDUCT_LIMIT, "0.50")))
        raw_deduct = money(Decimal(use_points) * point_deduct_rate)
        # 上限 = receivable_before_point × limit（此处用 promo 后的基数近似）
        base_for_point = (
            calc.total_amount - calc.item_discount
            - calc.promo_discount - calc.member_discount
        )
        max_deduct = money(base_for_point * point_deduct_limit)
        calc.point_deduct = min(raw_deduct, max_deduct)
        # 校验可用积分
        if use_points > member.points:
            raise BusinessError(ErrorCode.MEMBER_POINT_NOT_ENOUGH, "积分不足")

    # 6. 抹零
    calc.round_amount = money(params.round_amount or ZERO)

    # 7. 汇总
    calc.total_discount = money(
        calc.item_discount + calc.promo_discount + calc.member_discount + calc.point_deduct
    )
    calc.receivable = money_max_zero(
        calc.total_amount - calc.total_discount - calc.round_amount
    )

    # 8. 实收与找零
    calc.received = money(sum(money(p.amount) for p in params.payments))
    calc.change_amount = money_max_zero(calc.received - calc.receivable)

    # 9. 成本合计（用于 gross_profit）
    for idx, item in enumerate(params.items):
        line = calc.item_details[idx]
        calc.cost_amount += money(line["cost_price"] * item.quantity)
    calc.cost_amount = money(calc.cost_amount)

    return calc


def _validate_settle_params(
    session: Session,
    params: TradeCreateRequest,
    *,
    store_id: int,
) -> None:
    """8 步校验（spec 6.3 表格）。

    ⚠️ 顺序有讲究：先校验基础合法性（避免后续无意义的 DB 查询），
       再校验业务规则（库存/余额等需要锁行的放后面）。
    """
    # 步骤 1：items 非空、payments 非空、支付方式合法
    valid_methods = {"CASH", "WECHAT", "ALIPAY", "CARD", "BALANCE", "POINTS", "OTHER"}
    for pay in params.payments:
        if pay.pay_method not in valid_methods:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, f"不合法的支付方式：{pay.pay_method}")

    # 步骤 2：商品存在且在售
    product_ids = [it.product_id for it in params.items]
    products = _get_products_map(session, product_ids)
    for pid in product_ids:
        prod = products.get(pid)
        if prod is None:
            raise BusinessError(ErrorCode.PRODUCT_DISABLED, f"商品ID={pid}不存在")
        if prod.status == 0:
            raise BusinessError(ErrorCode.PRODUCT_DISABLED, f"商品「{prod.product_name}」已停用")

    # 步骤 3：库存充足（条件更新在 T1 里做，这里做**预检**给友好提示）
    for item in params.items:
        stock = inv_repo.get_stock(session, store_id, item.product_id)
        if stock is None or stock.quantity < item.quantity:
            prod = products.get(item.product_id)
            name = prod.product_name if prod else str(item.product_id)
            raise BusinessError(ErrorCode.STOCK_NOT_ENOUGH_SALE, f"商品「{name}」库存不足")

    # 步骤 4：会员存在（传了 member_id 时）
    if params.member_id:
        member = session.get(MemMember, params.member_id)
        if member is None:
            raise BusinessError(ErrorCode.AMOUNT_INVALID, "会员不存在")

    # 步骤 5-6 在 _recalc_receivable 里处理（需要加锁读会员）
    # 步骤 7 在 settle_trade 主体里处理（需要完整计算结果）
    # 步骤 8 在 settle_trade 主体里处理


def _validate_payment_amount(payments: list[TradePaymentIn], receivable: Decimal) -> None:
    """校验支付总额 == receivable（容差 ±0.01，应对前端浮点边界）。

    ⚠️ 容差理由：前端 JS 用 Math.round(n*100)/100，极少数情况下可能与
       后端 Decimal 量化结果差 1 分（如 0.005 的中间值）。
       ±0.01 容差覆盖此边界，但不会放过真正的金额错误。
    """
    total_paid = money(sum(money(p.amount) for p in payments))
    diff = abs(total_paid - receivable)
    if diff > Decimal("0.01"):
        raise BusinessError(
            ErrorCode.PAY_AMOUNT_MISMATCH,
            f"支付金额({total_paid})与应收金额({receivable})不一致",
        )


def _validate_promo_max_rate(session: Session, calc: _SettleCalc) -> None:
    """校验整单优惠不超过 promo_max_rate（spec 2.3：80%）。"""
    max_rate = Decimal(str(get_config(session, _Cfg.PROMO_MAX_RATE, "0.80")))
    if calc.total_amount > ZERO:
        actual_rate = calc.total_discount / calc.total_amount
        if actual_rate > max_rate:
            raise BusinessError(
                ErrorCode.AMOUNT_INVALID,
                f"整单优惠比例({actual_rate:.2%})超过上限({max_rate:.0%})",
            )


def _handle_member_settlement(
    session: Session,
    *,
    member_id: int,
    receivable: Decimal,
    payments: list[TradePaymentIn],
    use_points: int,
    point_deduct: Decimal,
    trade_no: str,
    now: datetime,
    operator: int,
) -> None:
    """会员结算处理（spec 6.3 步骤 7）。

    ⚠️ 储值支付：**先扣 gift_balance，不足再扣 balance**（spec 三 ⑥）。
    ⚠️ 积分：消费得积分 = floor(receivable × point_rate)。
    ⚠️ 等级升级：按 mem_level.upgrade_condition='CONSUME' + condition_value 比对 total_consume。
    """
    member = repo.get_member_for_update(session, member_id)
    if member is None:
        return

    # --- 储值扣款 ---
    balance_pay_amount = sum(
        money(p.amount) for p in payments if p.pay_method == "BALANCE"
    )
    if balance_pay_amount > ZERO:
        # 先扣赠送余额，不足再扣本金
        gift_deduct = min(balance_pay_amount, member.gift_balance)
        balance_deduct = balance_pay_amount - gift_deduct
        if balance_deduct > member.balance:
            raise BusinessError(ErrorCode.MEMBER_BALANCE_NOT_ENOUGH, "会员储值余额不足")

        before_balance = member.balance
        before_gift = member.gift_balance
        after_gift = money(before_gift - gift_deduct)
        after_balance = money(before_balance - balance_deduct)

        repo.insert_balance_flow(
            session,
            member_id=member_id,
            flow_type="CONSUME",
            amount=balance_deduct,
            gift_amount=gift_deduct,
            direction=-1,
            before_balance=before_balance,
            after_balance=after_balance,
            before_gift=before_gift,
            after_gift=after_gift,
            source_no=trade_no,
            operator=operator,
        )
        # 更新余额（增量表达式）
        repo.update_member_balance(
            session, member_id,
            balance_delta=-balance_deduct,
            gift_delta=-gift_deduct,
        )

    # --- 积分处理 ---
    points_delta = 0
    # 消费得积分
    if receivable > ZERO:
        point_rate = Decimal(str(get_config(session, _Cfg.POINT_RATE, "1.00")))
        earned_points = int(math.floor(float(receivable * point_rate)))
        points_delta += earned_points
        if earned_points > 0:
            before_pts = member.points
            after_pts = before_pts + earned_points
            repo.insert_point_flow(
                session,
                member_id=member_id,
                flow_type="EARN",
                points=earned_points,
                direction=1,
                before_points=before_pts,
                after_points=after_pts,
                source_no=trade_no,
                operator=operator,
            )

    # 使用积分抵扣
    if use_points > 0:
        points_delta -= use_points
        before_pts = member.points + (earned_points if receivable > ZERO else 0)
        after_pts = before_pts - use_points
        repo.insert_point_flow(
            session,
            member_id=member_id,
            flow_type="DEDUCT",
            points=use_points,
            direction=-1,
            before_points=before_pts,
            after_points=after_pts,
            source_no=trade_no,
            operator=operator,
        )

    # --- 累计消费 + 等级升级 ---
    new_total_consume = money(member.total_consume + receivable)
    new_level_id = _check_level_upgrade(session, member.level_id, new_total_consume)

    repo.update_member_balance(
        session, member_id,
        balance_delta=ZERO,  # 已在上面处理
        gift_delta=ZERO,
        points_delta=points_delta,
        total_consume_delta=receivable,
        consume_count_delta=1,
        last_consume_at=now,
        level_id=new_level_id,
    )


def _check_level_upgrade(
    session: Session, current_level_id: int, total_consume: Decimal
) -> int | None:
    """检查是否满足升级条件（spec 6.3 步骤 7）。

    Returns:
        新 level_id（需升级时）或 None（不变）。
    """
    from sqlalchemy import select

    levels = list(session.execute(
        select(MemLevel)
        .where(MemLevel.status == 1, MemLevel.upgrade_condition == "CONSUME")
        .order_by(MemLevel.condition_value.desc())
    ).scalars().all())

    for lv in levels:
        if lv.id == current_level_id:
            continue
        if total_consume >= lv.condition_value and lv.id > current_level_id:
            return lv.id
    return None


def _update_promo_stats(session: Session, promo_details: list[tuple[int, Decimal]]) -> None:
    """更新促销统计（spec 6.3 步骤 8）：trigger_count += 1, discount_total += amount。"""
    from sqlalchemy import update as sa_update

    # 聚合同一促销的多条命中（如 COMBO 可能只一条）
    agg: dict[int, Decimal] = {}
    for promo_id, amount in promo_details:
        agg[promo_id] = agg.get(promo_id, ZERO) + amount

    for promo_id, amount in agg.items():
        if amount <= ZERO:
            continue
        session.execute(
            sa_update(ProPromotion)
            .where(ProPromotion.id == promo_id)
            .values(
                trigger_count=ProPromotion.trigger_count + 1,
                discount_total=ProPromotion.discount_total + money(amount),
            )
        )
    session.flush()


def _build_settle_result(trade) -> TradeCreateResult:
    """构建结算响应（从 SalTrade ORM 对象）。"""
    return TradeCreateResult(
        id=trade.id,
        trade_no=trade.trade_no,
        receivable=trade.receivable,
        received=trade.received,
        change_amount=trade.change_amount,
        trade_time=trade.trade_time,
        item_count=0,  # 由调用方填充（或前端自己算）
    )


def _get_products_map(session: Session, product_ids: list[int]) -> dict[int, PrdProduct]:
    """批量读商品档案（避免 N+1）。"""
    from sqlalchemy import select

    if not product_ids:
        return {}
    rows = session.execute(
        select(PrdProduct).where(PrdProduct.id.in_(product_ids))
    ).scalars().all()
    return {p.id: p for p in rows}


def _get_unit_map(session: Session, unit_ids: set[int]) -> dict[int, str]:
    """批量读单位名称。"""
    from sqlalchemy import select

    from app.models.base_models import BaseUnit

    if not unit_ids:
        return {}
    rows = session.execute(
        select(BaseUnit.id, BaseUnit.unit_name).where(BaseUnit.id.in_(unit_ids))
    ).all()
    return {r[0]: r[1] for r in rows}


# ================================================================
# 6.4 GET /sales/trades —— 销售单列表
# ================================================================


def list_trades(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[TradeOut], int]:
    """分页查询销售单（spec 6.4）。"""
    rows, total = repo.list_trades(
        session,
        page=page,
        page_size=page_size,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        status=status,
        store_id=store_id,
    )
    result = []
    for trade, cashier_name, member_name, member_no in rows:
        result.append(TradeOut(
            id=trade.id,
            trade_no=trade.trade_no,
            request_id=trade.request_id,
            store_id=trade.store_id,
            pos_id=trade.pos_id,
            cashier_id=trade.cashier_id,
            cashier_name=cashier_name,
            member_id=trade.member_id,
            member_name=member_name,
            member_no=member_no,
            total_qty=trade.total_qty,
            total_amount=trade.total_amount,
            discount_amount=trade.discount_amount,
            point_deduct=trade.point_deduct,
            receivable=trade.receivable,
            received=trade.received,
            change_amount=trade.change_amount,
            cost_amount=trade.cost_amount,
            gross_profit=trade.gross_profit,
            round_amount=trade.round_amount,
            status=trade.status,
            trade_time=trade.trade_time,
        ))
    return result, total


# ================================================================
# 6.5 GET /sales/trades/{id} —— 销售单详情
# ================================================================


def get_trade_detail(session: Session, trade_id: int) -> TradeOut:
    """查询销售单详情（含 items + payments）。

    ⚠️ 用两次查询再内存组装，**不用 relationship()**（spec 6.4）。
    """
    trade = repo.get_trade_by_id(session, trade_id)
    if trade is None:
        raise BusinessError(ErrorCode.TRADE_NOT_FOUND, "销售单不存在")

    items_rows = repo.list_trade_items(session, trade_id)
    payment_rows = repo.list_trade_payments(session, trade_id)
    cashier_name = repo.get_user_real_name(session, trade.cashier_id)

    # 会员信息
    member_name = None
    member_no = None
    if trade.member_id:
        member = session.get(MemMember, trade.member_id)
        if member:
            member_name = member.real_name
            member_no = member.member_no

    items_out = [
        TradeItemOut(
            id=it.id,
            trade_id=it.trade_id,
            product_id=it.product_id,
            product_name=it.product_name,
            spec=it.spec,
            unit_name=it.unit_name,
            quantity=it.quantity,
            unit_price=it.unit_price,
            origin_price=it.origin_price,
            cost_price=it.cost_price,
            discount_amount=it.discount_amount,
            amount=it.amount,
            batch_id=it.batch_id,
            promo_id=it.promo_id,
            promo_type=it.promo_type,
        )
        for it in items_rows
    ]
    payments_out = [
        TradePaymentOut(
            pay_method=p.pay_method,
            amount=p.amount,
            pay_no=p.pay_no,
            pay_status=p.pay_status,
        )
        for p in payment_rows
    ]

    return TradeOut(
        id=trade.id,
        trade_no=trade.trade_no,
        request_id=trade.request_id,
        store_id=trade.store_id,
        pos_id=trade.pos_id,
        cashier_id=trade.cashier_id,
        cashier_name=cashier_name,
        member_id=trade.member_id,
        member_name=member_name,
        member_no=member_no,
        total_qty=trade.total_qty,
        total_amount=trade.total_amount,
        discount_amount=trade.discount_amount,
        point_deduct=trade.point_deduct,
        receivable=trade.receivable,
        received=trade.received,
        change_amount=trade.change_amount,
        cost_amount=trade.cost_amount,
        gross_profit=trade.gross_profit,
        round_amount=trade.round_amount,
        status=trade.status,
        trade_time=trade.trade_time,
        items=items_out,
        payments=payments_out,
    )


# ================================================================
# 6.6 POST /sales/trades/return —— 退货
# ================================================================


def return_trade(
    session: Session,
    params: SaleReturnCreateRequest,
    *,
    store_id: int,
    operator_id: int,
    operator_name: str | None = None,
) -> SaleReturnOut:
    """销售退货（spec 6.6）。

    ⚠️ 退货不做幂等（sal_return 无 request_id，spec 三 ⑥）。
    ⚠️ 防并发重复退货：SELECT ... FOR UPDATE + 已退数量校验。
    """
    from sqlalchemy import select

    # 1. 原单存在（加行锁防并发重复退货）
    trade = session.execute(
        select(SalTrade).where(SalTrade.id == params.source_trade_id).with_for_update()
    ).scalar_one_or_none()
    if trade is None:
        raise BusinessError(ErrorCode.TRADE_NOT_FOUND, "原销售单不存在")
    if trade.status == "VOID":
        raise BusinessError(ErrorCode.TRADE_NOT_FOUND, "已作废单据不可退货")

    # 2. 退货期限
    limit_days = int(get_config(session, _Cfg.RETURN_LIMIT_DAYS, 7))
    if trade.trade_time and (_now() - trade.trade_time).days > limit_days:
        raise BusinessError(ErrorCode.RETURN_EXPIRED, f"超出退货期限（{limit_days}天）")

    # 3. 退货数量校验
    now = _now()
    seq = NoSeqService(session)
    return_no = seq.next_no("TH")
    total_return_amount = ZERO
    total_cost_amount = ZERO

    # 获取原单明细
    original_items = repo.list_trade_items(session, trade.id)
    orig_item_map = {it.product_id: it for it in original_items}

    for ret_item in params.items:
        orig = orig_item_map.get(ret_item.product_id)
        if orig is None:
            raise BusinessError(
                ErrorCode.RETURN_QTY_EXCEED,
                f"商品ID={ret_item.product_id}不在原单中",
            )
        # 已退数量
        returned_qty = repo.sum_returned_quantity(session, trade.id, ret_item.product_id)
        available_qty = orig.quantity - returned_qty
        if ret_item.quantity > available_qty:
            raise BusinessError(
                ErrorCode.RETURN_QTY_EXCEED,
                f"退货数量({ret_item.quantity})超出可退数量({available_qty})",
            )

    # 4. 写退货单
    for ret_item in params.items:
        orig = orig_item_map[ret_item.product_id]
        line_amount = money(orig.unit_price * ret_item.quantity)
        line_cost = money(orig.cost_price * ret_item.quantity)
        total_return_amount += line_amount
        total_cost_amount += line_cost

    total_return_amount = money(total_return_amount)
    total_cost_amount = money(total_cost_amount)

    sal_return = repo.insert_return(
        session,
        return_no=return_no,
        source_trade_id=trade.id,
        store_id=store_id,
        member_id=trade.member_id,
        total_amount=total_return_amount,
        cost_amount=total_cost_amount,
        refund_method=params.refund_method,
        refund_status="REFUNDED",
        refunded_at=now,
        reason=params.reason,
        operator=operator_id,
    )

    for ret_item in params.items:
        orig = orig_item_map[ret_item.product_id]
        line_amount = money(orig.unit_price * ret_item.quantity)
        line_cost = money(orig.cost_price * ret_item.quantity)
        repo.insert_return_item(
            session,
            return_id=sal_return.id,
            product_id=ret_item.product_id,
            quantity=ret_item.quantity,
            unit_price=orig.unit_price,
            cost_price=orig.cost_price,
            amount=line_amount,
        )
        # 回补库存
        before_qty, after_qty, avg_cost = inv_repo.restore_stock(
            session,
            store_id=store_id,
            product_id=ret_item.product_id,
            quantity=ret_item.quantity,
        )
        inv_repo.write_stock_flow(
            session,
            store_id=store_id,
            product_id=ret_item.product_id,
            flow_type="SALE_RETURN_IN",
            direction=1,
            quantity=ret_item.quantity,
            before_qty=before_qty,
            after_qty=after_qty,
            unit_cost=orig.cost_price,
            amount=line_cost,
            source_type="RETURN",
            source_no=return_no,
            operator=operator_id,
        )

    # 5. 会员退款（⛔ 只退本金，赠送部分不退 —— spec 三 ⑥）
    if trade.member_id and params.refund_method == "BALANCE":
        member = repo.get_member_for_update(session, trade.member_id)
        if member:
            before_balance = member.balance
            after_balance = money(before_balance + total_return_amount)
            repo.insert_balance_flow(
                session,
                member_id=trade.member_id,
                flow_type="REFUND",
                amount=total_return_amount,
                gift_amount=ZERO,  # ⛔ 赠送不退
                direction=1,
                before_balance=before_balance,
                after_balance=after_balance,
                before_gift=member.gift_balance,
                after_gift=member.gift_balance,
                source_no=return_no,
                operator=operator_id,
            )
            repo.update_member_balance(
                session, trade.member_id,
                balance_delta=total_return_amount,
                gift_delta=ZERO,
            )

    # 6. 扣回积分
    if trade.member_id:
        point_rate = Decimal(str(get_config(session, _Cfg.POINT_RATE, "1.00")))
        deduct_points = int(math.floor(float(total_return_amount * point_rate)))
        if deduct_points > 0:
            member = session.get(MemMember, trade.member_id)
            if member and member.points >= deduct_points:
                before_pts = member.points
                after_pts = before_pts - deduct_points
                repo.insert_point_flow(
                    session,
                    member_id=trade.member_id,
                    flow_type="RETURN",
                    points=deduct_points,
                    direction=-1,
                    before_points=before_pts,
                    after_points=after_pts,
                    source_no=return_no,
                    operator=operator_id,
                )
                repo.update_member_balance(
                    session, trade.member_id,
                    balance_delta=ZERO,
                    gift_delta=ZERO,
                    points_delta=-deduct_points,
                )

    # 7. 判断原单状态：全部退完 → RETURNED，部分退 → 保持 FINISHED
    all_returned = _is_fully_returned(session, trade.id, original_items)
    if all_returned:
        repo.update_trade_status(session, trade.id, "RETURNED")

    # 8. 更新班次退货金额
    if trade.session_id:
        from sqlalchemy import update as sa_update

        session.execute(
            sa_update(SalSession)
            .where(SalSession.id == trade.session_id)
            .values(return_amount=SalSession.return_amount + total_return_amount)
        )
        session.flush()

    session.commit()
    logger.info(f"退货成功：return_no={return_no}, amount={total_return_amount}")

    return SaleReturnOut(
        id=sal_return.id,
        return_no=return_no,
        source_trade_id=trade.id,
        source_trade_no=trade.trade_no,
        store_id=store_id,
        member_id=trade.member_id,
        total_amount=total_return_amount,
        cost_amount=total_cost_amount,
        refund_method=params.refund_method,
        refund_status="REFUNDED",
        reason=params.reason,
        operator_name=operator_name,
        created_at=now,
    )


def _is_fully_returned(session: Session, trade_id: int, original_items: list) -> bool:
    """判断原单是否全部退完。"""
    for orig in original_items:
        returned = repo.sum_returned_quantity(session, trade_id, orig.product_id)
        if returned < orig.quantity:
            return False
    return True


# ================================================================
# 6.7 POST /sales/trades/{id}/void —— 作废
# ================================================================


def void_trade(
    session: Session,
    trade_id: int,
    params: VoidTradeRequest,
    *,
    store_id: int,
    operator_id: int,
) -> None:
    """作废销售单（spec 6.7）。

    ⚠️ 已交班的单据不可作废（session_id 对应班次 status != 'OPEN'）。
    ⚠️ 已退货的单据不可作废。
    """
    trade = repo.get_trade_by_id(session, trade_id)
    if trade is None:
        raise BusinessError(ErrorCode.TRADE_NOT_FOUND, "销售单不存在")
    if trade.status == "VOID":
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "该单据已作废")
    if trade.status == "RETURNED":
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "已退货单据不可作废")

    # 检查班次状态
    if trade.session_id:
        sal_session = repo.get_session_by_id(session, trade.session_id)
        if sal_session and sal_session.status != "OPEN":
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "已交班的单据不可作废")

    # 回补库存
    items = repo.list_trade_items(session, trade_id)
    for item in items:
        before_qty, after_qty, avg_cost = inv_repo.restore_stock(
            session,
            store_id=store_id,
            product_id=item.product_id,
            quantity=item.quantity,
        )
        inv_repo.write_stock_flow(
            session,
            store_id=store_id,
            product_id=item.product_id,
            flow_type="SALE_RETURN_IN",
            direction=1,
            quantity=item.quantity,
            before_qty=before_qty,
            after_qty=after_qty,
            unit_cost=item.cost_price,
            amount=money(item.cost_price * item.quantity),
            source_type="VOID",
            source_no=trade.trade_no,
            operator=operator_id,
            remark=f"作废：{params.reason}",
        )

    # 会员退款（若有储值支付）
    if trade.member_id:
        payments = repo.list_trade_payments(session, trade_id)
        balance_paid = sum(money(p.amount) for p in payments if p.pay_method == "BALANCE")
        if balance_paid > ZERO:
            member = repo.get_member_for_update(session, trade.member_id)
            if member:
                repo.insert_balance_flow(
                    session,
                    member_id=trade.member_id,
                    flow_type="REFUND",
                    amount=balance_paid,
                    gift_amount=ZERO,
                    direction=1,
                    before_balance=member.balance,
                    after_balance=money(member.balance + balance_paid),
                    before_gift=member.gift_balance,
                    after_gift=member.gift_balance,
                    source_no=trade.trade_no,
                    operator=operator_id,
                    remark="作废退款",
                )
                repo.update_member_balance(
                    session, trade.member_id,
                    balance_delta=balance_paid,
                    gift_delta=ZERO,
                )

    # 更新班次作废金额
    if trade.session_id:
        from sqlalchemy import update as sa_update

        session.execute(
            sa_update(SalSession)
            .where(SalSession.id == trade.session_id)
            .values(void_amount=SalSession.void_amount + trade.receivable)
        )
        session.flush()

    repo.update_trade_status(session, trade_id, "VOID")
    session.commit()
    logger.info(f"作废成功：trade_no={trade.trade_no}, reason={params.reason}")


# ================================================================
# 6.12 POST /sales/trades/{id}/receipt —— 小票
# ================================================================


def generate_receipt(session: Session, trade_id: int) -> ReceiptResult:
    """生成小票（纯文本，spec 6.12）。

    ⚠️ print_count 每次调用 +1 并落库。
    """
    trade = repo.get_trade_by_id(session, trade_id)
    if trade is None:
        raise BusinessError(ErrorCode.TRADE_NOT_FOUND, "销售单不存在")

    items = repo.list_trade_items(session, trade_id)
    payments = repo.list_trade_payments(session, trade_id)
    cashier_name = repo.get_user_real_name(session, trade.cashier_id) or "未知"
    title = str(get_config(session, _Cfg.POS_RECEIPT_TITLE, "社区超市销售管理系统"))

    # 组装纯文本小票
    lines: list[str] = []
    sep = "-" * 32
    lines.append(sep)
    lines.append(f"        {title}")
    lines.append(sep)
    lines.append(f"单号：{trade.trade_no}")
    lines.append(f"时间：{trade.trade_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"收银员：{cashier_name}")
    lines.append(sep)

    for it in items:
        qty_str = f"{it.quantity:.3f}".rstrip("0").rstrip(".")
        lines.append(f"{it.product_name}")
        lines.append(f"  {qty_str} x {it.unit_price:.2f}  = {it.amount:.2f}")
        if it.discount_amount > ZERO:
            lines.append(f"  优惠: -{it.discount_amount:.2f}")

    lines.append(sep)
    lines.append(f"商品合计：  {trade.total_amount:.2f}")
    if trade.discount_amount > ZERO:
        lines.append(f"优惠合计：  -{trade.discount_amount:.2f}")
    if trade.point_deduct > ZERO:
        lines.append(f"积分抵扣：  -{trade.point_deduct:.2f}")
    if trade.round_amount > ZERO:
        lines.append(f"抹零：      -{trade.round_amount:.2f}")
    lines.append(f"应收：      {trade.receivable:.2f}")
    lines.append(f"实收：      {trade.received:.2f}")
    lines.append(f"找零：      {trade.change_amount:.2f}")
    lines.append(sep)

    for p in payments:
        method_names = {
            "CASH": "现金", "WECHAT": "微信", "ALIPAY": "支付宝",
            "CARD": "银行卡", "BALANCE": "储值", "POINTS": "积分", "OTHER": "其他",
        }
        lines.append(f"{method_names.get(p.pay_method, p.pay_method)}：{p.amount:.2f}")

    if trade.member_id:
        from app.models.mem_models import MemMember as MM
        member = session.get(MM, trade.member_id)
        if member:
            lines.append(sep)
            lines.append(f"会员：{member.real_name or member.phone}")
            lines.append(f"卡号：{member.member_no}")

    lines.append(sep)
    lines.append("    [二维码占位]")
    lines.append("  感谢惠顾，欢迎再次光临！")
    lines.append(sep)

    print_data = "\n".join(lines)

    # print_count +1 落库
    new_count = repo.increment_print_count(session, trade_id)
    session.commit()

    return ReceiptResult(print_data=print_data, print_count=new_count)


# ================================================================
# 6.8-6.11 挂单 / 取单
# ================================================================


def create_hold(
    session: Session,
    *,
    pos_id: int,
    store_id: int,
    member_id: int | None,
    cart_json: str,
    item_count: int,
    amount: Decimal,
    operator_id: int,
) -> str:
    """挂单（spec 6.8）。返回 hold_no。"""
    seq = NoSeqService(session)
    hold_no = seq.next_no("GD")
    repo.insert_hold(
        session,
        hold_no=hold_no,
        store_id=store_id,
        pos_id=pos_id,
        member_id=member_id,
        cart_json=cart_json,
        item_count=item_count,
        amount=money(amount),
        operator=operator_id,
        status="HOLDING",
    )
    session.commit()
    return hold_no


def list_holds(session: Session, pos_id: int, store_id: int | None = None) -> list:
    """获取挂单列表（spec 6.9）：只返回 status='HOLDING'。"""
    return repo.list_holds(session, pos_id, store_id)


def resume_hold(session: Session, hold_no: str) -> dict:
    """取单（spec 6.10）：返回详情并置 RESUMED。"""
    hold = repo.get_hold_by_no(session, hold_no)
    if hold is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "挂单不存在")
    if hold.status != "HOLDING":
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"挂单状态异常：{hold.status}")
    repo.update_hold_status(session, hold.id, "RESUMED")
    session.commit()
    return {
        "id": hold.id,
        "hold_no": hold.hold_no,
        "cart_json": hold.cart_json,
        "member_id": hold.member_id,
        "item_count": hold.item_count,
        "amount": hold.amount,
    }


def cancel_hold(session: Session, hold_no: str) -> None:
    """取消挂单（spec 6.11）：⛔ 软删除（置 CANCELED），不物理删。"""
    hold = repo.get_hold_by_no(session, hold_no)
    if hold is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "挂单不存在")
    if hold.status != "HOLDING":
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"挂单状态异常：{hold.status}")
    repo.update_hold_status(session, hold.id, "CANCELED")
    session.commit()


# ================================================================
# 6.13-6.14 班次
# ================================================================


def get_or_open_session(
    session: Session,
    *,
    pos_id: int,
    store_id: int,
    cashier_id: int,
) -> dict:
    """获取当前班次（spec 6.13）。

    ⚠️ 没有则**自动开班**（用 JB 取号，init_cash=0），不返回 null。
    """
    current = repo.get_current_session(session, pos_id)
    if current is not None:
        return _session_to_dict(session, current)

    # 自动开班
    seq = NoSeqService(session)
    session_no = seq.next_no("JB")
    now = _now()
    new_session = repo.insert_session(
        session,
        session_no=session_no,
        store_id=store_id,
        pos_id=pos_id,
        cashier_id=cashier_id,
        open_time=now,
        init_cash=ZERO,
        status="OPEN",
    )
    session.commit()
    logger.info(f"自动开班：session_no={session_no}, pos_id={pos_id}")
    return _session_to_dict(session, new_session)


def close_session(
    session: Session,
    params: SessionCloseRequest,
    *,
    operator_id: int,
) -> dict:
    """交班（spec 6.14）。

    cash_diff = actual_cash − (init_cash + cash_amount)
    cash_diff ≠ 0 且 diff_remark 为空 → 2001
    """
    sal_session = repo.get_session_by_id(session, params.session_id)
    if sal_session is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "班次不存在")
    if sal_session.status != "OPEN":
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "该班次已交班")

    actual_cash = money(params.actual_cash)
    expected_cash = money(sal_session.init_cash + sal_session.cash_amount)
    cash_diff = money(actual_cash - expected_cash)

    if cash_diff != ZERO and not params.diff_remark:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "现金差异不为零时必须填写差异说明")

    repo.update_session_fields(session, sal_session.id, {
        "status": "CLOSED",
        "close_time": _now(),
        "actual_cash": actual_cash,
        "cash_diff": cash_diff,
        "diff_remark": params.diff_remark,
    })
    session.commit()
    logger.info(f"交班成功：session_no={sal_session.session_no}, cash_diff={cash_diff}")

    # 重新读取更新后的对象
    sal_session = repo.get_session_by_id(session, params.session_id)
    return _session_to_dict(session, sal_session)


def _session_to_dict(session: Session, s) -> dict:
    """班次 ORM → 字典（供路由层转 SaleSessionOut）。"""
    from sqlalchemy import select as sa_select

    from app.models.base_models import BasePos

    pos_name = session.execute(
        sa_select(BasePos.pos_name).where(BasePos.id == s.pos_id)
    ).scalar_one_or_none()
    cashier_name = repo.get_user_real_name(session, s.cashier_id)

    return {
        "id": s.id,
        "session_no": s.session_no,
        "store_id": s.store_id,
        "pos_id": s.pos_id,
        "pos_name": pos_name,
        "cashier_id": s.cashier_id,
        "cashier_name": cashier_name,
        "open_time": s.open_time,
        "close_time": s.close_time,
        "init_cash": s.init_cash,
        "sale_amount": s.sale_amount,
        "sale_count": s.sale_count,
        "return_amount": s.return_amount,
        "void_amount": s.void_amount,
        "cash_amount": s.cash_amount,
        "wechat_amount": s.wechat_amount,
        "alipay_amount": s.alipay_amount,
        "card_amount": s.card_amount,
        "balance_amount": s.balance_amount,
        "actual_cash": s.actual_cash,
        "cash_diff": s.cash_diff,
        "diff_remark": s.diff_remark,
        "status": s.status,
    }
