"""库存写入数据访问层（阶段 4 交付物）—— **只处理销售出库与退货入库**。

⚠️ 采购入库 / 盘点 / 调拨 / 报损属阶段 5，本文件不涉及。

⚠️ 关键设计（spec 6.3）：**条件更新防超卖**
-------------------------------------------------
所有库存扣减都用 `UPDATE ... WHERE quantity >= :qty`，执行后检查 `rowcount`：
- rowcount = 1 → 扣减成功
- rowcount = 0 → 库存不足（并发下另一个事务先扣走了），抛 7004 触发回滚

⛔ **不要**先 SELECT 再 UPDATE —— 并发下两个事务都读到 quantity=5，各自 UPDATE -3，
   结果一个成功一个失败但数据不一致；或者两个都成功但库存变负数。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.inv_models import InvStock, InvStockFlow


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ))


def get_stock(session: Session, store_id: int, product_id: int) -> InvStock | None:
    """读取一条库存记录（可能不存在，如新商品未初始化库存）。"""
    return session.execute(
        select(InvStock).where(
            InvStock.store_id == store_id,
            InvStock.product_id == product_id,
        )
    ).scalar_one_or_none()


def deduct_stock(
    session: Session,
    *,
    store_id: int,
    product_id: int,
    quantity: Decimal,
) -> tuple[bool, Decimal, Decimal, Decimal]:
    """扣减库存（条件更新，防超卖）。

    ⚠️ 关键 SQL：
        UPDATE inv_stock SET quantity = quantity - :qty
        WHERE store_id=? AND product_id=? AND quantity >= :qty

    Args:
        session: 数据库会话。
        store_id: 门店 ID。
        product_id: 商品 ID。
        quantity: 扣减数量（正数）。

    Returns:
        (成功?, before_qty, after_qty, avg_cost) 元组。
        成功=False 表示库存不足（rowcount=0）或库存记录不存在，调用方应抛 7004。

    ⚠️ before_qty / after_qty / avg_cost 用于后续写 inv_stock_flow，
       调用方必须在同一事务内使用（事务未提交前数据一致）。
    """
    # 先读当前值（用于流水的 before/after）
    # ⚠️ 这里的 SELECT 只是取快照值，实际的扣减靠下面的 UPDATE 条件保证并发安全
    stock = get_stock(session, store_id, product_id)
    if stock is None:
        return False, Decimal("0"), Decimal("0"), Decimal("0")

    before_qty = stock.quantity
    avg_cost = stock.avg_cost

    # 条件更新：WHERE quantity >= :qty 保证不超卖
    result = session.execute(
        text(
            "UPDATE inv_stock SET quantity = quantity - :qty "
            "WHERE store_id = :sid AND product_id = :pid AND quantity >= :qty"
        ),
        {"qty": quantity, "sid": store_id, "pid": product_id},
    )
    session.flush()

    if result.rowcount == 0:
        # 库存不足或并发下被其他事务先扣走了
        return False, before_qty, before_qty, avg_cost

    after_qty = before_qty - quantity
    return True, before_qty, after_qty, avg_cost


def restore_stock(
    session: Session,
    *,
    store_id: int,
    product_id: int,
    quantity: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """回补库存（用于退货与作废）。

    ⚠️ 回补不需要条件更新（不会导致负库存），但仍需要返回 before/after 供流水使用。

    Returns:
        (before_qty, after_qty, avg_cost) 元组。
    """
    stock = get_stock(session, store_id, product_id)
    if stock is None:
        # 理论上不应发生（销售过的商品必然有 inv_stock 记录）
        return Decimal("0"), quantity, Decimal("0")

    before_qty = stock.quantity
    avg_cost = stock.avg_cost

    session.execute(
        update(InvStock)
        .where(
            InvStock.store_id == store_id,
            InvStock.product_id == product_id,
        )
        .values(quantity=InvStock.quantity + quantity)
    )
    session.flush()

    return before_qty, before_qty + quantity, avg_cost


def upsert_stock_add(
    session: Session,
    *,
    store_id: int,
    product_id: int,
    quantity: Decimal,
    avg_cost: Decimal,
) -> tuple[Decimal, Decimal]:
    """调入门店库存 upsert（不存在则新建，存在则累加）——调拨入库专用（spec 5.10）。

    ⚠️ 新建时 safe_qty 默认 0（spec 5.10）；avg_cost 用调出方的成本。
    ⚠️ 已存在时只累加 quantity，**不改 avg_cost**（本阶段不做移动加权成本重算，阶段 7）。

    Returns:
        (before_qty, after_qty) 元组，供写流水用。
    """
    stock = get_stock(session, store_id, product_id)
    if stock is None:
        # 新建库存记录
        before_qty = Decimal("0")
        session.add(InvStock(
            store_id=store_id,
            product_id=product_id,
            quantity=quantity,
            safe_qty=Decimal("0"),
            max_qty=None,
            avg_cost=avg_cost,
        ))
        session.flush()
        return before_qty, quantity

    before_qty = stock.quantity
    session.execute(
        update(InvStock)
        .where(
            InvStock.store_id == store_id,
            InvStock.product_id == product_id,
        )
        .values(quantity=InvStock.quantity + quantity)
    )
    session.flush()
    return before_qty, before_qty + quantity


def write_stock_flow(
    session: Session,
    *,
    store_id: int,
    product_id: int,
    flow_type: str,
    direction: int,
    quantity: Decimal,
    before_qty: Decimal,
    after_qty: Decimal,
    unit_cost: Decimal,
    amount: Decimal,
    source_type: str,
    source_no: str,
    batch_id: int | None = None,
    operator: int | None = None,
    remark: str | None = None,
) -> None:
    """写库存流水（只增不改）。

    ⚠️ flow_type 取值（与 db/01_schema.sql 建表注释一致，spec 三 ③）：
    - 'SALE_OUT'：销售出库（direction=-1）
    - 'SALE_RETURN_IN'：销售退货入库（direction=1）
    - ⛔ 不是 'RETURN_IN'（docs/06 1684 行是错的）

    Args:
        direction: 1 入库 / -1 出库
        quantity: 变动数量（正数，方向由 direction 决定）
        before_qty / after_qty: 变动前后库存（用于勾稽校验）
        unit_cost: 单位成本快照（DECIMAL(12,4)）
        amount: 变动金额 = quantity × unit_cost（DECIMAL(12,2)）
        source_type / source_no: 来源单据（如 'SALE' + trade_no）
    """
    session.add(InvStockFlow(
        store_id=store_id,
        product_id=product_id,
        flow_type=flow_type,
        direction=direction,
        quantity=quantity,
        before_qty=before_qty,
        after_qty=after_qty,
        unit_cost=unit_cost,
        amount=amount,
        source_type=source_type,
        source_no=source_no,
        batch_id=batch_id,
        operator=operator,
        remark=remark,
    ))
    session.flush()
