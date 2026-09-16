"""采购订单业务编排（阶段 6 CP3 交付物）—— 建单/查询/修改/审核/作废。

⚠️ 状态机（spec 4.1）：
   POST /purchases            → DRAFT
   audit approved=true        → AUDITED
   audit approved=false       → 保持 DRAFT + 写 reject_reason
   receive 部分               → PARTIAL（CP4）
   receive 全部               → FINISHED（CP4）
   void                       → VOID（仅 DRAFT）

⛔ 只有 DRAFT 可修改/审核/作废（否则 4004）。
⛔ 创建一律 DRAFT，不接受 status 参数（spec 冲突表 ⑦）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.models.prd_models import PrdProduct
from app.repositories import purchase_order_repository as repo
from app.repositories import supplier_repository
from app.repositories.auth_repository import write_operation_log
from app.schemas.purchase import (
    AuditRequest,
    PurchaseOrder,
    PurchaseOrderCreate,
    PurchaseOrderItem,
    PurchaseOrderUpdate,
    VoidRequest,
)
from app.utils.money import ZERO, money

logger = get_logger(__name__)


def _today() -> date:
    return datetime.now(ZoneInfo(settings.TZ)).date()


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ)).replace(tzinfo=None)


def _get_products(session: Session, product_ids: list[int]) -> dict[int, PrdProduct]:
    from sqlalchemy import select

    if not product_ids:
        return {}
    rows = session.execute(
        select(PrdProduct).where(PrdProduct.id.in_(product_ids))
    ).scalars().all()
    return {p.id: p for p in rows}


def _validate_items(session: Session, items: list) -> None:
    """校验明细：非空、quantity>0、unit_price>=0、商品存在且未停用。"""
    if not items:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "采购明细不能为空")
    product_ids = [it.product_id for it in items]
    products = _get_products(session, product_ids)
    for it in items:
        if it.quantity <= 0:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "采购数量必须大于 0")
        if it.unit_price < 0:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "采购单价不能为负")
        prod = products.get(it.product_id)
        if prod is None:
            raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品ID={it.product_id}不存在")
        if prod.status == 0:
            raise BusinessError(ErrorCode.PRODUCT_DUPLICATE, f"商品「{prod.product_name}」已停用")


def _validate_supplier(session: Session, supplier_id: int) -> None:
    """校验供应商：存在 → 4005；停用 → 3001（spec 5.3）。"""
    supplier = supplier_repository.get_supplier_by_id(session, supplier_id)
    if supplier is None:
        raise BusinessError(ErrorCode.SUPPLIER_NOT_FOUND, f"供应商 id={supplier_id} 不存在")
    if supplier.status == 0:
        raise BusinessError(
            ErrorCode.SUPPLIER_DISABLED, f"供应商「{supplier.supplier_name}」已停用"
        )


# ---------- 5.3 POST /purchases ----------


def create_order(
    session: Session,
    params: PurchaseOrderCreate,
    *,
    store_id: int,
    operator_id: int,
) -> PurchaseOrder:
    """创建采购订单（spec 5.3）：一律 DRAFT。

    校验顺序：供应商存在(4005)/停用(3001) → 明细非空/数量>0/单价>=0(2001)
    → 商品存在(2004)/停用(2002)。
    """
    _validate_supplier(session, params.supplier_id)
    _validate_items(session, params.items)

    seq = NoSeqService(session)
    order_no = seq.next_no("CG")

    # 计算总额
    total_amount = ZERO
    item_rows = []
    for it in params.items:
        amount = money(it.quantity * it.unit_price)
        total_amount += amount
        item_rows.append({
            "product_id": it.product_id,
            "quantity": it.quantity,
            "received_qty": ZERO,
            "unit_price": it.unit_price,
            "amount": amount,
            "remark": it.remark,
        })
    total_amount = money(total_amount)

    order = repo.insert_order(
        session,
        order_no=order_no,
        supplier_id=params.supplier_id,
        store_id=params.store_id or store_id,
        order_date=params.order_date or _today(),
        expect_date=params.expect_date,
        total_amount=total_amount,
        source_type=params.source_type or "MANUAL",
        status="DRAFT",
        remark=params.remark,
        created_by=operator_id,
    )
    # 补 order_id 后批量插入明细
    for row in item_rows:
        row["order_id"] = order.id
    repo.insert_order_items_batch(session, item_rows)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="创建采购单",
        target_type="pur_order", target_id=order.id,
        after_value={"order_no": order_no, "total_amount": str(total_amount)},
        result=1,
    )
    session.commit()
    logger.info(f"创建采购单：order_no={order_no}, total={total_amount}, {len(item_rows)} 项")
    return get_order_detail(session, order.id)


# ---------- 5.4 PUT /purchases/{id} ----------


def update_order(
    session: Session, order_id: int, params: PurchaseOrderUpdate, *, operator_id: int,
) -> PurchaseOrder:
    """修改采购订单（spec 5.4）：仅 DRAFT 可改，明细全量替换。

    ⛔ 已收货（received_qty>0）拒绝修改（4004）。
    """
    order = repo.get_order_by_id(session, order_id)
    if order is None:
        raise BusinessError(ErrorCode.PURCHASE_ORDER_NOT_FOUND, f"采购订单 id={order_id} 不存在")
    if order.status != "DRAFT":
        raise BusinessError(
            ErrorCode.PURCHASE_STATUS_FORBID, f"只有草稿状态可修改（当前 {order.status}）"
        )

    # 已收货校验（DRAFT 状态下 received_qty 应为 0，防御性检查）
    existing_items = repo.list_order_items(session, order_id)
    if any((it.received_qty or ZERO) > 0 for it in existing_items):
        raise BusinessError(ErrorCode.PURCHASE_STATUS_FORBID, "订单已收货，不可修改明细")

    # 供应商变更校验
    if params.supplier_id is not None and params.supplier_id != order.supplier_id:
        _validate_supplier(session, params.supplier_id)

    # 明细全量替换
    if params.items is not None:
        _validate_items(session, params.items)
        repo.delete_order_items(session, order_id)
        total_amount = ZERO
        new_items = []
        for it in params.items:
            amount = money(it.quantity * it.unit_price)
            total_amount += amount
            new_items.append({
                "order_id": order_id,
                "product_id": it.product_id,
                "quantity": it.quantity,
                "received_qty": ZERO,
                "unit_price": it.unit_price,
                "amount": amount,
                "remark": it.remark,
            })
        repo.insert_order_items_batch(session, new_items)
        total_amount = money(total_amount)
    else:
        total_amount = order.total_amount

    # 更新主表字段
    values: dict = {}
    if params.supplier_id is not None:
        values["supplier_id"] = params.supplier_id
    if params.order_date is not None:
        values["order_date"] = params.order_date
    if params.expect_date is not None:
        values["expect_date"] = params.expect_date
    if params.remark is not None:
        values["remark"] = params.remark
    if params.items is not None:
        values["total_amount"] = total_amount
    repo.update_order(session, order_id, values)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="修改采购单",
        target_type="pur_order", target_id=order_id,
        after_value={"changed_fields": list(values.keys())},
        result=1,
    )
    session.commit()
    logger.info(f"修改采购单：order_no={order.order_no}, 变更={list(values.keys())}")
    return get_order_detail(session, order_id)


# ---------- 5.5 POST /purchases/{id}/audit ----------


def audit_order(
    session: Session, order_id: int, params: AuditRequest, *, operator_id: int,
) -> None:
    """审核采购订单（spec 5.5）：仅 DRAFT 可审核。

    approved=true → AUDITED；approved=false → 保持 DRAFT + 写 reject_reason。
    """
    order = repo.get_order_by_id(session, order_id)
    if order is None:
        raise BusinessError(ErrorCode.PURCHASE_ORDER_NOT_FOUND, f"采购订单 id={order_id} 不存在")
    if order.status != "DRAFT":
        raise BusinessError(
            ErrorCode.PURCHASE_STATUS_FORBID, f"只有草稿状态可审核（当前 {order.status}）"
        )

    if params.approved:
        repo.update_order(session, order_id, {
            "status": "AUDITED",
            "audited_by": operator_id,
            "audited_at": _now(),
        })
        action = "审核采购单"
    else:
        # 驳回：保持 DRAFT，写 reject_reason
        repo.update_order(session, order_id, {"reject_reason": params.reason})
        action = "驳回采购单"

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action=action,
        target_type="pur_order", target_id=order_id,
        after_value={"approved": params.approved, "reason": params.reason},
        result=1,
    )
    session.commit()
    logger.info(f"{action}：order_no={order.order_no}")


# ---------- 5.6 POST /purchases/{id}/void ----------


def void_order(
    session: Session, order_id: int, params: VoidRequest, *, operator_id: int,
) -> None:
    """作废采购订单（spec 5.6）：仅 DRAFT 可作废。

    ⛔ 已收货的不可能回到 DRAFT，无需回补库存（校验通过即可）。
    """
    order = repo.get_order_by_id(session, order_id)
    if order is None:
        raise BusinessError(ErrorCode.PURCHASE_ORDER_NOT_FOUND, f"采购订单 id={order_id} 不存在")
    if order.status != "DRAFT":
        raise BusinessError(
            ErrorCode.PURCHASE_STATUS_FORBID, f"只有草稿状态可作废（当前 {order.status}）"
        )

    # remark 追加作废原因
    new_remark = f"{order.remark or ''}[作废:{params.reason}]".strip()
    repo.update_order(session, order_id, {"status": "VOID", "remark": new_remark[:255]})

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="作废采购单",
        target_type="pur_order", target_id=order_id,
        after_value={"reason": params.reason},
        result=1,
    )
    session.commit()
    logger.info(f"作废采购单：order_no={order.order_no}, reason={params.reason}")


# ---------- 5.1 GET /purchases ----------


def list_orders(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    supplier_id: int | None = None,
    store_id: int | None = None,
) -> tuple[list[PurchaseOrder], int]:
    """采购订单列表（spec 5.1）。"""
    rows, total = repo.list_orders(
        session, page=page, page_size=page_size,
        status=status, supplier_id=supplier_id, store_id=store_id,
    )
    result = [
        PurchaseOrder(
            id=o.id,
            order_no=o.order_no,
            supplier_id=o.supplier_id,
            supplier_name=supplier_name,
            store_id=o.store_id,
            order_date=o.order_date,
            expect_date=o.expect_date,
            total_amount=o.total_amount,
            source_type=o.source_type,
            status=o.status,
            created_by_name=created_name,
            audited_at=o.audited_at,
            reject_reason=o.reject_reason,
            remark=o.remark,
        )
        for o, supplier_name, created_name in rows
    ]
    return result, total


# ---------- 5.2 GET /purchases/{id} ----------


def get_order_detail(session: Session, order_id: int) -> PurchaseOrder:
    """采购订单详情（含 items，JOIN 商品与单位，spec 5.2 + 冲突表 ④）。"""
    order = repo.get_order_by_id(session, order_id)
    if order is None:
        raise BusinessError(ErrorCode.PURCHASE_ORDER_NOT_FOUND, f"采购订单 id={order_id} 不存在")

    supplier = supplier_repository.get_supplier_by_id(session, order.supplier_id)
    from app.repositories.sale_repository import get_user_real_name
    created_by_name = get_user_real_name(session, order.created_by)

    # items：两次查询内存组装（JOIN 商品名/单位名）
    item_rows = repo.list_order_items_with_product(session, order_id)
    items = [
        PurchaseOrderItem(
            id=it.id,
            product_id=it.product_id,
            product_code=product_code,
            product_name=product_name,
            spec=spec,
            unit_name=unit_name,
            quantity=it.quantity,
            received_qty=it.received_qty,
            unit_price=it.unit_price,
            amount=it.amount,
            remark=it.remark,
        )
        for it, product_code, product_name, spec, unit_name in item_rows
    ]

    return PurchaseOrder(
        id=order.id,
        order_no=order.order_no,
        supplier_id=order.supplier_id,
        supplier_name=supplier.supplier_name if supplier else None,
        store_id=order.store_id,
        order_date=order.order_date,
        expect_date=order.expect_date,
        total_amount=order.total_amount,
        source_type=order.source_type,
        status=order.status,
        created_by_name=created_by_name,
        audited_at=order.audited_at,
        reject_reason=order.reject_reason,
        remark=order.remark,
        items=items,
    )


# 让 lint 知道 Decimal 被间接使用
_ = Decimal
