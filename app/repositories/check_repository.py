"""盘点数据访问层（阶段 5 CP2 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
⚠️ 盘点明细批量 INSERT（spec 5.5：不要逐行插入 20+ 次）。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.models.inv_models import InvCheck, InvCheckItem, InvStock
from app.models.prd_models import PrdProduct
from app.models.sys_models import SysUser

# ---------- 盘点单 ----------


def get_check_by_id(session: Session, check_id: int) -> InvCheck | None:
    return session.execute(
        select(InvCheck).where(InvCheck.id == check_id)
    ).scalar_one_or_none()


def insert_check(session: Session, **fields) -> InvCheck:
    c = InvCheck(**fields)
    session.add(c)
    session.flush()
    return c


def list_checks(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[tuple], int]:
    """分页查询盘点单，返回 (行列表, total)。每行 = (InvCheck, created_by_name)。"""
    stmt: Select = (
        select(InvCheck, SysUser.real_name.label("created_by_name"))
        .outerjoin(SysUser, SysUser.id == InvCheck.created_by)
    )
    conditions = []
    if status:
        conditions.append(InvCheck.status == status)
    if store_id is not None:
        conditions.append(InvCheck.store_id == store_id)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(InvCheck.id.desc()).offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)


def update_check(session: Session, check_id: int, values: dict) -> None:
    if not values:
        return
    session.execute(update(InvCheck).where(InvCheck.id == check_id).values(**values))
    session.flush()


# ---------- 盘点明细 ----------


def insert_check_items_batch(session: Session, items: list[dict]) -> None:
    """批量插入盘点明细（一次 add_all + flush，spec 5.5）。"""
    objs = [InvCheckItem(**it) for it in items]
    session.add_all(objs)
    session.flush()


def list_check_items(session: Session, check_id: int) -> list[InvCheckItem]:
    return list(session.execute(
        select(InvCheckItem).where(InvCheckItem.check_id == check_id).order_by(InvCheckItem.id)
    ).scalars().all())


def get_check_item(session: Session, item_id: int) -> InvCheckItem | None:
    return session.execute(
        select(InvCheckItem).where(InvCheckItem.id == item_id)
    ).scalar_one_or_none()


def update_check_item(session: Session, item_id: int, values: dict) -> None:
    session.execute(update(InvCheckItem).where(InvCheckItem.id == item_id).values(**values))
    session.flush()


# ---------- 盘点范围：按 check_type 取商品库存 ----------


def get_stock_for_check(
    session: Session,
    *,
    store_id: int,
    check_type: str,
    check_scope: str | None,
    descendant_ids: set[int] | None = None,
) -> list[tuple]:
    """按盘点方式取该门店的库存记录（用于生成明细）。

    返回 [(InvStock, product_code, product_name, spec), ...]。

    check_type：
    - ALL：全部库存
    - CATEGORY：check_scope 分类及子孙分类下的商品（descendant_ids 由 service 传入）
    - SHELF：check_scope 货架上的商品
    - PART：退化为 ALL（spec 5.5，无法解析抽样规则）
    """
    stmt = (
        select(InvStock, PrdProduct.product_code, PrdProduct.product_name, PrdProduct.spec)
        .join(PrdProduct, PrdProduct.id == InvStock.product_id)
        .where(InvStock.store_id == store_id)
    )

    if check_type == "CATEGORY" and check_scope and descendant_ids is not None:
        # check_scope 是分类 ID（可能逗号分隔多个）；含自身 + 子孙
        scope_ids = _parse_scope_ids(check_scope)
        all_cat_ids = set(scope_ids) | descendant_ids
        stmt = stmt.where(PrdProduct.category_id.in_(all_cat_ids))
    elif check_type == "SHELF" and check_scope:
        shelf_ids = _parse_scope_ids(check_scope)
        stmt = stmt.where(PrdProduct.shelf_id.in_(shelf_ids))
    # ALL / PART / 无法解析 → 不加额外过滤（全部）

    return list(session.execute(stmt.order_by(InvStock.product_id.asc())).all())


def _parse_scope_ids(scope: str) -> list[int]:
    """解析 check_scope（逗号分隔的 ID 字符串）为 int 列表。"""
    ids = []
    for part in scope.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


# ---------- 审核时的实时库存读取 ----------


def get_stock_qty(session: Session, store_id: int, product_id: int) -> Decimal | None:
    """读取审核时的实时库存（用于增量调整 + 负库存检查）。"""
    return session.execute(
        select(InvStock.quantity).where(
            InvStock.store_id == store_id,
            InvStock.product_id == product_id,
        )
    ).scalar_one_or_none()
