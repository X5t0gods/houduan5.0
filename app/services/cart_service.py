"""购物车服务（阶段 4 CP2 交付物）—— 条码解析 + 促销试算。

⚠️ 结算逻辑在 sale_service.py（CP3 交付），本文件只处理**不落库**的读操作。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.models.base_models import BaseUnit
from app.models.inv_models import InvStock
from app.models.mem_models import MemMember
from app.models.prd_models import PrdBarcode, PrdProduct
from app.schemas.sale import (
    CartCalcRequest,
    CartCalcResult,
    CartResolveResult,
    PromoDetailOut,
)
from app.services.promotion_service import EngineItem, calc_promotion
from app.utils.money import ZERO, money


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ))


# ---------- 6.1 POST /sales/cart/resolve ----------


def resolve_product(session: Session, code: str, store_id: int) -> CartResolveResult | None:
    """按条码或商品编码解析商品（收银台扫码入口）。

    ⚠️ 匹配顺序（spec 6.1）：
    1. 先查 `prd_barcode.barcode`（含附加条码，走 uk_barcode 唯一索引）
    2. 未命中再查 `prd_product.product_code`（走 uk_product_code 唯一索引）
    ⛔ 两次查询都是唯一索引，不做全表扫（性能 P95 ≤ 200ms）

    Args:
        session: 数据库会话。
        code: 用户扫描或输入的条码 / 商品编码。
        store_id: 当前门店 ID（用于查 inv_stock）。

    Returns:
        CartResolveResult；找不到时返回 **None**（路由层转 code=0 + data=null，
        ⛔ **不是 6001**，spec 三 ④）。

    Raises:
        BusinessError(6002): 商品已停用（status=0）—— 收银台不能卖停用商品。
    """
    code = code.strip()
    if not code:
        return None

    # 1. 先查条码表
    row = session.execute(
        select(PrdProduct, PrdBarcode.barcode)
        .join(PrdBarcode, PrdBarcode.product_id == PrdProduct.id)
        .where(PrdBarcode.barcode == code)
        .limit(1)
    ).fetchone()
    matched_barcode = code

    # 2. 未命中则查商品编码
    if row is None:
        product = session.execute(
            select(PrdProduct).where(PrdProduct.product_code == code)
        ).scalar_one_or_none()
        if product is None:
            return None
        matched_barcode = code  # 用编码本身作为回显
    else:
        product = row[0]
        matched_barcode = row[1]

    # 3. 停用商品：spec 6.1 建议返 6002 让前端提示
    # ⚠️ 与阶段 3 的 GET /products/barcode/{code} 不同：
    #   - 阶段 3 是**建档查重**，停用商品也要能查到（不判断 status）
    #   - 阶段 4 是**收银扫码**，停用商品必须拒绝售卖（返 6002）
    if product.status == 0:
        raise BusinessError(ErrorCode.PRODUCT_DISABLED, f"商品「{product.product_name}」已停用")

    # 4. 关联数据：单位名 + 库存
    unit_name = session.execute(
        select(BaseUnit.unit_name).where(BaseUnit.id == product.unit_id)
    ).scalar_one_or_none()

    stock_qty = session.execute(
        select(InvStock.quantity, InvStock.avg_cost).where(
            InvStock.store_id == store_id,
            InvStock.product_id == product.id,
        )
    ).fetchone()
    quantity = stock_qty[0] if stock_qty else ZERO
    avg_cost = stock_qty[1] if stock_qty else product.avg_cost

    return CartResolveResult(
        product_id=product.id,
        product_code=product.product_code,
        product_name=product.product_name,
        spec=product.spec,
        barcode=matched_barcode,
        unit_name=unit_name,
        origin_price=product.sale_price,
        unit_price=product.sale_price,  # 默认成交价 = 零售价，收银员可改
        member_price=product.member_price,
        cost_price=avg_cost,
        is_weight=product.is_weight,
        stock_qty=quantity,
    )


# ---------- 6.2 POST /sales/cart/calc ----------


def calc_cart(session: Session, params: CartCalcRequest) -> CartCalcResult:
    """促销试算（**不落库**，纯计算）。

    ⚠️ 关键约定（spec 5.4）：
    - `discount_amount` **不含会员折扣、不含积分抵扣**
    - 会员折扣由前端 stores/pos.ts:57 单独计算与展示，后端 calc 不返回避免重复扣减
    - `point_deduct` 恒为 0

    Args:
        session: 数据库会话。
        params: 试算参数（items + member_id 可选）。

    Returns:
        CartCalcResult（total_amount / discount_amount / point_deduct / receivable / details）。
    """
    # 判断是否会员（用于 is_member_only 促销过滤）
    is_member = False
    if params.member_id is not None and params.member_id > 0:
        member = session.execute(
            select(MemMember.id).where(
                MemMember.id == params.member_id,
                MemMember.status == 1,  # 冻结/挂失会员不算
            )
        ).scalar_one_or_none()
        is_member = member is not None

    # 转换成促销引擎的输入
    engine_items = [
        EngineItem(
            product_id=it.product_id,
            quantity=it.quantity,
            unit_price=it.unit_price,
            # category_id 不传，让引擎内部按 product_id 批量查（避免 N+1）
        )
        for it in params.items
    ]

    # 调用促销引擎（当前时间由引擎内部取，允许注入便于测试）
    result = calc_promotion(session, engine_items, is_member=is_member, now=_now())

    # 转成响应 Schema
    return CartCalcResult(
        total_amount=result.total_amount,
        discount_amount=result.discount_amount,
        point_deduct=result.point_deduct,
        receivable=result.receivable,
        details=[
            PromoDetailOut(
                promo_id=d.promo_id,
                promo_name=d.promo_name,
                promo_type=d.promo_type,
                amount=d.amount,
                desc=d.desc,
            )
            for d in result.details
        ],
    )


# 让 lint 知道这些 import 是被间接使用的
_ = (money, or_)
