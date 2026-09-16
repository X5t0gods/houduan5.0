"""商品数据访问层（阶段 3 交付物）。

⚠️ 关键设计（spec 4.1）：
- 条码搜索**必须用 EXISTS 子查询**，不能 JOIN prd_barcode
  原因：商品 14 有 2 个条码，JOIN 会让它在列表里出现两次、total 虚高
- stock_qty 用 **LEFT JOIN inv_stock + COALESCE(quantity, 0)**
  原因：新建商品无 inv_stock 记录，INNER JOIN 会让它整条消失
- category_name / unit_name 用 LEFT JOIN 取（分类/单位被删的场景兜底为 None）

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.models.base_models import BaseUnit
from app.models.inv_models import (
    InvBatch,
    InvCheckItem,
    InvLossItem,
    InvStock,
    InvStockFlow,
    InvTransferItem,
)
from app.models.prd_models import (
    PrdBarcode,
    PrdCategory,
    PrdPriceHistory,
    PrdProduct,
    PrdProductTag,
)
from app.models.pur_models import PurOrderItem, PurReceiptItem, PurReturnItem
from app.models.sal_models import SalReturnItem, SalTradeItem

# 商品删除前需要检查的**业务流水表**（任一有记录 → 拒绝删除，返回 2007）
# ⚠️ 不包含 prd_barcode / prd_product_tag / pur_supplier_product / prd_price_history
# —— 那些是配置/关联表，删除商品时应一并清理，不阻止删除
_BUSINESS_FLOW_TABLES: list[tuple[str, Any]] = [
    ("sal_trade_item", SalTradeItem),
    ("sal_return_item", SalReturnItem),
    ("pur_order_item", PurOrderItem),
    ("pur_receipt_item", PurReceiptItem),
    ("pur_return_item", PurReturnItem),
    ("inv_stock", InvStock),
    ("inv_stock_flow", InvStockFlow),
    ("inv_batch", InvBatch),
    ("inv_check_item", InvCheckItem),
    ("inv_loss_item", InvLossItem),
    ("inv_transfer_item", InvTransferItem),
]


# ---------- 查询：列表与详情 ----------


def _build_product_select(store_id: int) -> Select:
    """构造带 JOIN 的商品查询基础 Select（列表与详情共用）。

    ⚠️ LEFT JOIN 而非 INNER JOIN：新建商品无 inv_stock 记录也要能出现在列表里。
    """
    return (
        select(
            PrdProduct,
            PrdCategory.category_name.label("category_name"),
            BaseUnit.unit_name.label("unit_name"),
            func.coalesce(InvStock.quantity, Decimal("0")).label("stock_qty"),
        )
        .outerjoin(PrdCategory, PrdCategory.id == PrdProduct.category_id)
        .outerjoin(BaseUnit, BaseUnit.id == PrdProduct.unit_id)
        .outerjoin(
            InvStock,
            and_(InvStock.product_id == PrdProduct.id, InvStock.store_id == store_id),
        )
    )


def list_products(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    category_id: int | None = None,
    store_id: int = 0,
) -> tuple[list[tuple], int]:
    """分页查询商品（GET /products）。

    ⚠️ 条码搜索用 EXISTS 子查询（不用 JOIN），避免一品多码时列表出现重复行。

    Args:
        session: 数据库会话。
        page: 页码，从 1 开始。
        page_size: 每页条数，≤100。
        keyword: 模糊匹配 商品名称 / 商品编码 / 条码。
        category_id: 可选，按分类过滤。
        store_id: 当前门店 ID（用于取 inv_stock.quantity）；未指定时用 0。

    Returns:
        (rows, total) 元组。rows 每项是 (PrdProduct, category_name, unit_name, stock_qty)。
    """
    stmt = _build_product_select(store_id)
    conditions = []

    if keyword:
        kw = f"%{keyword.strip()}%"
        # ⚠️ 关键：条码用 EXISTS 子查询，不用 JOIN，避免商品 14 因 2 个条码出现 2 行
        barcode_exists = (
            select(PrdBarcode.id)
            .where(
                PrdBarcode.product_id == PrdProduct.id,
                PrdBarcode.barcode.like(kw),
            )
            .exists()
        )
        conditions.append(
            or_(
                PrdProduct.product_name.like(kw),
                PrdProduct.product_code.like(kw),
                barcode_exists,
            )
        )

    if category_id is not None:
        conditions.append(PrdProduct.category_id == category_id)

    if conditions:
        stmt = stmt.where(*conditions)

    # 总数：用同样的 WHERE 条件，但只 count PrdProduct.id
    # LEFT JOIN 不会导致行倍增（1:1 关系），所以 count 与主查询一致
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.execute(count_stmt).scalar_one()

    # 分页 + 排序（按 id 倒序，最新建的在前）
    stmt = stmt.order_by(PrdProduct.id.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(stmt).all()
    return list(rows), int(total)


def get_product_row(session: Session, product_id: int, store_id: int) -> tuple | None:
    """按 ID 查商品（含 JOIN 的 category_name / unit_name / stock_qty）。"""
    stmt = _build_product_select(store_id).where(PrdProduct.id == product_id)
    return session.execute(stmt).fetchone()


def get_product_by_barcode_row(
    session: Session, barcode: str, store_id: int,
) -> tuple | None:
    """按条码查商品（GET /products/barcode/{barcode}）。

    ⚠️ 支持主条码与附加条码（spec 4.7）—— 匹配 prd_barcode.barcode 即可。
    ⚠️ 商品已停用（status=0）时**照样返回**（这个接口用于建档查重，不是收银）。
    """
    stmt = (
        _build_product_select(store_id)
        .join(PrdBarcode, PrdBarcode.product_id == PrdProduct.id)
        .where(PrdBarcode.barcode == barcode)
        .limit(1)
    )
    return session.execute(stmt).fetchone()


def get_barcodes_by_product_id(session: Session, product_id: int) -> list[str]:
    """获取商品的所有条码，按 barcode_type 升序（主条码在前）。"""
    rows = session.execute(
        select(PrdBarcode.barcode)
        .where(PrdBarcode.product_id == product_id)
        .order_by(PrdBarcode.barcode_type.asc(), PrdBarcode.id.asc())
    ).scalars().all()
    return list(rows)


def get_existing_barcodes(session: Session, barcodes: list[str]) -> set[str]:
    """查询给定条码中已存在的（用于批量校验）。"""
    if not barcodes:
        return set()
    rows = session.execute(
        select(PrdBarcode.barcode).where(PrdBarcode.barcode.in_(barcodes))
    ).scalars().all()
    return set(rows)


def check_product_code_exists(
    session: Session, code: str, exclude_id: int | None = None,
) -> bool:
    """检查商品编码是否已存在（可排除自身，用于 PUT 时校验）。"""
    stmt = select(PrdProduct.id).where(PrdProduct.product_code == code)
    if exclude_id is not None:
        stmt = stmt.where(PrdProduct.id != exclude_id)
    return session.execute(stmt.limit(1)).fetchone() is not None


# ---------- 写入 ----------


def insert_product(session: Session, **fields) -> PrdProduct:
    """插入商品记录（不含条码，条码由 insert_barcodes 单独处理）。"""
    product = PrdProduct(**fields)
    session.add(product)
    session.flush()  # 拿到 product.id
    return product


def update_product_fields(session: Session, product_id: int, values: dict) -> None:
    """更新商品字段（只更新传入的键）。"""
    if not values:
        return
    session.execute(
        update(PrdProduct).where(PrdProduct.id == product_id).values(**values)
    )
    session.flush()


def insert_barcodes(session: Session, product_id: int, barcodes: list[str]) -> None:
    """批量插入条码：第一个是主条码（type=1），其余是附加条码（type=2）。"""
    for idx, code in enumerate(barcodes):
        session.add(PrdBarcode(
            product_id=product_id,
            barcode=code,
            barcode_type=1 if idx == 0 else 2,
            status=1,
        ))
    session.flush()


def diff_barcodes(session: Session, product_id: int, new_barcodes: list[str]) -> None:
    """按 diff 方式更新条码（spec 4.4）：
    - B - A → INSERT
    - A - B → DELETE
    - A ∩ B → **保持不变**（不动 created_at）

    ⚠️ 不全量删了重建：会丢失 created_at，制造无谓的删除/插入。

    Args:
        session: 数据库会话。
        product_id: 商品 ID。
        new_barcodes: 目标条码集合（B）。第一个视为主条码。
    """
    # 现有条码集合 A
    existing_rows = session.execute(
        select(PrdBarcode.id, PrdBarcode.barcode, PrdBarcode.barcode_type)
        .where(PrdBarcode.product_id == product_id)
    ).all()
    existing_map = {row[1]: (row[0], row[2]) for row in existing_rows}  # barcode → (id, type)
    existing_set = set(existing_map.keys())
    new_set = set(new_barcodes)

    # A - B → DELETE
    to_delete = existing_set - new_set
    if to_delete:
        session.execute(
            delete(PrdBarcode).where(
                PrdBarcode.product_id == product_id,
                PrdBarcode.barcode.in_(to_delete),
            )
        )

    # B - A → INSERT
    to_insert = new_set - existing_set
    # 按 new_barcodes 的顺序确定主/附加
    for idx, code in enumerate(new_barcodes):
        if code in to_insert:
            session.add(PrdBarcode(
                product_id=product_id,
                barcode=code,
                barcode_type=1 if idx == 0 else 2,
                status=1,
            ))

    # A ∩ B → 保持不变，但如果 barcode_type 需要调整（如主条码换了），单独 UPDATE
    for idx, code in enumerate(new_barcodes):
        if code in existing_set:
            expected_type = 1 if idx == 0 else 2
            current_id, current_type = existing_map[code]
            if current_type != expected_type:
                session.execute(
                    update(PrdBarcode)
                    .where(PrdBarcode.id == current_id)
                    .values(barcode_type=expected_type)
                )

    session.flush()


def write_price_history(
    session: Session,
    *,
    product_id: int,
    change_type: str,
    old_purchase: Decimal | None = None,
    new_purchase: Decimal | None = None,
    old_sale: Decimal | None = None,
    new_sale: Decimal | None = None,
    old_member: Decimal | None = None,
    new_member: Decimal | None = None,
    changed_by: int | None = None,
    reason: str | None = None,
) -> None:
    """写一条价格变更历史（FRS-PRD-003）。

    ⚠️ 三个价（进价/售价/会员价）中**哪个变了就为哪个单独写一条记录**（spec 4.4）。
       三个价都没变时**不要调用**此函数（否则改个备注也刷一条垃圾记录）。

    Args:
        change_type: '进价' / '售价' / '会员价'（对应 PrdPriceHistory.change_type）。
    """
    session.add(PrdPriceHistory(
        product_id=product_id,
        old_purchase_price=old_purchase,
        new_purchase_price=new_purchase,
        old_sale_price=old_sale,
        new_sale_price=new_sale,
        change_type=change_type,
        changed_by=changed_by,
        reason=reason,
    ))
    session.flush()


def delete_product_cascade(session: Session, product_id: int) -> None:
    """删除商品及其配置类关联记录（**不删业务流水**）。

    ⚠️ 顺序至关重要（spec 4.6 点 2）：18 张表外键引用 prd_product(id) 且**全部 RESTRICT**，
       不先删子表会直接抛外键约束错误（500），而不是优雅的业务错误。

    本阶段清理范围：
    - prd_barcode（商品条码）
    - prd_product_tag（商品标签关联）
    - prd_price_history（价格变更历史）
    - pur_supplier_product（供货关系）

    待办（阶段 6/9 补）：
    - pro_promotion_item.gift_product_id（阶段 6）
    - bi_recommend_cache.product_id / recommend_product_id（阶段 9）
    """
    session.execute(delete(PrdBarcode).where(PrdBarcode.product_id == product_id))
    session.execute(delete(PrdProductTag).where(PrdProductTag.product_id == product_id))
    session.execute(delete(PrdPriceHistory).where(PrdPriceHistory.product_id == product_id))
    # pur_supplier_product 需要延迟导入避免循环
    from app.models.pur_models import PurSupplierProduct
    session.execute(
        delete(PurSupplierProduct).where(PurSupplierProduct.product_id == product_id)
    )
    # 最后删商品本身
    session.execute(delete(PrdProduct).where(PrdProduct.id == product_id))
    session.flush()


def find_business_flow_reference(session: Session, product_id: int) -> str | None:
    """检查商品是否有业务流水（删除前校验）。

    Returns:
        第一个命中的表名（用于错误消息定位），或 None 表示无流水可删。

    ⚠️ 遍历 11 张业务流水表，任一有记录即返回。演示商品全部有 inv_stock + inv_stock_flow，
       因此**20 个演示商品一个都删不掉**（spec 4.6 点 3，预期行为）。
    """
    for table_name, model in _BUSINESS_FLOW_TABLES:
        # 各表的字段名都是 product_id
        count = session.execute(
            select(func.count()).select_from(model).where(model.product_id == product_id)
        ).scalar_one()
        if count > 0:
            return table_name
    return None


# ---------- 导入相关 ----------


def get_max_product_seq(session: Session) -> int:
    """查询现有最大商品编码流水号（与 scripts/init_product_seq.py 逻辑一致）。

    ⚠️ 用原生 SQL 避免 SQLAlchemy 对 MySQL REGEXP + CAST 的方言差异。
    """
    result = session.execute(
        text(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(product_code, 2) AS UNSIGNED)), 0) "
            "FROM prd_product WHERE product_code REGEXP '^P[0-9]+$'"
        )
    ).scalar_one()
    return int(result or 0)
