"""采购收货业务编排（阶段 6 CP4 交付物）—— **本阶段最核心**。

包含：
- receive_order：收货事务 T2（spec 5.7）+ MAVG 成本重算
- direct_receive：无单快速入库（spec 5.8 + 4.3，建单+收货+入库一体，status=FINISHED）
- create_return：采购退货（spec 5.9，创建即生效，冲减应付允许负）

⚠️ MAVG 成本重算（spec 4.2，本阶段核心）：
   新成本 = (原量×原成本 + 入库量×单价) / (原量+入库量)；原库存≤0 → 用本次单价。
   算法在 costing_service（纯函数），本 service 调用后传给 repository 写库。

⚠️ 批次只给 prd_product.is_perishable=1 的商品建（spec 冲突表 ⑤⑥，⛔不信前端 idx%3 假值）。
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
from app.models.inv_models import InvBatch
from app.models.prd_models import PrdProduct
from app.repositories import inventory_write_repository as inv_write
from app.repositories import purchase_order_repository as order_repo
from app.repositories import purchase_return_repository as return_repo
from app.repositories import receipt_repository as receipt_repo
from app.repositories import supplier_repository
from app.repositories.auth_repository import write_operation_log
from app.schemas.purchase import (
    DirectReceiveParams,
    PurchaseReturnOut,
    PurchaseReturnParams,
    ReceiptResult,
    ReceiveParams,
)
from app.services.costing_service import calc_weighted_avg_cost
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


def _stock_in_and_flow(
    session: Session,
    *,
    store_id: int,
    product: PrdProduct,
    receive_qty: Decimal,
    unit_price: Decimal,
    source_no: str,
    operator_id: int,
    production_date: date | None,
    expiry_date: date | None,
    batch_seq: NoSeqService,
) -> str | None:
    """单商品入库 + MAVG 成本重算 + 流水 + 批次（receive/direct 共用）。

    Returns:
        batch_no（保质期商品）或 None（非保质期商品）。
    """
    # 1. 读当前库存 → MAVG 重算新成本
    stock = inv_write.get_stock(session, store_id, product.id)
    old_qty = stock.quantity if stock else ZERO
    old_cost = stock.avg_cost if stock else ZERO
    new_avg_cost = calc_weighted_avg_cost(old_qty, old_cost, receive_qty, unit_price)

    # 2. 入库（quantity += receive_qty, avg_cost = 新成本）
    before_qty, after_qty = inv_write.receive_stock(
        session,
        store_id=store_id,
        product_id=product.id,
        add_qty=receive_qty,
        new_avg_cost=new_avg_cost,
    )

    # 3. 写库存流水（PURCHASE_IN, direction=1, unit_cost=本次单价）
    amount = money(receive_qty * unit_price)
    inv_write.write_stock_flow(
        session,
        store_id=store_id,
        product_id=product.id,
        flow_type="PURCHASE_IN",
        direction=1,
        quantity=receive_qty,
        before_qty=before_qty,
        after_qty=after_qty,
        unit_cost=unit_price,
        amount=amount,
        source_type="RECEIPT",
        source_no=source_no,
        operator=operator_id,
    )

    # 4. 保质期商品建批次（⛔ 仅 prd_product.is_perishable=1，spec 冲突表 ⑤⑥）
    batch_no = None
    if product.is_perishable == 1:
        if expiry_date is None:
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING,
                f"保质期商品「{product.product_name}」必须录入到期日",
            )
        batch_no = batch_seq.next_no("BATCH", scope=product.product_code)
        session.add(InvBatch(
            batch_no=batch_no,
            product_id=product.id,
            store_id=store_id,
            production_date=production_date,
            expiry_date=expiry_date,
            init_qty=receive_qty,
            remain_qty=receive_qty,
            unit_cost=unit_price,
            status=1,
        ))
        session.flush()

    return batch_no


# ---------- 5.7 POST /purchases/{id}/receive ----------


def receive_order(
    session: Session,
    order_id: int,
    params: ReceiveParams,
    *,
    operator_id: int,
) -> ReceiptResult:
    """收货入库（spec 5.7，事务 T2）。

    校验顺序：订单存在(4003) → 状态∈{AUDITED,PARTIAL}(4001) → items非空/receive_qty>0(2001)
    → 超收(4002) → 商品属于订单(2001)。
    """
    # 1. 订单存在
    order = order_repo.get_order_by_id(session, order_id)
    if order is None:
        raise BusinessError(ErrorCode.PURCHASE_ORDER_NOT_FOUND, f"采购订单 id={order_id} 不存在")

    # 2. 状态校验（只有 AUDITED / PARTIAL 可收货）
    if order.status not in ("AUDITED", "PARTIAL"):
        raise BusinessError(
            ErrorCode.ORDER_STATUS_INVALID, f"当前状态({order.status})不允许收货"
        )

    # 3. items 非空、receive_qty > 0
    if not params.items:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "收货明细不能为空")
    for it in params.items:
        if it.receive_qty <= 0:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "收货数量必须大于 0")

    store_id = params.store_id or order.store_id

    # 4+5. 超收校验 + 商品属于订单
    order_items = order_repo.list_order_items(session, order_id)
    order_item_map = {oi.product_id: oi for oi in order_items}
    for it in params.items:
        oi = order_item_map.get(it.product_id)
        if oi is None:
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING, f"商品ID={it.product_id} 不属于该订单"
            )
        remaining = oi.quantity - oi.received_qty
        if it.receive_qty > remaining:
            raise BusinessError(
                ErrorCode.RECEIVE_QTY_EXCEED,
                f"商品ID={it.product_id} 收货量({it.receive_qty})超出剩余可收量({remaining})",
            )

    # 商品信息（is_perishable / product_code / name）
    products = _get_products(session, [it.product_id for it in params.items])

    # ---- 事务 T2 ----
    seq = NoSeqService(session)
    receipt_no = seq.next_no("SH")

    # 先算收货总额
    total_amount = ZERO
    for it in params.items:
        total_amount += money(it.receive_qty * it.unit_price)
    total_amount = money(total_amount)

    receipt = receipt_repo.insert_receipt(
        session,
        receipt_no=receipt_no,
        order_id=order_id,
        supplier_id=order.supplier_id,
        store_id=store_id,
        total_amount=total_amount,
        is_direct=0,
        diff_remark=params.diff_remark,
        received_by=operator_id,
        status="FINISHED",
        received_at=_now(),
    )

    # 逐商品：明细 + 累加received_qty + 入库+MAVG+流水+批次
    for it in params.items:
        product = products[it.product_id]
        oi = order_item_map[it.product_id]

        batch_no = _stock_in_and_flow(
            session,
            store_id=store_id,
            product=product,
            receive_qty=it.receive_qty,
            unit_price=it.unit_price,
            source_no=receipt_no,
            operator_id=operator_id,
            production_date=it.production_date,
            expiry_date=it.expiry_date,
            batch_seq=seq,
        )

        receipt_repo.insert_receipt_item(
            session,
            receipt_id=receipt.id,
            product_id=it.product_id,
            order_qty=oi.quantity,
            receive_qty=it.receive_qty,
            unit_price=it.unit_price,
            amount=money(it.receive_qty * it.unit_price),
            batch_no=batch_no,
            production_date=it.production_date if product.is_perishable == 1 else None,
            expiry_date=it.expiry_date if product.is_perishable == 1 else None,
        )

        # 累加 pur_order_item.received_qty
        order_repo.update_order_item(session, oi.id, {
            "received_qty": oi.received_qty + it.receive_qty,
        })

    # 判定订单状态：全部收完 → FINISHED，否则 PARTIAL
    new_status = _calc_order_status(session, order_id)
    order_repo.update_order(session, order_id, {"status": new_status})

    # 应付账款 += 收货金额
    supplier_repository.adjust_balance_payable(session, order.supplier_id, total_amount)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="采购收货",
        target_type="pur_receipt", target_id=receipt.id,
        after_value={"receipt_no": receipt_no, "total_amount": str(total_amount)},
        result=1,
    )
    session.commit()
    logger.info(f"采购收货：receipt_no={receipt_no}, total={total_amount}, 订单→{new_status}")

    return ReceiptResult(
        receipt_no=receipt_no,
        order_id=order_id,
        order_status=new_status,
        total_amount=total_amount,
        is_direct=0,
    )


def _calc_order_status(session: Session, order_id: int) -> str:
    """判定订单状态：全部明细 received_qty >= quantity → FINISHED，否则 PARTIAL。"""
    items = order_repo.list_order_items(session, order_id)
    all_received = all((it.received_qty >= it.quantity) for it in items)
    return "FINISHED" if all_received else "PARTIAL"


# ---------- 5.8 POST /purchases/direct-receive ----------


def direct_receive(
    session: Session,
    params: DirectReceiveParams,
    *,
    store_id: int,
    operator_id: int,
) -> ReceiptResult:
    """无单快速入库（spec 5.8 + 4.3）：建单+收货+入库一体，订单直接 FINISHED。

    ⚠️ 数量字段是 quantity（不是 receive_qty）。
    ⚠️ 建 pur_order 并关联（order_id 非空），is_direct=1（spec 冲突表 ⑨ 补充设计）。
    """
    # 校验供应商（停用 → 3001）
    supplier = supplier_repository.get_supplier_by_id(session, params.supplier_id)
    if supplier is None:
        raise BusinessError(ErrorCode.SUPPLIER_NOT_FOUND, f"供应商 id={params.supplier_id} 不存在")
    if supplier.status == 0:
        raise BusinessError(
            ErrorCode.SUPPLIER_DISABLED, f"供应商「{supplier.supplier_name}」已停用"
        )
    if not params.items:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "入库明细不能为空")
    for it in params.items:
        if it.quantity <= 0:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "入库数量必须大于 0")

    products = _get_products(session, [it.product_id for it in params.items])
    for pid in products:
        if products[pid].status == 0:
            raise BusinessError(
                ErrorCode.PRODUCT_DUPLICATE, f"商品「{products[pid].product_name}」已停用"
            )

    effective_store = params.store_id or store_id
    now = _now()
    seq = NoSeqService(session)

    # 算总额
    total_amount = ZERO
    for it in params.items:
        total_amount += money(it.quantity * it.unit_price)
    total_amount = money(total_amount)

    # 1. 建订单（status=FINISHED）
    order_no = seq.next_no("CG")
    order = order_repo.insert_order(
        session,
        order_no=order_no,
        supplier_id=params.supplier_id,
        store_id=effective_store,
        order_date=_today(),
        total_amount=total_amount,
        source_type="MANUAL",
        status="FINISHED",
        remark=params.remark,
        created_by=operator_id,
        audited_by=operator_id,
        audited_at=now,
    )
    # 建订单明细（quantity=received_qty=本次数量）
    order_items = []
    for it in params.items:
        order_items.append({
            "order_id": order.id,
            "product_id": it.product_id,
            "quantity": it.quantity,
            "received_qty": it.quantity,
            "unit_price": it.unit_price,
            "amount": money(it.quantity * it.unit_price),
        })
    order_repo.insert_order_items_batch(session, order_items)

    # 2. 建收货单（order_id 关联，is_direct=1）
    receipt_no = seq.next_no("SH")
    receipt = receipt_repo.insert_receipt(
        session,
        receipt_no=receipt_no,
        order_id=order.id,  # ⚠️ 关联订单（spec 4.3，故意与表注释"无单入库order_id为空"不一致）
        supplier_id=params.supplier_id,
        store_id=effective_store,
        total_amount=total_amount,
        is_direct=1,
        received_by=operator_id,
        status="FINISHED",
        received_at=now,
    )

    # 3. 逐商品：入库+MAVG+流水+批次 + 收货明细
    for it in params.items:
        product = products[it.product_id]
        batch_no = _stock_in_and_flow(
            session,
            store_id=effective_store,
            product=product,
            receive_qty=it.quantity,
            unit_price=it.unit_price,
            source_no=receipt_no,
            operator_id=operator_id,
            production_date=it.production_date,
            expiry_date=it.expiry_date,
            batch_seq=seq,
        )
        receipt_repo.insert_receipt_item(
            session,
            receipt_id=receipt.id,
            product_id=it.product_id,
            order_qty=it.quantity,
            receive_qty=it.quantity,
            unit_price=it.unit_price,
            amount=money(it.quantity * it.unit_price),
            batch_no=batch_no,
            production_date=it.production_date if product.is_perishable == 1 else None,
            expiry_date=it.expiry_date if product.is_perishable == 1 else None,
        )

    # 4. 应付账款 += 入库金额
    supplier_repository.adjust_balance_payable(session, params.supplier_id, total_amount)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="无单快速入库",
        target_type="pur_receipt", target_id=receipt.id,
        after_value={
            "receipt_no": receipt_no, "order_no": order_no,
            "total_amount": str(total_amount),
        },
        result=1,
    )
    session.commit()
    logger.info(f"无单入库：order_no={order_no}, receipt_no={receipt_no}, total={total_amount}")

    return ReceiptResult(
        receipt_no=receipt_no,
        order_id=order.id,
        order_status="FINISHED",
        total_amount=total_amount,
        is_direct=1,
    )


# ---------- 5.9 POST /purchases/returns ----------


def create_return(
    session: Session,
    params: PurchaseReturnParams,
    *,
    store_id: int,
    operator_id: int,
) -> PurchaseReturnOut:
    """采购退货（spec 5.9，创建即生效）。

    ⚠️ 扣库存（条件更新，不足→6003）+ PURCHASE_RETURN_OUT 流水 + 冲减应付（允许负）。
    ⛔ 不重算 avg_cost（简化处理，与阶段5报损口径一致）。
    ⚠️ unit_price 缺失取 prd_product.purchase_price 兜底。
    """
    supplier = supplier_repository.get_supplier_by_id(session, params.supplier_id)
    if supplier is None:
        raise BusinessError(ErrorCode.SUPPLIER_NOT_FOUND, f"供应商 id={params.supplier_id} 不存在")
    if not params.items:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "退货明细不能为空")

    effective_store = params.store_id or store_id
    products = _get_products(session, [it.product_id for it in params.items])

    # 校验商品存在 + 库存充足（条件更新在扣减时兜底，这里预检给友好提示）
    for it in params.items:
        if it.product_id not in products:
            raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品ID={it.product_id}不存在")
        if it.quantity <= 0:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "退货数量必须大于 0")
        stock = inv_write.get_stock(session, effective_store, it.product_id)
        if stock is None or stock.quantity < it.quantity:
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH,
                f"商品「{products[it.product_id].product_name}」库存不足，无法退货",
            )

    seq = NoSeqService(session)
    return_no = seq.next_no("CT")

    # 算退货总额（unit_price 缺失取 purchase_price）
    total_amount = ZERO
    line_data = []
    for it in params.items:
        product = products[it.product_id]
        unit_price = it.unit_price if it.unit_price is not None else product.purchase_price
        amount = money(it.quantity * unit_price)
        total_amount += amount
        line_data.append((it, product, unit_price, amount))
    total_amount = money(total_amount)

    ret = return_repo.insert_return(
        session,
        return_no=return_no,
        supplier_id=params.supplier_id,
        store_id=effective_store,
        source_receipt_id=params.source_receipt_id,
        total_amount=total_amount,
        reason=params.reason,
        status="FINISHED",
        created_by=operator_id,
    )

    for it, product, unit_price, amount in line_data:
        return_repo.insert_return_item(
            session,
            return_id=ret.id,
            product_id=it.product_id,
            quantity=it.quantity,
            unit_price=unit_price,
            amount=amount,
        )
        # 扣库存（条件更新，不足→6003）
        ok, before_qty, after_qty, avg_cost = inv_write.deduct_stock(
            session,
            store_id=effective_store,
            product_id=it.product_id,
            quantity=it.quantity,
        )
        if not ok:
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH, f"商品「{product.product_name}」库存不足，无法退货"
            )
        # ⛔ 不重算 avg_cost（spec 5.9）
        inv_write.write_stock_flow(
            session,
            store_id=effective_store,
            product_id=it.product_id,
            flow_type="PURCHASE_RETURN_OUT",
            direction=-1,
            quantity=it.quantity,
            before_qty=before_qty,
            after_qty=after_qty,
            unit_cost=avg_cost,
            amount=money(it.quantity * avg_cost),
            source_type="PURCHASE_RETURN",
            source_no=return_no,
            operator=operator_id,
        )

    # 冲减应付（允许为负，⛔不校验余额充足）
    supplier_repository.adjust_balance_payable(session, params.supplier_id, -total_amount)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="采购退货",
        target_type="pur_return", target_id=ret.id,
        after_value={"return_no": return_no, "total_amount": str(total_amount)},
        result=1,
    )
    session.commit()

    updated_supplier = supplier_repository.get_supplier_by_id(session, params.supplier_id)
    logger.info(f"采购退货：return_no={return_no}, total={total_amount}")

    return PurchaseReturnOut(
        id=ret.id,
        return_no=return_no,
        supplier_id=params.supplier_id,
        store_id=effective_store,
        total_amount=total_amount,
        reason=params.reason,
        status="FINISHED",
        balance_payable=updated_supplier.balance_payable,
    )


# 让 lint 知道 Decimal 被间接使用
_ = Decimal
