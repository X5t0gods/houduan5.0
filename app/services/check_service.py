"""盘点业务编排（阶段 5 CP2 交付物）—— 快照 → 录入 → 提交 → 审核。

⚠️ 头号设计点（spec 4.1）：审核后用**增量调整**，不是覆盖。
   ```
   调整量 = diff_qty = actual_qty - snapshot_qty
   UPDATE inv_stock SET quantity = quantity + 调整量 WHERE ...
   ```
   这样"盘点期间的销售"被完整保留，只有真实盘盈/盘亏反映到库存。
   ⛔ 覆盖法（quantity = actual_qty）会把盘点期间卖掉的货"变回来"，库存虚高。

⚠️ 负库存拒绝（spec 4.1）：若 quantity + 调整量 < 0 → 拒绝审核（5002），绝不落负库存。

⚠️ diff_qty 基准是 **snapshot_qty**（快照），不是 book_qty（spec 冲突表 ④）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.models.inv_models import InvStock
from app.models.prd_models import PrdProduct
from app.models.sys_models import SysUser
from app.repositories import category_repository
from app.repositories import check_repository as repo
from app.repositories import inventory_write_repository as inv_write
from app.repositories.auth_repository import write_operation_log
from app.schemas.inventory import (
    CheckAuditRequest,
    CheckCreateRequest,
    CheckItemInput,
    CheckSheet,
    CheckSheetItem,
)
from app.utils.money import ZERO, money

logger = get_logger(__name__)

# 差异原因合法值（spec 2.2，5 个中文值）
VALID_DIFF_REASONS = {"漏记", "损耗", "错盘", "串码", "其他"}


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ)).replace(tzinfo=None)


# ---------- 5.5 创建盘点单（冻结快照） ----------


def create_check(
    session: Session,
    params: CheckCreateRequest,
    *,
    store_id: int,
    user_id: int,
) -> CheckSheet:
    """创建盘点单（spec 5.5）：取号 PD → 写主表 → 批量生成明细（冻结快照）。

    ⚠️ book_qty 与 snapshot_qty 初始都 = 当前 inv_stock.quantity（前端 check.vue:46-47）。
    ⚠️ actual_qty/diff_qty/diff_rate/diff_reason 全部 NULL。
    """
    effective_store = params.store_id or store_id
    now = _now()
    seq = NoSeqService(session)
    check_no = seq.next_no("PD")

    check = repo.insert_check(
        session,
        check_no=check_no,
        store_id=effective_store,
        check_type=params.check_type,
        check_scope=params.check_scope,
        snapshot_at=now,
        total_diff_amount=ZERO,
        status="DRAFT",
        created_by=user_id,
        remark=params.remark,
    )

    # CATEGORY 需要子孙分类 ID（递归）
    descendant_ids: set[int] | None = None
    if params.check_type == "CATEGORY" and params.check_scope:
        descendant_ids = set()
        for cid in _parse_ids(params.check_scope):
            descendant_ids |= category_repository.get_descendant_ids(session, cid)

    # 取盘点范围内的库存
    stock_rows = repo.get_stock_for_check(
        session,
        store_id=effective_store,
        check_type=params.check_type,
        check_scope=params.check_scope,
        descendant_ids=descendant_ids,
    )

    # 批量生成明细（快照冻结）
    items = []
    for stock, _product_code, _product_name, _spec in stock_rows:
        items.append({
            "check_id": check.id,
            "product_id": stock.product_id,
            "book_qty": stock.quantity,
            "snapshot_qty": stock.quantity,  # ⚠️ 冻结快照
            "cost_price": stock.avg_cost,
            "profit_loss": ZERO,
            # actual_qty / diff_qty / diff_rate / diff_reason 全部 NULL（不传）
        })
    if items:
        repo.insert_check_items_batch(session, items)

    session.commit()
    logger.info(f"创建盘点单：check_no={check_no}, 明细 {len(items)} 条")
    return get_check_detail(session, check.id)


def _parse_ids(scope: str) -> list[int]:
    return [int(p.strip()) for p in scope.split(",") if p.strip().isdigit()]


# ---------- 5.6 录入实盘数量 ----------


def update_check_items(
    session: Session, check_id: int, items: list[CheckItemInput]
) -> None:
    """录入实盘数量（spec 5.6）。

    ⚠️ 服务端重算 diff_qty/diff_rate/profit_loss（不信任前端算好的值）。
    ⚠️ 只有 DRAFT 状态可录入，否则 5002。
    """
    check = repo.get_check_by_id(session, check_id)
    if check is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "盘点单不存在")
    if check.status != "DRAFT":
        raise BusinessError(ErrorCode.DOC_ALREADY_AUDITED, f"当前状态({check.status})不可录入")

    total_diff = ZERO
    for inp in items:
        item = repo.get_check_item(session, inp.id)
        if item is None or item.check_id != check_id:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, f"盘点明细ID={inp.id}不存在")

        # diff_qty = actual_qty - snapshot_qty（⛔ 基准是快照，spec 冲突表 ④）
        diff_qty = inp.actual_qty - item.snapshot_qty
        # diff_rate = snapshot_qty > 0 ? ABS(diff_qty)/snapshot_qty : 0（⛔ 取绝对值）
        if item.snapshot_qty > 0:
            diff_rate = (abs(diff_qty) / item.snapshot_qty).quantize(Decimal("0.0001"))
        else:
            diff_rate = Decimal("0.0000")
        profit_loss = money(diff_qty * item.cost_price)

        repo.update_check_item(session, inp.id, {
            "actual_qty": inp.actual_qty,
            "diff_qty": diff_qty,
            "diff_rate": diff_rate,
            "diff_reason": inp.diff_reason,
            "profit_loss": profit_loss,
        })

    # 重算 total_diff_amount = Σ profit_loss（读全部明细）
    all_items = repo.list_check_items(session, check_id)
    total_diff = sum((it.profit_loss or ZERO) for it in all_items)
    repo.update_check(session, check_id, {"total_diff_amount": money(total_diff)})

    session.commit()


# ---------- 5.7 提交审核 ----------


def submit_check(session: Session, check_id: int) -> None:
    """提交审核（spec 5.7）。

    ⚠️ actual_qty 未填的按 snapshot_qty 补齐（与前端 fillAllWithBook 一致）。
    ⚠️ 校验 FRS-INV-006：diff_qty != 0 必须有 diff_reason，否则 5001（message 带商品）。
    ⚠️ 状态 DRAFT → SUBMITTED。
    """
    check = repo.get_check_by_id(session, check_id)
    if check is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "盘点单不存在")
    if check.status != "DRAFT":
        raise BusinessError(ErrorCode.DOC_ALREADY_AUDITED, f"当前状态({check.status})不可提交")

    items = repo.list_check_items(session, check_id)

    # 未填 actual_qty 的按 snapshot_qty 补齐
    for it in items:
        if it.actual_qty is None:
            it.actual_qty = it.snapshot_qty
            it.diff_qty = ZERO
            it.diff_rate = Decimal("0.0000")
            it.profit_loss = ZERO
            session.flush()

    # 校验：diff_qty != 0 必须有合法 diff_reason
    missing_reason_products = []
    for it in items:
        diff = it.diff_qty or ZERO
        if diff != 0 and (not it.diff_reason or it.diff_reason not in VALID_DIFF_REASONS):
            missing_reason_products.append(str(it.product_id))
    if missing_reason_products:
        raise BusinessError(
            ErrorCode.CHECK_DIFF_REASON_MISSING,
            f"以下商品有差异但未填差异原因（商品ID：{', '.join(missing_reason_products)}）",
        )

    repo.update_check(session, check_id, {"status": "SUBMITTED"})
    session.commit()
    logger.info(f"提交盘点审核：check_no={check.check_no}")


# ---------- 5.8 审核盘点（关键：增量调整） ----------


def audit_check(
    session: Session,
    check_id: int,
    params: CheckAuditRequest,
    *,
    auditor_id: int,
) -> None:
    """审核盘点（spec 5.8，本阶段最关键）。

    ⚠️ approved=false → 驳回，状态回 DRAFT（补充约定：驳回后可重新录入）。
    ⚠️ approved=true → 同一事务内：
       1. 按**增量方式**调整 inv_stock.quantity（条件更新 + 负库存检查）
       2. 写 inv_stock_flow（CHECK_ADJUST，direction 按正负）
       3. ⛔ diff_qty=0 的明细不写流水
       4. 更新 inv_check（AUDITED + audited_by + audited_at）
    """
    check = repo.get_check_by_id(session, check_id)
    if check is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "盘点单不存在")
    if check.status != "SUBMITTED":
        raise BusinessError(
            ErrorCode.DOC_ALREADY_AUDITED, f"只有待审核状态可审核（当前 {check.status}）"
        )

    now = _now()

    # 驳回：状态回 DRAFT（补充约定，交付说明写明）
    if not params.approved:
        repo.update_check(session, check_id, {
            "status": "DRAFT",
            "remark": params.remark or check.remark,
        })
        session.commit()
        logger.info(f"盘点驳回：check_no={check.check_no}")
        return

    # 通过：增量调整库存
    items = repo.list_check_items(session, check_id)

    # 先做负库存预检（避免部分调整后才发现负库存，导致回滚）
    for it in items:
        diff = it.diff_qty or ZERO
        if diff == 0:
            continue
        current_qty = repo.get_stock_qty(session, check.store_id, it.product_id)
        if current_qty is None:
            continue
        new_qty = current_qty + diff
        if new_qty < 0:
            raise BusinessError(
                ErrorCode.DOC_ALREADY_AUDITED,
                f"商品ID={it.product_id} 审核后库存将为负"
                f"（当前{current_qty}+调整{diff}），请先核对",
            )

    # 逐条调整 + 写流水
    total_diff_amount = ZERO
    for it in items:
        diff = it.diff_qty or ZERO
        total_diff_amount += (it.profit_loss or ZERO)
        if diff == 0:
            continue  # ⛔ 无变动不写流水（spec 5.8-3）

        # 增量调整：quantity = quantity + diff（条件更新防负库存）
        if diff > 0:
            # 盘盈：直接加
            before_qty = repo.get_stock_qty(session, check.store_id, it.product_id) or ZERO
            session.execute(
                sa_update(InvStock)
                .where(
                    InvStock.store_id == check.store_id,
                    InvStock.product_id == it.product_id,
                )
                .values(quantity=InvStock.quantity + diff)
            )
            after_qty = before_qty + diff
            direction = 1
        else:
            # 盘亏：条件更新扣减（WHERE quantity >= ABS(diff)）
            abs_diff = abs(diff)
            before_qty = repo.get_stock_qty(session, check.store_id, it.product_id) or ZERO
            result = inv_write.deduct_stock(
                session,
                store_id=check.store_id,
                product_id=it.product_id,
                quantity=abs_diff,
            )
            ok, before_qty, after_qty, _avg = result
            if not ok:
                raise BusinessError(
                    ErrorCode.DOC_ALREADY_AUDITED,
                    f"商品ID={it.product_id} 库存不足，无法盘亏调整",
                )
            direction = -1

        session.flush()
        inv_write.write_stock_flow(
            session,
            store_id=check.store_id,
            product_id=it.product_id,
            flow_type="CHECK_ADJUST",
            direction=direction,
            quantity=abs(diff),
            before_qty=before_qty,
            after_qty=after_qty,
            unit_cost=it.cost_price,
            amount=money(abs(diff) * it.cost_price),
            source_type="CHECK",
            source_no=check.check_no,
            operator=auditor_id,
        )

    # 更新盘点单：AUDITED + book_qty 刷成审核时实时库存（供事后审计，spec 4.1）
    for it in items:
        current_qty = repo.get_stock_qty(session, check.store_id, it.product_id)
        if current_qty is not None:
            repo.update_check_item(session, it.id, {"book_qty": current_qty})

    repo.update_check(session, check_id, {
        "status": "AUDITED",
        "audited_by": auditor_id,
        "audited_at": now,
        "total_diff_amount": money(total_diff_amount),
    })

    # 操作日志（spec 5.8：module=库存, action=审核盘点）
    write_operation_log(
        session,
        user_id=auditor_id,
        user_name=None,
        module="库存",
        action="审核盘点",
        target_type="inv_check",
        target_id=check_id,
        before_value=None,
        after_value={
            "check_no": check.check_no,
            "total_diff_amount": str(money(total_diff_amount)),
        },
        result=1,
    )

    session.commit()
    logger.info(f"盘点审核通过：check_no={check.check_no}, 差异金额={money(total_diff_amount)}")


# ---------- 5.5/5.6 查询 ----------


def list_checks(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[CheckSheet], int]:
    """盘点单列表（spec 接口 5）。"""
    rows, total = repo.list_checks(
        session, page=page, page_size=page_size, status=status, store_id=store_id,
    )
    result = [
        CheckSheet(
            id=c.id,
            check_no=c.check_no,
            store_id=c.store_id,
            check_type=c.check_type,
            check_scope=c.check_scope,
            snapshot_at=c.snapshot_at,
            total_diff_amount=c.total_diff_amount,
            status=c.status,
            created_by_name=name,
            audited_at=c.audited_at,
            remark=c.remark,
        )
        for c, name in rows
    ]
    return result, total


def get_check_detail(session: Session, check_id: int) -> CheckSheet:
    """盘点单详情（含明细，spec 接口 6）。"""
    check = repo.get_check_by_id(session, check_id)
    if check is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "盘点单不存在")

    items = repo.list_check_items(session, check_id)
    # 批量取商品信息（product_code/name/spec）
    product_ids = [it.product_id for it in items]
    products: dict[int, PrdProduct] = {}
    if product_ids:
        rows = session.execute(
            select(PrdProduct).where(PrdProduct.id.in_(product_ids))
        ).scalars().all()
        products = {p.id: p for p in rows}

    created_by_name = session.execute(
        select(SysUser.real_name).where(SysUser.id == check.created_by)
    ).scalar_one_or_none()

    items_out = []
    for it in items:
        prod = products.get(it.product_id)
        items_out.append(CheckSheetItem(
            id=it.id,
            product_id=it.product_id,
            product_code=prod.product_code if prod else None,
            product_name=prod.product_name if prod else None,
            spec=prod.spec if prod else None,
            book_qty=it.book_qty,
            snapshot_qty=it.snapshot_qty,
            actual_qty=it.actual_qty,
            diff_qty=it.diff_qty,
            diff_rate=it.diff_rate,
            diff_reason=it.diff_reason,
            cost_price=it.cost_price,
            profit_loss=it.profit_loss,
        ))

    return CheckSheet(
        id=check.id,
        check_no=check.check_no,
        store_id=check.store_id,
        check_type=check.check_type,
        check_scope=check.check_scope,
        snapshot_at=check.snapshot_at,
        total_diff_amount=check.total_diff_amount,
        status=check.status,
        created_by_name=created_by_name,
        audited_at=check.audited_at,
        remark=check.remark,
        items=items_out,
    )
