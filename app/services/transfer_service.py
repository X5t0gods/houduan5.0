"""调拨业务编排（阶段 5 CP3 交付物）—— 一步到位。

⚠️ spec 冲突表 ⑤：缺少调出/调入/作废接口，DRAFT 状态会永远卡住无法演示。
   **补充设计**：创建调拨单时同步完成调出与调入（一个事务内）：
   1. 调出门店：条件更新扣减库存 + 写 TRANSFER_OUT 流水
   2. 调入门店：upsert inv_stock（不存在则新建，safe_qty=0）+ 写 TRANSFER_IN 流水
   3. status='IN'、out_at=in_at=当前时间
   代价：失去"调出后再确认收货"的两阶段能力（列入待办，阶段 7+ 补）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.repositories import inventory_write_repository as inv_write
from app.repositories import transfer_repository as repo
from app.schemas.inventory import TransferCreateRequest, TransferOut
from app.utils.money import ZERO, money

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ)).replace(tzinfo=None)


def create_transfer(
    session: Session,
    params: TransferCreateRequest,
    *,
    user_id: int,
) -> TransferOut:
    """调拨申请（spec 5.10）：一步到位完成调出+调入。

    校验：
    - from_store_id != to_store_id → 2001（前端已拦，后端也拦）
    - 两门店都存在且 status=1 → 2001
    - 调出门店库存充足 → 6003
    - items 非空、数量 > 0
    """
    # 1. 校验门店不同
    if params.from_store_id == params.to_store_id:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "调出门店与调入门店不能相同")

    # 2. 校验门店存在且营业
    from_store = repo.get_store(session, params.from_store_id)
    to_store = repo.get_store(session, params.to_store_id)
    if from_store is None or from_store.status != 1:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "调出门店不存在或已停业")
    if to_store is None or to_store.status != 1:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "调入门店不存在或已停业")

    # 3. 批量校验商品存在
    product_ids = [it.product_id for it in params.items]
    products = _get_products(session, product_ids)
    for pid in product_ids:
        if pid not in products:
            raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品ID={pid}不存在")

    # 4. 调出门店库存充足预检
    for it in params.items:
        stock = inv_write.get_stock(session, params.from_store_id, it.product_id)
        if stock is None or stock.quantity < it.quantity:
            pname = products[it.product_id].product_name
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH, f"商品「{pname}」调出门店库存不足"
            )

    # 5. 取号 + 写主表（一步到位 status=IN）
    now = _now()
    seq = NoSeqService(session)
    transfer_no = seq.next_no("DB")

    transfer = repo.insert_transfer(
        session,
        transfer_no=transfer_no,
        from_store_id=params.from_store_id,
        to_store_id=params.to_store_id,
        status="IN",  # ⚠️ 一步到位（spec 冲突表 ⑤）
        created_by=user_id,
        out_at=now,
        in_at=now,
        remark=params.remark,
    )

    # 6. 逐商品：调出扣减 + 调入 upsert + 两条流水
    for it in params.items:
        from_stock = inv_write.get_stock(session, params.from_store_id, it.product_id)
        # ⚠️ unit_cost 以调出门店 avg_cost 为准（不用前端传的）
        unit_cost = from_stock.avg_cost if from_stock else ZERO
        amount = money(unit_cost * it.quantity)

        repo.insert_transfer_item(
            session,
            transfer_id=transfer.id,
            product_id=it.product_id,
            quantity=it.quantity,
            unit_cost=unit_cost,
            amount=amount,
        )

        # 调出：条件更新扣减
        ok, out_before, out_after, _avg = inv_write.deduct_stock(
            session,
            store_id=params.from_store_id,
            product_id=it.product_id,
            quantity=it.quantity,
        )
        if not ok:
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH, f"商品ID={it.product_id} 调出门店库存不足"
            )
        inv_write.write_stock_flow(
            session,
            store_id=params.from_store_id,
            product_id=it.product_id,
            flow_type="TRANSFER_OUT",
            direction=-1,
            quantity=it.quantity,
            before_qty=out_before,
            after_qty=out_after,
            unit_cost=unit_cost,
            amount=amount,
            source_type="TRANSFER",
            source_no=transfer_no,
            operator=user_id,
        )

        # 调入：upsert（不存在则新建 safe_qty=0）
        in_before, in_after = inv_write.upsert_stock_add(
            session,
            store_id=params.to_store_id,
            product_id=it.product_id,
            quantity=it.quantity,
            avg_cost=unit_cost,
        )
        inv_write.write_stock_flow(
            session,
            store_id=params.to_store_id,
            product_id=it.product_id,
            flow_type="TRANSFER_IN",
            direction=1,
            quantity=it.quantity,
            before_qty=in_before,
            after_qty=in_after,
            unit_cost=unit_cost,
            amount=amount,
            source_type="TRANSFER",
            source_no=transfer_no,
            operator=user_id,
        )

    session.commit()
    logger.info(
        f"调拨完成：transfer_no={transfer_no}, "
        f"{params.from_store_id}→{params.to_store_id}, {len(params.items)} 项"
    )

    return TransferOut(
        id=transfer.id,
        transfer_no=transfer_no,
        from_store_id=params.from_store_id,
        to_store_id=params.to_store_id,
        from_store=from_store.store_name,
        to_store=to_store.store_name,
        status="IN",
        item_count=len(params.items),
        created_by=user_id,
        out_at=now,
        in_at=now,
        remark=params.remark,
        created_at=now,
    )


def list_transfers(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[TransferOut], int]:
    """调拨单列表（spec 5.10）。"""
    rows, total = repo.list_transfers(
        session, page=page, page_size=page_size, status=status, store_id=store_id,
    )
    result = [
        TransferOut(
            id=t.id,
            transfer_no=t.transfer_no,
            from_store_id=t.from_store_id,
            to_store_id=t.to_store_id,
            from_store=from_name,
            to_store=to_name,
            status=t.status,
            item_count=item_count or 0,
            created_by=t.created_by,
            created_by_name=created_name,
            out_at=t.out_at,
            in_at=t.in_at,
            remark=t.remark,
            created_at=t.created_at,
        )
        for t, from_name, to_name, created_name, item_count in rows
    ]
    return result, total


def _get_products(session: Session, product_ids: list[int]) -> dict:
    """批量读商品。"""
    from sqlalchemy import select

    from app.models.prd_models import PrdProduct

    if not product_ids:
        return {}
    rows = session.execute(
        select(PrdProduct).where(PrdProduct.id.in_(product_ids))
    ).scalars().all()
    return {p.id: p for p in rows}


# 让 lint 知道 Decimal 被间接使用
_ = Decimal
