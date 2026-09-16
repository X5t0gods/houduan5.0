"""采购订单数据访问层（阶段 6 CP3 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
⚠️ pur_order_item 无商品名称快照（spec 冲突表 ④）：详情 items 必须 JOIN prd_product + base_unit。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.models.base_models import BaseUnit
from app.models.prd_models import PrdProduct
from app.models.pur_models import PurOrder, PurOrderItem, PurSupplier
from app.models.sys_models import SysUser

# ---------- 订单 ----------


def get_order_by_id(session: Session, order_id: int) -> PurOrder | None:
    return session.execute(
        select(PurOrder).where(PurOrder.id == order_id)
    ).scalar_one_or_none()


def insert_order(session: Session, **fields) -> PurOrder:
    o = PurOrder(**fields)
    session.add(o)
    session.flush()
    return o


def update_order(session: Session, order_id: int, values: dict) -> None:
    if not values:
        return
    session.execute(update(PurOrder).where(PurOrder.id == order_id).values(**values))
    session.flush()


def list_orders(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    supplier_id: int | None = None,
    store_id: int | None = None,
) -> tuple[list[tuple], int]:
    """分页查询采购订单，返回 (行列表, total)。

    每行 = (PurOrder, supplier_name, created_by_name)。
    按 order_date 倒序、id 倒序（spec 5.1）。
    """
    stmt: Select = (
        select(
            PurOrder,
            PurSupplier.supplier_name.label("supplier_name"),
            SysUser.real_name.label("created_by_name"),
        )
        .outerjoin(PurSupplier, PurSupplier.id == PurOrder.supplier_id)
        .outerjoin(SysUser, SysUser.id == PurOrder.created_by)
    )
    conditions = []
    if status:
        conditions.append(PurOrder.status == status)
    if supplier_id is not None:
        conditions.append(PurOrder.supplier_id == supplier_id)
    if store_id is not None:
        conditions.append(PurOrder.store_id == store_id)
    if conditions:
        stmt = stmt.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    stmt = stmt.order_by(PurOrder.order_date.desc(), PurOrder.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(session.execute(stmt).all()), int(total)


# ---------- 订单明细 ----------


def insert_order_items_batch(session: Session, items: list[dict]) -> None:
    """批量插入订单明细（一次 add_all + flush）。"""
    objs = [PurOrderItem(**it) for it in items]
    session.add_all(objs)
    session.flush()


def list_order_items(session: Session, order_id: int) -> list[PurOrderItem]:
    return list(session.execute(
        select(PurOrderItem).where(PurOrderItem.order_id == order_id).order_by(PurOrderItem.id)
    ).scalars().all())


def get_order_item(session: Session, order_id: int, product_id: int) -> PurOrderItem | None:
    return session.execute(
        select(PurOrderItem).where(
            PurOrderItem.order_id == order_id,
            PurOrderItem.product_id == product_id,
        )
    ).scalar_one_or_none()


def delete_order_items(session: Session, order_id: int) -> None:
    """删除订单全部明细（修改订单时全量替换用，spec 5.4）。"""
    from sqlalchemy import delete

    session.execute(delete(PurOrderItem).where(PurOrderItem.order_id == order_id))
    session.flush()


def update_order_item(session: Session, item_id: int, values: dict) -> None:
    session.execute(update(PurOrderItem).where(PurOrderItem.id == item_id).values(**values))
    session.flush()


def list_order_items_with_product(session: Session, order_id: int) -> list[tuple]:
    """订单明细 + 商品信息（JOIN prd_product + base_unit，spec 冲突表 ④）。

    返回 [(PurOrderItem, product_code, product_name, spec, unit_name), ...]。
    """
    return list(session.execute(
        select(
            PurOrderItem,
            PrdProduct.product_code,
            PrdProduct.product_name,
            PrdProduct.spec,
            BaseUnit.unit_name,
        )
        .outerjoin(PrdProduct, PrdProduct.id == PurOrderItem.product_id)
        .outerjoin(BaseUnit, BaseUnit.id == PrdProduct.unit_id)
        .where(PurOrderItem.order_id == order_id)
        .order_by(PurOrderItem.id)
    ).all())
