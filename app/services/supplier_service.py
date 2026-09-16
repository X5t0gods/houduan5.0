"""供应商业务编排（阶段 6 CP2 交付物）—— CRUD + 对账 + 付款。

⚠️ 对账用"倒推法"（spec 4.4）：初始数据里 3 家供应商有 balance_payable 但无历史单据，
   用"统计单据倒推期初"会得 0，与当前余额对不上。因此：
   ```
   end_payable   = pur_supplier.balance_payable        # 权威当前值
   increase      = Σ 区间内收货金额
   decrease      = Σ 区间内退货金额 + Σ 区间内付款金额
   begin_payable = end_payable − increase + decrease   # 倒推
   ```
   恒等式 begin + increase − decrease = end 天然成立。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.repositories import supplier_repository as repo
from app.repositories.auth_repository import write_operation_log
from app.schemas.purchase import (
    PaymentOut,
    PaymentParams,
    StatementResult,
    Supplier,
    SupplierCreate,
    SupplierUpdate,
)
from app.utils.money import ZERO, money

logger = get_logger(__name__)

# 合法结算方式 / 付款方式
VALID_SETTLE_TYPES = {"CASH", "PERIOD"}
VALID_PAY_METHODS = {"CASH", "TRANSFER", "WECHAT", "ALIPAY"}


def _to_supplier(s) -> Supplier:
    return Supplier(
        id=s.id,
        supplier_code=s.supplier_code,
        supplier_name=s.supplier_name,
        contact_name=s.contact_name,
        phone=s.phone,
        address=s.address,
        settle_type=s.settle_type,
        credit_days=s.credit_days,
        balance_payable=s.balance_payable,
        status=s.status,
        remark=s.remark,
    )


# ---------- 5.10 GET /suppliers ----------


def list_suppliers(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    status: int | None = None,
) -> tuple[list[Supplier], int]:
    """供应商列表。"""
    rows, total = repo.list_suppliers(
        session, page=page, page_size=page_size, keyword=keyword, status=status,
    )
    return [_to_supplier(s) for s in rows], total


# ---------- 5.11 POST /suppliers ----------


def create_supplier(
    session: Session, params: SupplierCreate, *, operator_id: int
) -> Supplier:
    """新增供应商（spec 5.11）。

    ⚠️ supplier_code 后端自动生成（S 取号）；传了则校验唯一（重复 2002）。
    ⚠️ balance_payable 初始为 0（⛔不接受前端传入，防篡改 —— schema 里就没这字段）。
    """
    if params.settle_type not in VALID_SETTLE_TYPES:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"非法结算方式：{params.settle_type}")
    # PERIOD 时 credit_days 应 >= 0（schema 已保证 ge=0）

    # 编码：不传则自动生成
    supplier_code = params.supplier_code
    if supplier_code:
        if repo.get_supplier_by_code(session, supplier_code) is not None:
            raise BusinessError(ErrorCode.PRODUCT_DUPLICATE, f"供应商编码 {supplier_code} 已存在")
    else:
        seq = NoSeqService(session)
        supplier_code = seq.next_no("S")

    supplier = repo.insert_supplier(
        session,
        supplier_code=supplier_code,
        supplier_name=params.supplier_name,
        contact_name=params.contact_name,
        phone=params.phone,
        address=params.address,
        settle_type=params.settle_type,
        credit_days=params.credit_days,
        balance_payable=ZERO,  # ⛔ 强制 0，不接受前端传入
        status=1,
        remark=params.remark,
    )

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="新增供应商",
        target_type="pur_supplier", target_id=supplier.id,
        after_value={"supplier_code": supplier_code, "supplier_name": params.supplier_name},
        result=1,
    )
    session.commit()
    logger.info(f"新增供应商：code={supplier_code}, name={params.supplier_name}")
    return _to_supplier(supplier)


# ---------- 5.12 PUT /suppliers/{id} ----------


def update_supplier(
    session: Session, supplier_id: int, params: SupplierUpdate, *, operator_id: int
) -> Supplier:
    """修改供应商（spec 5.12）。

    ⚠️ 不允许改 supplier_code 与 balance_payable（schema 里就没这两个字段）。
    ⚠️ 支持停用（status=0），不提供 DELETE（文档 3.4.10：只停用不删除）。
    """
    supplier = repo.get_supplier_by_id(session, supplier_id)
    if supplier is None:
        raise BusinessError(ErrorCode.SUPPLIER_NOT_FOUND, f"供应商 id={supplier_id} 不存在")

    if params.settle_type is not None and params.settle_type not in VALID_SETTLE_TYPES:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"非法结算方式：{params.settle_type}")

    # 只组装允许修改的字段（⛔ 不含 supplier_code / balance_payable）
    values: dict = {}
    for field_name, new_val in [
        ("supplier_name", params.supplier_name),
        ("contact_name", params.contact_name),
        ("phone", params.phone),
        ("address", params.address),
        ("settle_type", params.settle_type),
        ("credit_days", params.credit_days),
        ("status", params.status),
        ("remark", params.remark),
    ]:
        if new_val is not None:
            values[field_name] = new_val

    repo.update_supplier(session, supplier_id, values)
    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="修改供应商",
        target_type="pur_supplier", target_id=supplier_id,
        after_value={"changed_fields": list(values.keys())},
        result=1,
    )
    session.commit()
    updated = repo.get_supplier_by_id(session, supplier_id)
    return _to_supplier(updated)


# ---------- 5.13 GET /suppliers/{id}/statement ----------


def get_statement(
    session: Session, supplier_id: int, start_date: date, end_date: date
) -> StatementResult:
    """供应商对账（spec 4.4 倒推法）。

    ⚠️ begin_payable 是倒推值（end - increase + decrease），非历史累计。
       原因：初始化数据的应付余额没有对应历史单据。
    """
    supplier = repo.get_supplier_by_id(session, supplier_id)
    if supplier is None:
        raise BusinessError(ErrorCode.SUPPLIER_NOT_FOUND, f"供应商 id={supplier_id} 不存在")

    increase = money(repo.sum_receipts_in_range(session, supplier_id, start_date, end_date))
    returns = repo.sum_returns_in_range(session, supplier_id, start_date, end_date)
    payments = repo.sum_payments_in_range(session, supplier_id, start_date, end_date)
    decrease = money(returns + payments)

    end_payable = money(supplier.balance_payable)
    begin_payable = money(end_payable - increase + decrease)

    return StatementResult(
        begin_payable=begin_payable,
        increase=increase,
        decrease=decrease,
        end_payable=end_payable,
    )


# ---------- 5.14 POST /suppliers/payments ----------


def create_payment(
    session: Session, params: PaymentParams, *, operator_id: int
) -> PaymentOut:
    """付款登记（spec 5.14）。

    ⚠️ 冲减应付：balance_payable -= amount（允许为负，同退货）。
    """
    supplier = repo.get_supplier_by_id(session, params.supplier_id)
    if supplier is None:
        raise BusinessError(ErrorCode.SUPPLIER_NOT_FOUND, f"供应商 id={params.supplier_id} 不存在")
    if params.amount <= 0:
        raise BusinessError(ErrorCode.AMOUNT_INVALID, "付款金额必须大于 0")
    if params.pay_method not in VALID_PAY_METHODS:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"非法付款方式：{params.pay_method}")

    seq = NoSeqService(session)
    pay_no = seq.next_no("FK")
    amount = money(params.amount)

    payment = repo.insert_payment(
        session,
        pay_no=pay_no,
        supplier_id=params.supplier_id,
        amount=amount,
        pay_method=params.pay_method,
        pay_date=params.pay_date,
        operator=operator_id,
        remark=params.remark,
    )
    # 冲减应付（负 delta）
    repo.adjust_balance_payable(session, params.supplier_id, -amount)

    write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="采购", action="供应商付款",
        target_type="pur_payment", target_id=payment.id,
        after_value={"pay_no": pay_no, "amount": str(amount)},
        result=1,
    )
    session.commit()
    logger.info(f"供应商付款：pay_no={pay_no}, amount={amount}")

    updated = repo.get_supplier_by_id(session, params.supplier_id)
    return PaymentOut(
        id=payment.id,
        pay_no=pay_no,
        supplier_id=params.supplier_id,
        amount=amount,
        pay_method=params.pay_method,
        pay_date=params.pay_date,
        balance_payable=updated.balance_payable,
    )
