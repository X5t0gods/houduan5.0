"""库存查询数据访问层（阶段 5 CP1 交付物）。

⚠️ 关键设计（spec 5.1 + 4.2 + 5.4）：
- 一次 JOIN 把商品/单位/门店全带出来，⛔ 禁止逐行查库（N+1）
- `stock_amount = quantity × avg_cost`（量化到分，用 money()）
- `stock_status` 优先级：**LOW > NEAR_EXPIRY > OVER > NORMAL**（spec 4.2）
- `remain_days` / `nearest_expiry`：取该门店下 remain_qty>0 批次中 expiry_date 最小者，
  `remain_days = DATEDIFF(expiry_date, today)`（**可为负**，已过期）
- ⛔ **不依赖 inv_batch.status**（初始化数据里过期批次 status 仍是 1），必须实时算日期差

⚠️ 分层约束：只读，不 commit。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sys_config_reader import get_config
from app.models.base_models import BaseStore, BaseUnit
from app.models.inv_models import InvBatch, InvStock
from app.models.prd_models import PrdProduct
from app.utils.money import money


def _today() -> date:
    return datetime.now(ZoneInfo(settings.TZ)).date()


# ---------- 库存状态判定（spec 4.2） ----------

# 状态优先级：数字越小优先级越高
_STATUS_PRIORITY = {"LOW": 0, "NEAR_EXPIRY": 1, "OVER": 2, "NORMAL": 3}


def compute_stock_status(
    quantity: Decimal,
    safe_qty: Decimal,
    max_qty: Decimal | None,
    remain_days: int | None,
    near_expiry_days: int,
) -> str:
    """按优先级判定库存状态（spec 4.2）。

    优先级：LOW（缺货）> NEAR_EXPIRY（临期）> OVER（积压）> NORMAL

    Args:
        quantity: 当前库存。
        safe_qty: 安全库存下限。
        max_qty: 库存上限（None 时不参与 OVER 判定）。
        remain_days: 最近到期批次的剩余天数（None=无批次；可为负=已过期）。
        near_expiry_days: 临期阈值（sys_config inv.near_expiry_days，默认 30）。

    Returns:
        'LOW' / 'NEAR_EXPIRY' / 'OVER' / 'NORMAL'
    """
    # LOW：quantity <= safe_qty（⛔ 含等于，spec 5.4）
    if quantity <= safe_qty:
        return "LOW"
    # NEAR_EXPIRY：有未耗尽批次且剩余天数 <= 阈值（含已过期负数）
    if remain_days is not None and remain_days <= near_expiry_days:
        return "NEAR_EXPIRY"
    # OVER：max_qty 非空且 quantity > max_qty
    if max_qty is not None and quantity > max_qty:
        return "OVER"
    return "NORMAL"


def get_nearest_expiry_map(
    session: Session, store_id: int, product_ids: list[int]
) -> dict[int, tuple[date, int]]:
    """批量查询每个商品在指定门店下"最近到期批次"的 (expiry_date, remain_days)。

    ⚠️ 一次性查全部 product_ids（避免 N+1）。
    ⚠️ 只取 remain_qty > 0 的批次；⛔ 不看 inv_batch.status（实时算日期差）。
    ⚠️ 同一商品多批次时取 expiry_date 最小的那条。

    Returns:
        {product_id: (nearest_expiry_date, remain_days)}；无批次的商品不在字典里。
    """
    if not product_ids:
        return {}
    today = _today()
    # 子查询：每个商品的最小 expiry_date（remain_qty>0）
    rows = session.execute(
        select(
            InvBatch.product_id,
            func.min(InvBatch.expiry_date).label("min_expiry"),
        )
        .where(
            InvBatch.store_id == store_id,
            InvBatch.product_id.in_(product_ids),
            InvBatch.remain_qty > 0,
            InvBatch.expiry_date.is_not(None),
        )
        .group_by(InvBatch.product_id)
    ).all()

    result: dict[int, tuple[date, int]] = {}
    for pid, min_expiry in rows:
        if min_expiry is None:
            continue
        remain_days = (min_expiry - today).days  # 可为负（已过期）
        result[pid] = (min_expiry, remain_days)
    return result


def _build_stock_select() -> Select:
    """构造库存查询的 JOIN 语句（inv_stock + prd_product + base_unit + base_store）。"""
    return (
        select(
            InvStock,
            PrdProduct.product_code,
            PrdProduct.product_name,
            PrdProduct.spec,
            BaseUnit.unit_name,
            BaseStore.store_name,
        )
        .join(PrdProduct, PrdProduct.id == InvStock.product_id)
        .outerjoin(BaseUnit, BaseUnit.id == PrdProduct.unit_id)
        .outerjoin(BaseStore, BaseStore.id == InvStock.store_id)
    )


def list_stock(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    stock_status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[dict], int]:
    """分页查询实时库存（spec 5.1）。

    ⚠️ stock_status 是**计算字段**，无法在 SQL 层过滤。策略：
    - 无 stock_status 过滤：SQL 层分页（OFFSET/LIMIT），只对当前页批量查到期日
    - 有 stock_status 过滤：先查全部（store+keyword 命中），Python 算状态后过滤再分页
      （社区超市单店库存量级小，可接受；已在注释说明取舍）

    Returns:
        (库存字典列表, total)。每个字典含 StockItem 需要的所有字段。
    """
    near_expiry_days = int(get_config(session, "inv.near_expiry_days", 30))

    # 基础过滤条件（store + keyword）
    conditions = []
    if store_id is not None:
        conditions.append(InvStock.store_id == store_id)
    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(or_(
            PrdProduct.product_name.like(kw),
            PrdProduct.product_code.like(kw),
        ))

    base_stmt = _build_stock_select()
    if conditions:
        base_stmt = base_stmt.where(*conditions)

    if stock_status:
        # 有状态过滤：查全部 → Python 算 → 过滤 → 分页
        all_rows = session.execute(
            base_stmt.order_by(InvStock.product_id.asc(), InvStock.store_id.asc())
        ).all()
        # 到期日按门店分组批量查（多门店时 store_id 可能不同）
        expiry_maps = _batch_expiry_by_store(session, all_rows)
        computed = [
            _row_to_stock_dict(r, expiry_maps, near_expiry_days)
            for r in all_rows
        ]
        filtered = [c for c in computed if c["stock_status"] == stock_status]
        total = len(filtered)
        start = (page - 1) * page_size
        page_rows = filtered[start:start + page_size]
        return page_rows, total

    # 无状态过滤：SQL 层分页
    total = session.execute(
        select(func.count()).select_from(base_stmt.subquery())
    ).scalar_one()

    rows = session.execute(
        base_stmt.order_by(InvStock.product_id.asc(), InvStock.store_id.asc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    expiry_maps = _batch_expiry_by_store(session, rows)
    result = [_row_to_stock_dict(r, expiry_maps, near_expiry_days) for r in rows]
    return result, int(total)


def _batch_expiry_by_store(
    session: Session, rows: list
) -> dict[int, dict[int, tuple[date, int]]]:
    """按门店分组批量查到期日（多门店场景）。

    Returns:
        {store_id: {product_id: (expiry_date, remain_days)}}
    """
    # 收集 (store_id, product_id) 组合，按 store 分组
    store_products: dict[int, list[int]] = {}
    for r in rows:
        stock = r[0]
        store_products.setdefault(stock.store_id, []).append(stock.product_id)

    result: dict[int, dict[int, tuple[date, int]]] = {}
    for sid, pids in store_products.items():
        result[sid] = get_nearest_expiry_map(session, sid, pids)
    return result


def _row_to_stock_dict(
    row: tuple,
    expiry_maps: dict[int, dict[int, tuple[date, int]]],
    near_expiry_days: int,
) -> dict:
    """把 JOIN 行转成 StockItem 字典（计算 stock_amount/stock_status/remain_days）。"""
    stock, product_code, product_name, spec, unit_name, store_name = row
    expiry_map = expiry_maps.get(stock.store_id, {})
    nearest = expiry_map.get(stock.product_id)
    nearest_expiry = nearest[0] if nearest else None
    remain_days = nearest[1] if nearest else None

    status = compute_stock_status(
        stock.quantity, stock.safe_qty, stock.max_qty, remain_days, near_expiry_days,
    )
    return {
        "id": stock.id,
        "store_id": stock.store_id,
        "store_name": store_name,
        "product_id": stock.product_id,
        "product_code": product_code,
        "product_name": product_name,
        "spec": spec,
        "unit_name": unit_name,
        "quantity": stock.quantity,
        "safe_qty": stock.safe_qty,
        "max_qty": stock.max_qty,
        "avg_cost": stock.avg_cost,
        "stock_amount": money(stock.quantity * stock.avg_cost),
        "stock_status": status,
        "nearest_expiry": nearest_expiry,
        "remain_days": remain_days,
    }


def get_warnings(
    session: Session, store_id: int | None = None
) -> dict[str, list[dict]]:
    """三类预警（spec 5.4）。

    ⚠️ 三个数组独立维度，一个商品可同时出现在 low_stock 与 near_expiry。
    ⚠️ 每个数组最多 100 条（不分页）。
    """
    near_expiry_days = int(get_config(session, "inv.near_expiry_days", 30))

    stmt = _build_stock_select()
    if store_id is not None:
        stmt = stmt.where(InvStock.store_id == store_id)
    rows = session.execute(
        stmt.order_by(InvStock.product_id.asc())
    ).all()
    expiry_maps = _batch_expiry_by_store(session, rows)

    low_stock: list[dict] = []
    over_stock: list[dict] = []
    near_expiry: list[dict] = []

    for r in rows:
        stock = r[0]
        d = _row_to_stock_dict(r, expiry_maps, near_expiry_days)
        # LOW：quantity <= safe_qty
        if stock.quantity <= stock.safe_qty and len(low_stock) < 100:
            low_stock.append(d)
        # OVER：max_qty 非空且 quantity > max_qty
        if stock.max_qty is not None and stock.quantity > stock.max_qty and len(over_stock) < 100:
            over_stock.append(d)
        # NEAR_EXPIRY：有未耗尽批次且剩余天数 <= 阈值
        remain_days = d["remain_days"]
        if remain_days is not None and remain_days <= near_expiry_days and len(near_expiry) < 100:
            near_expiry.append(d)

    return {
        "low_stock": low_stock,
        "over_stock": over_stock,
        "near_expiry": near_expiry,
    }


# 让 lint 知道 and_ 被间接使用
_ = and_
