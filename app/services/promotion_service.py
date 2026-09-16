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
