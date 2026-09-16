"""促销引擎（阶段 4 交付物）—— **逐行复刻** `qianduan/src/mock/promotion-engine.ts`。

⚠️ 这是本阶段最关键的模块。前端与后端算出的 `receivable` 必须**一分不差**，
   否则结算校验 `7001` 会让收银台完全不可用（spec 验收 11 生死线）。

⚠️ 与 Mock 的关键差异（spec 三 + 5.2）
------------------------------------
1. **MEMBER 促销不参与计算**（Mock 第 79 行 `.filter(p => p.promo_type !== 'MEMBER')`）
   前端 stores/pos.ts:57 已单独按 mem_level.discount_rate 算了 memberDiscount，
   后端再返回会**重复扣减**（spec 三 ①）。
2. **target_ids 结构差异**：Mock 是静态展开的商品 ID 数组；DB 是 `item_type + target_id`：
   - PRODUCT + target_id > 0：直接匹配商品
   - PRODUCT + target_id = 0：**哨兵值，全场**（FULL_REDUCE / COUPON / MEMBER 用）
   - CATEGORY + target_id：⛔ **必须递归父分类**（商品挂三级、促销挂二级时才能命中）
3. **COMBO 组合价共享**：Mock 的 `p.promo_price` 是单值；DB 的 `pro_promotion_item` 有多行，
   每行都有 `promo_price`（值相同）—— ⛔ **禁止累加**，取第一条即可（spec 5.3 坑）。

⚠️ 金额舍入
----------
全程用 Decimal + ROUND_HALF_UP（`app/utils/money.py::money`），
与前端 `Math.round(n * 100) / 100` 严格一致。⛔ 不用 Python 内置 `round()`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories import promotion_repository as repo
from app.repositories.promotion_repository import PromotionWithItems
from app.utils.money import ZERO, money, money_max_zero

# ---------- 数据结构 ----------


@dataclass
class EngineItem:
    """促销引擎输入项（对应 Mock 的 `EngineItem`）。

    Attributes:
        product_id: 商品 ID。
        quantity: 数量（Decimal，支持称重商品）。
        unit_price: 成交单价（Decimal）。若为 None 或 ≤0，回退到商品档案 sale_price。
        category_id: 商品所属分类 ID（用于 CATEGORY 类型促销的递归匹配）。
                    若为 None，引擎内部会自动从商品档案读取。
    """

    product_id: int
    quantity: Decimal
    unit_price: Decimal | None = None
    category_id: int | None = None


@dataclass
class PromoDetail:
    """单条促销命中详情（对应 Mock 的 `PromoDetail`）。"""

    promo_id: int
    promo_name: str
    promo_type: str
    amount: Decimal
    desc: str


@dataclass
class CalcResult:
    """促销试算结果（对应 Mock 的 `CalcResult`）。

    ⚠️ `discount_amount` **不含会员折扣、不含积分抵扣**（spec 5.4）。
       会员折扣由 sale_service 在结算时单独算；calc 接口只返促销优惠。
    """

    total_amount: Decimal
    discount_amount: Decimal
    point_deduct: Decimal
    receivable: Decimal
    details: list[PromoDetail] = field(default_factory=list)


# ---------- 内部工具 ----------


def _price_of(item: EngineItem, product_fallback: dict[int, Decimal]) -> Decimal:
    """商品成交单价：优先取购物车传入价，否则取商品档案 sale_price。

    对应 Mock 的 `priceOf(item)` 函数。
    """
    if item.unit_price is not None and item.unit_price > 0:
        return item.unit_price
    return product_fallback.get(item.product_id, ZERO)


def _in_period(promo: PromotionWithItems, now: datetime) -> bool:
    """判断促销是否在生效期（复刻 Mock 的 `inPeriod`）。

    三段校验：日期区间 + 星期 + 时段。

    ⚠️ 星期判断的兼容性（spec 5.1）：
    - 用 `date.isoweekday()`（周一=1 … 周日=7），与 DB 注释「5,6 表示周五周六」一致
    - **兼容 JS 风格的 0=周日**：若配置里出现 '0'，视为周日（isoweekday=7）
    - 目前 DB 数据里只有 '3'（周三），但兼容逻辑要写上避免后续踩坑

    ⚠️ 时段判断（spec 5.1）：
    - Mock 有 `p.start_time !== '00:00'` 的奇怪条件（Mock 瑕疵，不照抄）
    - 正确逻辑：start_time/end_time 为 None，或 (00:00:00, 23:59:59) 时视为**全天**
    """
    # 1. 日期区间（字符串比较 ISO 格式即可）
    today: date = now.date()
    if today < promo.start_date or today > promo.end_date:
        return False

    # 2. 星期
    if promo.week_days:
        weeks = [s.strip() for s in promo.week_days.split(",") if s.strip()]
        if weeks:
            iso_wd = today.isoweekday()  # 周一=1 ... 周日=7
            # 兼容 JS 风格：'0' 视为周日（isoweekday=7）
            normalized = {(7 if w == "0" else int(w)) for w in weeks if w.isdigit()}
            if iso_wd not in normalized:
                return False

    # 3. 时段
    if promo.start_time is not None and promo.end_time is not None:
        # 全天：00:00:00 ~ 23:59:59（或 00:00 ~ 23:59）
        is_full_day = (
            promo.start_time <= time(0, 0, 0)
            and promo.end_time >= time(23, 59, 59)
        )
        if not is_full_day:
            now_time = now.time()
            if now_time < promo.start_time or now_time > promo.end_time:
                return False

    return True


def _match_items_by_promotion(
    promo: PromotionWithItems,
    items: list[EngineItem],
    ancestors: dict[int, set[int]],
) -> tuple[list[tuple[EngineItem, Decimal | None]], bool]:
    """找出被此促销命中的购物车项，并返回每项对应的 promo_price/discount_rate（若有）。

    Returns:
        (matched, has_sentinel) 元组：
        - matched: [(EngineItem, promo_price or discount_rate or None)] 列表
        - has_sentinel: 是否含 target_id=0 的哨兵（表示"全场"，FULL_REDUCE/COUPON 用）
    """
    matched: list[tuple[EngineItem, Decimal | None]] = []
    has_sentinel = False

    # 建索引：PRODUCT target_id → item；CATEGORY target_id → item
    product_targets: dict[int, Decimal | None] = {}  # target_id → promo_price 或 discount_rate
    category_targets: dict[int, Decimal | None] = {}

    for pi in promo.items:
        if pi.item_type == "PRODUCT":
            if pi.target_id == 0:
                has_sentinel = True
            else:
                # SPECIAL 用 promo_price；DISCOUNT 用 discount_rate；COMBO 用 promo_price
                product_targets[pi.target_id] = (
                    pi.promo_price if pi.promo_price is not None else pi.discount_rate
                )
        elif pi.item_type == "CATEGORY":
            category_targets[pi.target_id] = pi.discount_rate

    for it in items:
        # 1. PRODUCT 直接匹配
        if it.product_id in product_targets:
            matched.append((it, product_targets[it.product_id]))
            continue
        # 2. CATEGORY 递归匹配（用祖先链）
        if category_targets and it.category_id is not None:
            item_ancestors = ancestors.get(it.category_id, {it.category_id})
            for cat_id, rate in category_targets.items():
                if cat_id in item_ancestors:
                    matched.append((it, rate))
                    break  # 一个商品只匹配一次

    return matched, has_sentinel


# ---------- 主计算函数 ----------


def calc_promotion(
    session: Session,
    items: list[EngineItem],
    *,
    is_member: bool = False,
    now: datetime | None = None,
) -> CalcResult:
    """计算购物车命中的促销（**逐行复刻** Mock 的 `calcPromotionForItems`）。

    Args:
        session: 数据库会话（用于读促销、分类、商品档案）。
        items: 购物车项列表。
        is_member: 是否已识别会员（用于 is_member_only 过滤）。
        now: 当前时间（可注入便于测试跨时段促销）；默认 datetime.now()。

    Returns:
        CalcResult(total_amount, discount_amount, point_deduct=0, receivable, details)。

    ⚠️ point_deduct 恒为 0 —— calc 接口不处理积分（spec 5.4）。
    ⚠️ discount_amount **不含会员折扣** —— 会员折扣由 sale_service 结算时单独算。
    """
    if now is None:
        from zoneinfo import ZoneInfo

        from app.core.config import settings
        now = datetime.now(ZoneInfo(settings.TZ))

    if not items:
        return CalcResult(
            total_amount=ZERO, discount_amount=ZERO,
            point_deduct=ZERO, receivable=ZERO, details=[],
        )

    # 1. 补齐 EngineItem 的 category_id（若调用方未传）
    missing_cat_ids = [it.product_id for it in items if it.category_id is None]
    product_map: dict[int, Decimal] = {}  # product_id → sale_price 兜底
    if missing_cat_ids:
        products = repo.get_products_by_ids(session, missing_cat_ids)
        for it in items:
            if it.category_id is None and it.product_id in products:
                it.category_id = products[it.product_id].category_id
        # 顺便建立 sale_price 兜底表
        product_map = {pid: p.sale_price for pid, p in products.items()}
    # 对所有商品都建 sale_price 兜底（可能调用方传了 unit_price=None）
    all_pids = list({it.product_id for it in items if it.unit_price is None or it.unit_price <= 0})
    if all_pids:
        extra = repo.get_products_by_ids(session, all_pids)
        for pid, p in extra.items():
            product_map.setdefault(pid, p.sale_price)

    # 2. 计算 totalAmount（对应 Mock 第 70 行）
    total_amount = money(sum(
        (_price_of(it, product_map) * it.quantity for it in items),
        ZERO,
    ))

    # 3. 一次性读促销 + 分类祖先映射（避免 N+1）
    promotions = repo.list_running_promotions(session)
    ancestors = repo.build_category_ancestor_map(session)

    # 4. 候选筛选（复刻 Mock 第 77-82 行）
    candidates: list[PromotionWithItems] = []
    for p in promotions:
        # status == 'RUNNING'（repo 已过滤，此处兜底）
        if p.status != "RUNNING":
            continue
        # ⛔ 排除 MEMBER（spec 三 ①，Mock 第 79 行）
        if p.promo_type == "MEMBER":
            continue
        # 有效期
        if not _in_period(p, now):
            continue
        # 会员专享过滤
        if p.is_member_only == 1 and not is_member:
            continue
        candidates.append(p)
    # 已在 repo 里按 priority 排序，此处保持稳定

    # 5. 主循环（复刻 Mock 第 84-149 行）
    details: list[PromoDetail] = []
    accumulated = ZERO
    exclusive_used = False

    for p in candidates:
        # 互斥逻辑：不可叠加 + 已有不可叠加生效 → 跳过
        if p.is_stackable == 0 and exclusive_used:
            continue

        # 满减基数 = totalAmount - accumulated（spec 5.3 与验收 7）
        base = money(total_amount - accumulated)

        amount = ZERO
        desc = ""

        matched, has_sentinel = _match_items_by_promotion(p, items, ancestors)

        if p.promo_type == "SPECIAL":
            # 对每个命中商品：(priceOf - promo_price) × quantity 累加
            if not matched:
                continue
            sub_total = ZERO
            for it, promo_price in matched:
                if promo_price is None:
                    continue
                up = _price_of(it, product_map)
                if up > promo_price:
                    sub_total += (up - promo_price) * it.quantity
            amount = money(sub_total)
            if amount <= ZERO:
                continue
            # desc 用第一条明细的 promo_price（Mock 里 p.promo_price 是单值）
            first_price = next(
                (pp for _, pp in matched if pp is not None), ZERO,
            )
            desc = f"指定商品特价 ¥{first_price}"

        elif p.promo_type == "DISCOUNT":
            if not matched:
                continue
            # ⚠️ 每条明细可能有不同 discount_rate（虽然演示数据里都一样）
            # 按 rate 分组分别计算，防止混合 rate 时算错
            sub_total = ZERO
            rate_for_desc: Decimal | None = None
            for it, rate in matched:
                if rate is None:
                    continue
                sub_total += _price_of(it, product_map) * it.quantity * (Decimal(1) - rate)
                if rate_for_desc is None:
                    rate_for_desc = rate
            amount = money(sub_total)
            if amount <= ZERO:
                continue
            # desc：`指定商品 X.X 折`（rate * 10 保留 1 位小数）
            if rate_for_desc is not None:
                discount_display = (rate_for_desc * Decimal(10)).quantize(Decimal("0.1"))
                desc = f"指定商品 {discount_display} 折"
            else:
                desc = "指定商品折扣"

        elif p.promo_type == "COMBO":
            # ⛔ 组合内商品**必须全部在购物车中**，否则跳过
            combo_product_ids = {
                pi.target_id for pi in p.items
                if pi.item_type == "PRODUCT" and pi.target_id > 0
            }
            if not combo_product_ids:
                continue
            cart_pids = {it.product_id for it in items}
            if not combo_product_ids.issubset(cart_pids):
                continue
            # ⚠️ 组合价：**取第一条明细的 promo_price**，禁止累加（spec 5.3 坑）
            # 同一促销的 COMBO 明细共享同一个组合价（如 promo_id=3 的两条都是 11.50）
            combo_price = next(
                (pi.promo_price for pi in p.items
                 if pi.item_type == "PRODUCT" and pi.promo_price is not None),
                None,
            )
            if combo_price is None:
                continue
            # 组合内商品小计
            sub_total = ZERO
            for it in items:
                if it.product_id in combo_product_ids:
                    sub_total += _price_of(it, product_map) * it.quantity
            sub_total = money(sub_total)
            amount = money_max_zero(sub_total - combo_price)
            if amount <= ZERO:
                continue
            desc = f"组合价 ¥{combo_price}"

        elif p.promo_type in ("FULL_REDUCE", "COUPON"):
            # 从 target_id=0 的哨兵明细取 threshold/reduce
            sentinel = next(
                (pi for pi in p.items
                 if pi.item_type == "PRODUCT" and pi.target_id == 0),
                None,
            )
            if (
                sentinel is None
                or sentinel.threshold_amount is None
                or sentinel.reduce_amount is None
            ):
                continue
            # ⚠️ base 已扣除 accumulated（验收 7 关键）
            if base < sentinel.threshold_amount:
                continue
            amount = money(sentinel.reduce_amount)
            desc = f"满 ¥{sentinel.threshold_amount} 减 ¥{sentinel.reduce_amount}"

        elif p.promo_type == "FULL_GIFT":
            # 数据结构支持但演示数据里没有；实现为记录赠品行、amount=0（spec 5.3）
            # 目前无实际数据，跳过；若后续接入需在此写赠品逻辑
            continue

        else:
            # 未知类型（包括 MEMBER，虽然前面已过滤）
            continue

        details.append(PromoDetail(
            promo_id=p.id,
            promo_name=p.promo_name,
            promo_type=p.promo_type,
            amount=amount,
            desc=desc,
        ))
        accumulated = money(accumulated + amount)
        if p.is_stackable == 0:
            exclusive_used = True

    # 6. 汇总（复刻 Mock 第 151-159 行）
    discount_amount = money(sum((d.amount for d in details), ZERO))
    receivable = money_max_zero(total_amount - discount_amount)

    return CalcResult(
        total_amount=total_amount,
        discount_amount=discount_amount,
        point_deduct=ZERO,  # calc 接口不处理积分（spec 5.4）
        receivable=receivable,
        details=details,
    )


# ---------- 会员折扣（结算专用，calc 不用） ----------


def calc_member_discount(
    total_amount: Decimal,
    item_discount: Decimal,
    promo_discount: Decimal,
    member_discount_rate: Decimal,
) -> Decimal:
    """计算会员折扣（**复刻 stores/pos.ts:57-62**）。

    ⚠️ 关键约定（spec 三 ②）：
    - 会员折扣率来自 `mem_member.level_id → mem_level.discount_rate`
    - **calc 接口不返回**会员折扣（避免与前端 memberDiscount 重复展示）
    - **结算接口必须自己算**（否则与前端算的 receivable 对不上 → 7001）

    公式（与前端 stores/pos.ts:57-62 逐字对齐）：
        rate >= 1 → 0
        base = round2(totalAmount - itemDiscount - promoDiscount)
        memberDiscount = round2(base × (1 - rate))

    Args:
        total_amount: 商品总金额（优惠前）。
        item_discount: 行级优惠合计（前端 CartItem.discount_amount 之和）。
        promo_discount: 促销优惠合计（calc_promotion 返回的 discount_amount）。
        member_discount_rate: 会员折扣率（Decimal，如 Decimal("0.9500") 表示 95 折）。

    Returns:
        会员折扣金额（Decimal，量化到分）；rate>=1 时返回 0。
    """
    if member_discount_rate >= Decimal(1):
        return ZERO
    base = money(total_amount - item_discount - promo_discount)
    return money(base * (Decimal(1) - member_discount_rate))


# ================================================================
# 阶段 7：促销 CRUD + 试算 + 效果
# ================================================================

from app.core.error_codes import ErrorCode  # noqa: E402
from app.core.exceptions import BusinessError  # noqa: E402
from app.core.logging import get_logger  # noqa: E402
from app.repositories.auth_repository import write_operation_log  # noqa: E402
from app.schemas.promotion import (  # noqa: E402
    ComparedPoint,
    Promotion,
    PromotionCreate,
    PromotionItem,
    PromotionItemIn,
    PromotionStatusUpdate,
    PromotionUpdate,
    RuleFromPromoRequest,
    SimulateDetail,
    SimulateRequest,
    SimulateResult,
)
from app.utils import category_tree  # noqa: E402

_logger = get_logger(__name__)

# 优先级缺省映射（与前端 edit.vue:52-60 priorityHint 一致）
PRIORITY_HINT = {
    "SPECIAL": 1, "DISCOUNT": 2, "COMBO": 3, "FULL_REDUCE": 4,
    "FULL_GIFT": 5, "COUPON": 6, "MEMBER": 7,
}
VALID_PROMO_TYPES = set(PRIORITY_HINT.keys())
# 全场类促销（不选商品，需构造 target_id=0 哨兵明细）
FULL_STORE_TYPES = {"FULL_REDUCE", "FULL_GIFT", "COUPON", "MEMBER"}


def _convert_time_str(raw: str | None) -> time | None:
    """前端时间字符串 → time 或 None（spec 三：'' → NULL，'HH:mm' → HH:mm:00）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None  # ⛔ 空串 → NULL（不能塞 TIME 列）
    # 支持 'HH:mm' 或 'HH:mm:ss'
    parts = s.split(":")
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        ss = int(parts[2]) if len(parts) > 2 else 0
        return time(hh, mm, ss)
    except (ValueError, IndexError):
        return None


def _time_to_str(t: time | None) -> str | None:
    """time → 'HH:mm:ss' 字符串（响应用）。"""
    return t.strftime("%H:%M:%S") if t else None


def _build_promotion_items(
    promo_type: str,
    items: list[PromotionItemIn] | None,
    top: PromotionCreate | PromotionUpdate,
) -> list[dict]:
    """构造促销明细（spec 三，**生死线**）。

    规则：
    - items 非空 → 以 items 为准逐条落库
    - items 为空 且 类型∈全场类(FULL_REDUCE/FULL_GIFT/COUPON/MEMBER)
      → ⛔ 用顶层字段构造一条 target_id=0 全场哨兵明细
    - items 为空 且 商品类(SPECIAL/DISCOUNT/COMBO) → 2001
    """
    result: list[dict] = []
    if items:
        for it in items:
            result.append({
                "item_type": it.item_type or "PRODUCT",
                "target_id": it.target_id,
                "promo_price": it.promo_price,
                "discount_rate": it.discount_rate,
                "threshold_amount": it.threshold_amount,
                "reduce_amount": it.reduce_amount,
                "gift_product_id": it.gift_product_id,
                "gift_qty": it.gift_qty,
                "combo_qty": it.combo_qty,
            })
        return result

    # items 为空
    if promo_type in FULL_STORE_TYPES:
        # ⛔ 全场哨兵：target_id=0，规则值取顶层字段
        return [{
            "item_type": "PRODUCT",
            "target_id": 0,
            "promo_price": getattr(top, "promo_price", None),
            "discount_rate": getattr(top, "discount_rate", None),
            "threshold_amount": getattr(top, "threshold_amount", None),
            "reduce_amount": getattr(top, "reduce_amount", None),
            "gift_product_id": getattr(top, "gift_product_id", None),
            "gift_qty": getattr(top, "gift_qty", None),
            "combo_qty": None,
        }]

    # 商品类但无明细 → 2001
    raise BusinessError(ErrorCode.REQUIRED_MISSING, f"{promo_type} 类型促销必须选择适用商品")


def _validate_promotion_common(
    promo_name: str | None, promo_type: str, start_date: date, end_date: date,
) -> None:
    if not promo_name or not promo_name.strip():
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "促销名称不能为空")
    if promo_type not in VALID_PROMO_TYPES:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"非法促销类型：{promo_type}")
    if start_date > end_date:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "开始日期不能晚于结束日期")


def create_promotion(
    session: Session, params: PromotionCreate, *, operator_id: int,
) -> Promotion:
    """创建促销（spec 5.17）。

    ⚠️ priority 缺省按类型自动填；MEMBER 强制 is_member_only=1；status 缺省 DRAFT。
    ⚠️ 全场类空 items → 构造 target_id=0 明细（生死线）。
    """
    _validate_promotion_common(params.promo_name, params.promo_type,
                               params.start_date, params.end_date)

    # 明细构造（可能抛 2001）
    item_rows = _build_promotion_items(params.promo_type, params.items, params)

    priority = params.priority if params.priority is not None else PRIORITY_HINT[params.promo_type]
    # MEMBER 强制会员专属
    is_member_only = 1 if params.promo_type == "MEMBER" else params.is_member_only
    status = params.status or "DRAFT"

    promo = repo.insert_promotion(
        session,
        promo_name=params.promo_name,
        promo_type=params.promo_type,
        priority=priority,
        start_date=params.start_date,
        end_date=params.end_date,
        week_days=params.week_days or None,
        start_time=_convert_time_str(params.start_time),
        end_time=_convert_time_str(params.end_time),
        is_member_only=is_member_only,
        is_stackable=params.is_stackable,
        trigger_count=0,
        discount_total=ZERO,
        status=status,
        created_by=operator_id,
        remark=params.remark,
    )
    for row in item_rows:
        row["promo_id"] = promo.id
    repo.insert_promotion_items_batch(session, item_rows)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="促销", action="创建促销",
        target_type="pro_promotion", target_id=promo.id,
        after_value={"promo_name": params.promo_name, "promo_type": params.promo_type},
        result=1,
    )
    session.commit()
    _logger.info(f"创建促销：id={promo.id}, name={params.promo_name}, 明细{len(item_rows)}条")
    return get_promotion_detail(session, promo.id)


def update_promotion(
    session: Session, promo_id: int, params: PromotionUpdate, *, operator_id: int,
) -> Promotion:
    """修改促销（spec 5.18）：明细全量替换。

    ⚠️ trigger_count>0 时：只允许改名称/有效期/备注，改规则→2008。
    """
    promo = repo.get_promotion_by_id(session, promo_id)
    if promo is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"促销 id={promo_id} 不存在")

    # 判断是否“改规则”（传了 items 或规则快捷字段或 target_ids）
    changes_rule = (
        params.items is not None
        or params.target_ids is not None
        or params.promo_price is not None
        or params.discount_rate is not None
        or params.threshold_amount is not None
        or params.reduce_amount is not None
        or params.promo_type is not None
    )
    if promo.trigger_count > 0 and changes_rule:
        raise BusinessError(
            ErrorCode.PROMOTION_HAS_TRIGGER,
            "该促销已有触发记录，不允许修改规则（可改名称/有效期/备注或改为停用）",
        )

    promo_type = params.promo_type or promo.promo_type
    # 主表字段更新
    values: dict = {}
    if params.promo_name is not None:
        values["promo_name"] = params.promo_name
    if params.promo_type is not None:
        values["promo_type"] = params.promo_type
    if params.priority is not None:
        values["priority"] = params.priority
    if params.start_date is not None:
        values["start_date"] = params.start_date
    if params.end_date is not None:
        values["end_date"] = params.end_date
    if params.week_days is not None:
        values["week_days"] = params.week_days or None
    if params.start_time is not None:
        values["start_time"] = _convert_time_str(params.start_time)
    if params.end_time is not None:
        values["end_time"] = _convert_time_str(params.end_time)
    if params.is_member_only is not None:
        values["is_member_only"] = 1 if promo_type == "MEMBER" else params.is_member_only
    if params.is_stackable is not None:
        values["is_stackable"] = params.is_stackable
    if params.remark is not None:
        values["remark"] = params.remark

    # 日期校验（若两者都有）
    sd = params.start_date or promo.start_date
    ed = params.end_date or promo.end_date
    if sd > ed:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "开始日期不能晚于结束日期")

    # 明细全量替换（仅当传了 items/规则字段时）
    if changes_rule:
        merged = PromotionCreate(
            promo_name=params.promo_name or promo.promo_name,
            promo_type=promo_type,
            start_date=sd, end_date=ed,
            promo_price=params.promo_price,
            discount_rate=params.discount_rate,
            threshold_amount=params.threshold_amount,
            reduce_amount=params.reduce_amount,
            gift_product_id=params.gift_product_id,
            gift_qty=params.gift_qty,
            items=params.items,
        )
        item_rows = _build_promotion_items(promo_type, params.items, merged)
        repo.delete_promotion_items(session, promo_id)
        for row in item_rows:
            row["promo_id"] = promo_id
        repo.insert_promotion_items_batch(session, item_rows)

    repo.update_promotion(session, promo_id, values)
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="促销", action="修改促销",
        target_type="pro_promotion", target_id=promo_id,
        after_value={"changed_fields": list(values.keys()), "rule_changed": changes_rule},
        result=1,
    )
    session.commit()
    return get_promotion_detail(session, promo_id)


def update_promotion_status(
    session: Session, promo_id: int, params: PromotionStatusUpdate, *, operator_id: int,
) -> None:
    """启停促销（spec 5.19）：status ∈ {RUNNING, STOPPED}。"""
    promo = repo.get_promotion_by_id(session, promo_id)
    if promo is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"促销 id={promo_id} 不存在")
    if params.status not in ("RUNNING", "STOPPED"):
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            f"非法状态：{params.status}（只 RUNNING/STOPPED）",
        )
    repo.update_promotion(session, promo_id, {"status": params.status})
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="促销", action="启停促销",
        target_type="pro_promotion", target_id=promo_id,
        after_value={"status": params.status}, result=1,
    )
    session.commit()


def delete_promotion(session: Session, promo_id: int, *, operator_id: int) -> None:
    """删除促销（spec 5.20）：trigger_count>0 → 2008（请改为停用）。"""
    promo = repo.get_promotion_by_id(session, promo_id)
    if promo is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"促销 id={promo_id} 不存在")
    if promo.trigger_count > 0:
        raise BusinessError(
            ErrorCode.PROMOTION_HAS_TRIGGER,
            f"该促销已触发 {promo.trigger_count} 次，不可删除，请改为停用",
        )
    repo.delete_promotion(session, promo_id)
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="促销", action="删除促销",
        target_type="pro_promotion", target_id=promo_id,
        before_value={"promo_name": promo.promo_name}, result=1,
    )
    session.commit()


def _calc_product_count(session: Session, promo_id: int, items: list) -> int:
    """计算促销覆盖商品数（spec 冲突表 ④）。

    - PRODUCT + target_id=0（全场）：全部商品数
    - PRODUCT + target_id>0：明细条数
    - CATEGORY：分类及子分类下商品数（递归）
    """
    if not items:
        return 0
    # 全场哨兵
    if any(it.item_type == "PRODUCT" and it.target_id == 0 for it in items):
        return category_tree.count_all_products(session)
    # CATEGORY：展开子分类
    cat_ids = [it.target_id for it in items if it.item_type == "CATEGORY"]
    product_ids = [it.target_id for it in items if it.item_type == "PRODUCT" and it.target_id > 0]
    count = len(set(product_ids))
    if cat_ids:
        count += category_tree.count_products_in_categories(session, cat_ids)
    return count


def list_promotions(
    session: Session, *, page: int = 1, page_size: int = 20,
    keyword: str | None = None, status: str | None = None,
) -> tuple[list[Promotion], int]:
    """促销列表（spec 5.15，含 product_count）。"""
    rows, total = repo.list_promotions(
        session, page=page, page_size=page_size, keyword=keyword, status=status,
    )
    result = []
    for promo, created_by_name in rows:
        items = repo.list_promotion_items(session, promo.id)
        result.append(Promotion(
            id=promo.id,
            promo_name=promo.promo_name,
            promo_type=promo.promo_type,
            priority=promo.priority,
            start_date=promo.start_date,
            end_date=promo.end_date,
            week_days=promo.week_days,
            start_time=_time_to_str(promo.start_time),
            end_time=_time_to_str(promo.end_time),
            is_member_only=promo.is_member_only,
            is_stackable=promo.is_stackable,
            trigger_count=promo.trigger_count,
            discount_total=promo.discount_total,
            status=promo.status,
            created_by_name=created_by_name,
            remark=promo.remark,
            product_count=_calc_product_count(session, promo.id, items),
        ))
    return result, total


def get_promotion_detail(session: Session, promo_id: int) -> Promotion:
    """促销详情（含 items，spec 5.16）。"""
    promo = repo.get_promotion_by_id(session, promo_id)
    if promo is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"促销 id={promo_id} 不存在")
    from app.repositories.sale_repository import get_user_real_name
    created_by_name = get_user_real_name(session, promo.created_by)
    items = repo.list_promotion_items(session, promo_id)
    return Promotion(
        id=promo.id,
        promo_name=promo.promo_name,
        promo_type=promo.promo_type,
        priority=promo.priority,
        start_date=promo.start_date,
        end_date=promo.end_date,
        week_days=promo.week_days,
        start_time=_time_to_str(promo.start_time),
        end_time=_time_to_str(promo.end_time),
        is_member_only=promo.is_member_only,
        is_stackable=promo.is_stackable,
        trigger_count=promo.trigger_count,
        discount_total=promo.discount_total,
        status=promo.status,
        created_by_name=created_by_name,
        remark=promo.remark,
        product_count=_calc_product_count(session, promo_id, items),
        items=[
            PromotionItem(
                id=it.id, promo_id=it.promo_id, item_type=it.item_type,
                target_id=it.target_id, promo_price=it.promo_price,
                discount_rate=it.discount_rate, threshold_amount=it.threshold_amount,
                reduce_amount=it.reduce_amount, gift_product_id=it.gift_product_id,
                gift_qty=it.gift_qty, combo_qty=it.combo_qty,
            )
            for it in items
        ],
    )


def simulate(
    session: Session, params: SimulateRequest, *, now: datetime | None = None,
) -> SimulateResult:
    """促销试算（spec 5.21）：复用引擎，不传价格（取 sale_price），排除 MEMBER。"""
    # 取商品 sale_price
    products = repo.get_products_by_ids(session, [it.product_id for it in params.items])
    engine_items = [
        EngineItem(
            product_id=it.product_id,
            quantity=it.quantity,
            unit_price=products[it.product_id].sale_price if it.product_id in products else None,
        )
        for it in params.items
    ]
    is_member = False
    if params.member_id:
        from app.models.mem_models import MemMember
        m = session.get(MemMember, params.member_id)
        is_member = m is not None and m.status == 1

    result = calc_promotion(session, engine_items, is_member=is_member, now=now)
    return SimulateResult(
        total_amount=result.total_amount,
        discount_amount=result.discount_amount,
        receivable=result.receivable,
        details=[
            SimulateDetail(
                promo_id=d.promo_id, promo_name=d.promo_name,
                promo_type=d.promo_type, amount=d.amount, desc=d.desc,
            )
            for d in result.details
        ],
    )


def get_promotion_effect(session: Session, promo_id: int) -> dict:
    """促销效果分析（spec 5.22）：trigger_count/discount_total + 销售汇总 + compared 估算。

    ⚠️ compared 是**估算口径**（非严格 A/B）：with_promo=当天命中该促销销售额，
       without_promo=当天未命中该促销销售额（基线）。
    ⚠️ 命中判定：sal_trade_item.promo_id = :id（阶段4结算时写入）。
    """
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.sal_models import SalTrade, SalTradeItem

    promo = repo.get_promotion_by_id(session, promo_id)
    if promo is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"促销 id={promo_id} 不存在")

    # 命中该促销的 trade_id（去重），再汇总销售额/毛利（只算非VOID）
    trade_ids = list(session.execute(
        sa_select(SalTradeItem.trade_id)
        .where(SalTradeItem.promo_id == promo_id)
        .group_by(SalTradeItem.trade_id)
    ).scalars().all())
    sale_amount = ZERO
    gross_profit = ZERO
    if trade_ids:
        agg = session.execute(
            sa_select(
                func.coalesce(func.sum(SalTrade.receivable), 0),
                func.coalesce(func.sum(SalTrade.gross_profit), 0),
            ).where(SalTrade.id.in_(trade_ids), SalTrade.status != "VOID")
        ).fetchone()
        sale_amount = Decimal(agg[0] or 0)
        gross_profit = Decimal(agg[1] or 0)

    return {
        "promo_id": promo_id,
        "trigger_count": promo.trigger_count,
        "discount_total": promo.discount_total,
        "sale_amount": money(sale_amount),
        "gross_profit": money(gross_profit),
        "compared": _build_compared(session, promo_id),
    }


def _build_compared(session: Session, promo_id: int) -> list[ComparedPoint]:
    """构造 compared 数组（估算：按日分组，命中 vs 未命中）。"""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.sal_models import SalTrade, SalTradeItem

    # 命中该促销的 trade_id
    hit_ids = set(session.execute(
        sa_select(SalTradeItem.trade_id).where(SalTradeItem.promo_id == promo_id)
    ).scalars().all())
    if not hit_ids:
        return []
    # 按日分组：with_promo=命中当天销售额，without_promo=未命中当天销售额

    rows = session.execute(
        sa_select(
            func.date(SalTrade.trade_time).label("d"),
            SalTrade.id,
            SalTrade.receivable,
        ).where(SalTrade.status != "VOID")
    ).all()
    by_day: dict[str, list] = {}
    for d, tid, receivable in rows:
        key = str(d)
        by_day.setdefault(key, {"with": ZERO, "without": ZERO})
        if tid in hit_ids:
            by_day[key]["with"] += receivable
        else:
            by_day[key]["without"] += receivable
    return [
        ComparedPoint(date=day, with_promo=money(v["with"]), without_promo=money(v["without"]))
        for day, v in sorted(by_day.items())
    ]


def create_from_rule(
    session: Session, params: RuleFromPromoRequest, *, operator_id: int,
) -> None:
    """由关联规则生成促销（spec 5.25，骨架）。

    ⛔ bi_rule 是阶段9的表，不存在 → 返回业务错误（非500），标注待阶段9联调。
    """

    # 检查 bi_rule 表是否存在
    try:
        from app.models import bi_models  # noqa: F401
        has_bi = hasattr(bi_models, "BiRule")
    except (ImportError, AttributeError):
        has_bi = False
    if not has_bi:
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            "关联规则模块（bi_rule）未就绪，待阶段 9 联调",
        )
    # 阶段 9 落地后补：查 rule_id → 左右项作为 COMBO target_id → adopted=1
    raise BusinessError(
        ErrorCode.REQUIRED_MISSING, "关联规则生成促销待阶段 9 实现"
    )
