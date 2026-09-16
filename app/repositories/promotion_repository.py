"""促销数据访问层（阶段 4 交付物）。

⚠️ 关键设计（spec 5.2）
----------------------
Mock 的 `target_ids` 是**商品 ID 数组**（已静态展开），但真实 DB 是 `pro_promotion_item`
表，用 `item_type` + `target_id` 表示：

| item_type | target_id | 匹配规则 |
|-----------|-----------|----------|
| PRODUCT   | > 0       | 商品 ID 直接匹配 |
| PRODUCT   | **0**     | **哨兵值**，表示全场（用于满减/券/会员等级折扣）|
| CATEGORY  | 分类 ID   | ⛔ **必须向上递归父分类** |

⛔ 递归父分类是必须的：促销 2 适用分类 11/12/13（二级），但商品「有机小番茄」挂在
三级分类 111（其父是 11）。只做直接匹配会漏掉这些商品。

⚠️ 性能：**一次性把 18 条分类读进内存**建 child→parent 映射，避免每个商品查一次库（N+1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prd_models import PrdCategory, PrdProduct
from app.models.pro_models import ProPromotion, ProPromotionItem


@dataclass
class PromotionItem:
    """促销明细（对应 pro_promotion_item 一行）。"""

    item_type: str  # 'PRODUCT' / 'CATEGORY'
    target_id: int  # 商品 ID / 分类 ID / 0（哨兵，全场）
    promo_price: Decimal | None = None       # SPECIAL / COMBO 用
    discount_rate: Decimal | None = None     # DISCOUNT 用
    threshold_amount: Decimal | None = None  # FULL_REDUCE / COUPON 用
    reduce_amount: Decimal | None = None     # FULL_REDUCE / COUPON 用
    gift_product_id: int | None = None       # FULL_GIFT 用
    gift_qty: Decimal | None = None
    combo_qty: Decimal | None = None         # COMBO 用


@dataclass
class PromotionWithItems:
    """促销 + 明细的聚合视图（供促销引擎使用）。"""

    id: int
    promo_name: str
    promo_type: str
    priority: int
    start_date: date
    end_date: date
    week_days: str | None
    start_time: time | None
    end_time: time | None
    is_member_only: int
    is_stackable: int
    status: str
    items: list[PromotionItem] = field(default_factory=list)


def list_running_promotions(session: Session) -> list[PromotionWithItems]:
    """读取所有 status='RUNNING' 的促销及其明细。

    ⚠️ 一次性 JOIN 查完，避免 N+1。按 (priority, id) 排序保证结果稳定。

    Returns:
        PromotionWithItems 列表，按 priority 升序（同 priority 按 id 升序）。
    """
    # 一次查出所有 RUNNING 促销 + 明细
    stmt = (
        select(ProPromotion, ProPromotionItem)
        .outerjoin(
            ProPromotionItem,
            ProPromotionItem.promo_id == ProPromotion.id,
        )
        .where(ProPromotion.status == "RUNNING")
        .order_by(ProPromotion.priority.asc(), ProPromotion.id.asc(), ProPromotionItem.id.asc())
    )
    rows = session.execute(stmt).all()

    # 按 promo_id 分组
    grouped: dict[int, PromotionWithItems] = {}
    for promo, item in rows:
        if promo.id not in grouped:
            grouped[promo.id] = PromotionWithItems(
                id=promo.id,
                promo_name=promo.promo_name,
                promo_type=promo.promo_type,
                priority=promo.priority,
                start_date=promo.start_date,
                end_date=promo.end_date,
                week_days=promo.week_days,
                start_time=promo.start_time,
                end_time=promo.end_time,
                is_member_only=promo.is_member_only,
                is_stackable=promo.is_stackable,
                status=promo.status,
            )
        if item is not None:
            grouped[promo.id].items.append(PromotionItem(
                item_type=item.item_type,
                target_id=item.target_id,
                promo_price=item.promo_price,
                discount_rate=item.discount_rate,
                threshold_amount=item.threshold_amount,
                reduce_amount=item.reduce_amount,
                gift_product_id=item.gift_product_id,
                gift_qty=item.gift_qty,
                combo_qty=item.combo_qty,
            ))

    # 按 (priority, id) 排序返回
    return sorted(grouped.values(), key=lambda p: (p.priority, p.id))


def build_category_ancestor_map(session: Session) -> dict[int, set[int]]:
    """构造「分类 ID → 自身及全部祖先 ID 集合」的映射。

    ⚠️ 一次性读全部分类（≤ 数百条）在内存里构造，避免每个商品查一次父分类（N+1）。

    用途：判断商品是否命中某个 CATEGORY 类型的促销。
    例如商品 category_id=111（叶菜类，三级），其祖先链是 {111, 11, 1}；
    若促销适用 category_id=11，则 11 ∈ {111, 11, 1}，命中。

    Returns:
        {category_id: {self_id, parent_id, grandparent_id, ...}}
    """
    rows = session.execute(
        select(PrdCategory.id, PrdCategory.parent_id)
    ).all()

    # 先建 child → parent 映射
    parent_of: dict[int, int] = {cid: pid for cid, pid in rows}

    # 再为每个分类展开祖先链
    ancestors: dict[int, set[int]] = {}
    for cid in parent_of:
        chain: set[int] = set()
        current: int = cid
        # 防环：最多遍历 10 层（分类最多 3 级，10 层绝对够）
        for _ in range(10):
            chain.add(current)
            parent = parent_of.get(current, 0)
            if parent == 0 or parent == current:
                break
            current = parent
        ancestors[cid] = chain

    return ancestors


def get_products_by_ids(session: Session, product_ids: list[int]) -> dict[int, PrdProduct]:
    """批量读商品档案（用于取 sale_price / category_id 兜底）。

    Args:
        session: 数据库会话。
        product_ids: 商品 ID 列表。

    Returns:
        {product_id: PrdProduct}；不存在的 ID 不出现在返回字典里。
    """
    if not product_ids:
        return {}
    rows = session.execute(
        select(PrdProduct).where(PrdProduct.id.in_(product_ids))
    ).scalars().all()
    return {p.id: p for p in rows}


def increment_promotion_stats(
    session: Session, promo_id: int, discount_amount: Decimal,
) -> None:
    """更新促销累计统计（结算成功后调用）。

    ⚠️ 用条件 UPDATE 保证并发安全，不做 SELECT + Python 计算 + UPDATE。
    """
    from sqlalchemy import update

    session.execute(
        update(ProPromotion)
        .where(ProPromotion.id == promo_id)
        .values(
            trigger_count=ProPromotion.trigger_count + 1,
            discount_total=ProPromotion.discount_total + discount_amount,
        )
    )
    session.flush()


# ---------- 阶段 7：促销 CRUD ----------


def list_promotions(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: str | None = None,
) -> tuple[list[tuple], int]:
    """分页查询促销，返回 ([(ProPromotion, created_by_name), ...], total)。"""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.sys_models import SysUser

    stmt = (
        sa_select(ProPromotion, SysUser.real_name.label("created_by_name"))
        .outerjoin(SysUser, SysUser.id == ProPromotion.created_by)
    )
    conditions = []
    if keyword:
        conditions.append(ProPromotion.promo_name.like(f"%{keyword.strip()}%"))
    if status:
        conditions.append(ProPromotion.status == status)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        sa_select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(ProPromotion.id.desc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)


def get_promotion_by_id(session: Session, promo_id: int) -> ProPromotion | None:
    from sqlalchemy import select as sa_select

    return session.execute(
        sa_select(ProPromotion).where(ProPromotion.id == promo_id)
    ).scalar_one_or_none()


def insert_promotion(session: Session, **fields) -> ProPromotion:
    p = ProPromotion(**fields)
    session.add(p)
    session.flush()
    return p


def update_promotion(session: Session, promo_id: int, values: dict) -> None:
    from sqlalchemy import update

    if not values:
        return
    session.execute(update(ProPromotion).where(ProPromotion.id == promo_id).values(**values))
    session.flush()


def delete_promotion(session: Session, promo_id: int) -> None:
    """删促销（先删明细再删主表，外键 RESTRICT）。"""
    from sqlalchemy import delete

    session.execute(delete(ProPromotionItem).where(ProPromotionItem.promo_id == promo_id))
    session.execute(delete(ProPromotion).where(ProPromotion.id == promo_id))
    session.flush()


def list_promotion_items(session: Session, promo_id: int) -> list[ProPromotionItem]:
    from sqlalchemy import select as sa_select

    return list(session.execute(
        sa_select(ProPromotionItem).where(ProPromotionItem.promo_id == promo_id)
        .order_by(ProPromotionItem.id)
    ).scalars().all())


def insert_promotion_items_batch(session: Session, items: list[dict]) -> None:
    """批量插入促销明细（一次 add_all + flush）。"""
    objs = [ProPromotionItem(**it) for it in items]
    session.add_all(objs)
    session.flush()


def delete_promotion_items(session: Session, promo_id: int) -> None:
    """删除促销全部明细（修改时全量替换用，spec 5.18）。"""
    from sqlalchemy import delete

    session.execute(delete(ProPromotionItem).where(ProPromotionItem.promo_id == promo_id))
    session.flush()
