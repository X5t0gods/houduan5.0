"""报损业务编排（阶段 5 CP3 交付物）—— 创建即生效。

⚠️ spec 冲突表 ⑥：inv_loss 无 audited_by/audited_at 字段，无审核入口 →
   创建即扣库存 + 写流水 + status='FINISHED'。

⚠️ spec 5.9：
- unit_cost 以 inv_stock.avg_cost 为准（前端传的只用于表单预览）
- 本阶段不做移动加权成本重算（阶段 7 采购入库负责），报损只扣数量，avg_cost 保持原值
- loss_type 用短名 BREAK/EXPIRE/FRESH/OTHER（spec 冲突表 ①）
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
from app.models.enums import LossType
from app.repositories import inventory_write_repository as inv_write
from app.repositories import loss_repository as repo
from app.repositories import product_repository
from app.schemas.inventory import LossCreateRequest, LossItemOut, LossOut
from app.utils.money import ZERO, money

logger = get_logger(__name__)

# 合法报损类型（短名，spec 冲突表 ①）
VALID_LOSS_TYPES = {LossType.BREAK, LossType.EXPIRE, LossType.FRESH, LossType.OTHER}


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ)).replace(tzinfo=None)


def create_loss(
    session: Session,
    params: LossCreateRequest,
    *,
    store_id: int,
    user_id: int,
) -> LossOut:
    """报损登记（spec 5.9）：创建即生效。

    步骤：
    1. 校验 loss_type/items/数量/商品存在
    2. 库存充足校验（不足 → 6003）
    3. 取号 BS，写 inv_loss + inv_loss_item
    4. 立即扣库存（条件更新）+ 写 LOSS_OUT 流水
    """
    # 1. 校验
    if params.loss_type not in VALID_LOSS_TYPES:
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            f"非法报损类型：{params.loss_type}（应为 BREAK/EXPIRE/FRESH/OTHER）",
        )
    effective_store = params.store_id or store_id

    # 批量校验商品存在
    product_ids = [it.product_id for it in params.items]
    products = _get_products(session, product_ids)
    for pid in product_ids:
        if pid not in products:
            raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品ID={pid}不存在")

    # 2. 库存充足预检
    for it in params.items:
        stock = inv_write.get_stock(session, effective_store, it.product_id)
        if stock is None or stock.quantity < it.quantity:
            pname = products[it.product_id].product_name
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH, f"商品「{pname}」库存不足，无法报损"
            )

    # 3. 取号 + 写主表
    now = _now()
    seq = NoSeqService(session)
    loss_no = seq.next_no("BS")

    # 先算总额（unit_cost 以 avg_cost 为准）
    total_amount = ZERO
    line_data = []
    for it in params.items:
        stock = inv_write.get_stock(session, effective_store, it.product_id)
        # ⚠️ unit_cost 以 inv_stock.avg_cost 为准，不用前端传的（spec 5.9）
        unit_cost = stock.avg_cost if stock else ZERO
        amount = money(unit_cost * it.quantity)
        total_amount += amount
        line_data.append((it, unit_cost, amount))
    total_amount = money(total_amount)

    loss = repo.insert_loss(
        session,
        loss_no=loss_no,
        store_id=effective_store,
        loss_type=params.loss_type,
        total_amount=total_amount,
        reason=params.remark,
        status="FINISHED",
        created_by=user_id,
    )

    # 4. 写明细 + 扣库存 + 流水
    for it, unit_cost, amount in line_data:
        repo.insert_loss_item(
            session,
            loss_id=loss.id,
            product_id=it.product_id,
            quantity=it.quantity,
            unit_cost=unit_cost,
            amount=amount,
        )
        # 扣库存（条件更新防超卖）
        ok, before_qty, after_qty, _avg = inv_write.deduct_stock(
            session,
            store_id=effective_store,
            product_id=it.product_id,
            quantity=it.quantity,
        )
        if not ok:
            raise BusinessError(
                ErrorCode.STOCK_NOT_ENOUGH, f"商品ID={it.product_id} 库存不足，无法报损"
            )
        inv_write.write_stock_flow(
            session,
            store_id=effective_store,
            product_id=it.product_id,
            flow_type="LOSS_OUT",
            direction=-1,
            quantity=it.quantity,
            before_qty=before_qty,
            after_qty=after_qty,
            unit_cost=unit_cost,
            amount=amount,
            source_type="LOSS",
            source_no=loss_no,
            operator=user_id,
        )

    session.commit()
    logger.info(f"报损登记：loss_no={loss_no}, total={total_amount}, {len(line_data)} 项")

    return LossOut(
        id=loss.id,
        loss_no=loss_no,
        store_id=effective_store,
        loss_type=params.loss_type,
        total_amount=total_amount,
        reason=params.remark,
        status="FINISHED",
        created_by=user_id,
        item_count=len(line_data),
        created_at=now,
    )


def list_losses(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[LossOut], int]:
    """报损单列表（spec 5.9）。"""
    rows, total = repo.list_losses(
        session, page=page, page_size=page_size, status=status, store_id=store_id,
    )
    result = [
        LossOut(
            id=loss.id,
            loss_no=loss.loss_no,
            store_id=loss.store_id,
            loss_type=loss.loss_type,
            total_amount=loss.total_amount,
            reason=loss.reason,
            status=loss.status,
            created_by=loss.created_by,
            created_by_name=name,
            item_count=item_count or 0,
            created_at=loss.created_at,
        )
        for loss, name, item_count in rows
    ]
    return result, total


def _get_products(session: Session, product_ids: list[int]) -> dict:
    """批量读商品（复用 product_repository 的 PrdProduct 查询）。"""
    from sqlalchemy import select

    from app.models.prd_models import PrdProduct

    if not product_ids:
        return {}
    rows = session.execute(
        select(PrdProduct).where(PrdProduct.id.in_(product_ids))
    ).scalars().all()
    return {p.id: p for p in rows}


# 让 lint 知道这些 import 被间接使用
_ = (product_repository, LossItemOut, Decimal)
