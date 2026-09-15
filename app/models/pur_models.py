"""供应商与采购域（pur_）—— 9 张表。

对应 db/01_schema.sql 第四节。

⚠️ 事务 T2（收货）：pur_receipt + pur_receipt_item + inv_stock + inv_stock_flow +
   inv_batch + prd_product.avg_cost 重算，必须在同一事务内完成。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurSupplier(Base):
    """供应商档案（pur_supplier）。"""

    __tablename__ = "pur_supplier"
    __table_args__ = (
        UniqueConstraint("supplier_code", name="uk_supplier_code"),
        {"comment": "供应商档案"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="供应商ID")
    supplier_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="供应商编码，S+6位流水")
    supplier_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="供应商名称")
    contact_name: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="联系人")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="联系电话")
    address: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="地址")
    settle_type: Mapped[str] = mapped_column(String(16), nullable=True, server_default=text("'CASH'"), comment="结算方式 CASH现结/PERIOD账期")
    credit_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="账期天数")
    # 应付余额：随收货增加、随付款减少，与 pur_payment 累计对账
    balance_payable: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="应付余额")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1合作中 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PurSupplierProduct(Base):
    """供应商-商品供货关系（pur_supplier_product）。"""

    __tablename__ = "pur_supplier_product"
    __table_args__ = (
        UniqueConstraint("supplier_id", "product_id", name="uk_supplier_product"),
        {"comment": "供应商-商品供货关系"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_supplier.id", ondelete="RESTRICT"), nullable=False, comment="供应商ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    supply_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="供货价")
    is_default: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否默认供应商 1是 0否")
    last_purchase_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近采购时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PurOrder(Base):
    """采购订单（pur_order）。

    状态机：DRAFT → AUDITED → PARTIAL → RECEIVED → FINISHED（或作废 VOID）。
    ⚠️ 状态非法时接口返回 4001，超收时返回 4002。
    """

    __tablename__ = "pur_order"
    __table_args__ = (
        UniqueConstraint("order_no", name="uk_order_no"),
        {"comment": "采购订单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="采购订单ID")
    order_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="采购单号 CG+日期+流水")
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_supplier.id", ondelete="RESTRICT"), nullable=False, comment="供应商ID")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="收货门店ID")
    order_date: Mapped[date] = mapped_column(Date, nullable=False, comment="下单日期")
    expect_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="预计到货日期")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="订单金额合计")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'MANUAL'"), comment="来源 MANUAL手工/REPLENISH补货建议")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'DRAFT'"), comment="状态 DRAFT待审核/AUDITED已审核/PARTIAL部分收货/RECEIVED已收货/FINISHED已完成/VOID已作废")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="RESTRICT"), nullable=False, comment="制单人ID")
    audited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="审核人ID")
    audited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审核时间")
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="驳回原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PurOrderItem(Base):
    """采购订单明细（pur_order_item）。"""

    __tablename__ = "pur_order_item"
    __table_args__ = {"comment": "采购订单明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_order.id", ondelete="RESTRICT"), nullable=False, comment="采购订单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="采购数量")
    received_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="已收货数量")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="采购单价")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="行金额 = 数量 × 单价")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PurReceipt(Base):
    """采购收货单（pur_receipt）—— 事务 T2 的主表。

    ⚠️ `is_direct=1` 表示无单直入（跳过 pur_order），此时 order_id 为 NULL。
    """

    __tablename__ = "pur_receipt"
    __table_args__ = (
        UniqueConstraint("receipt_no", name="uk_receipt_no"),
        {"comment": "采购收货单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="收货单ID")
    receipt_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="收货单号 SH+日期+流水")
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pur_order.id", ondelete="RESTRICT"), nullable=True, comment="来源采购订单ID（无单入库时为空）")
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_supplier.id", ondelete="RESTRICT"), nullable=False, comment="供应商ID")
    store_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="收货门店ID")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="收货金额合计")
    is_direct: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否无单直入 1是 0否")
    diff_remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="收货差异说明")
    received_by: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="收货人ID")
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="收货时间")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'FINISHED'"), comment="状态 FINISHED已完成/VOID已作废")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PurReceiptItem(Base):
    """采购收货单明细（pur_receipt_item）。

    ⚠️ 携带批次信息（batch_no / production_date / expiry_date），
       事务 T2 会据此创建 inv_batch 记录。
    """

    __tablename__ = "pur_receipt_item"
    __table_args__ = {"comment": "采购收货单明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    receipt_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_receipt.id", ondelete="RESTRICT"), nullable=False, comment="收货单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    order_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="订单数量")
    receive_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="实收数量")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="收货单价")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="行金额")
    batch_no: Mapped[str | None] = mapped_column(String(40), nullable=True, comment="批次号")
    production_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="生产日期")
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="到期日期")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class PurReturn(Base):
    """采购退货单（pur_return）。

    ⚠️ source_receipt_id 无外键（脚本注释未列出，脚本末尾也无对应 ALTER），
       保持不声明 FK 与脚本一致。
    """

    __tablename__ = "pur_return"
    __table_args__ = (
        UniqueConstraint("return_no", name="uk_return_no"),
        {"comment": "采购退货单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="采购退货单ID")
    return_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="退货单号 CT+日期+流水")
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_supplier.id", ondelete="RESTRICT"), nullable=False, comment="供应商ID")
    store_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="门店ID")
    source_receipt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="来源收货单ID")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="退货金额")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退货原因")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'FINISHED'"), comment="状态")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="制单人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PurReturnItem(Base):
    """采购退货明细（pur_return_item）。"""

    __tablename__ = "pur_return_item"
    __table_args__ = {"comment": "采购退货明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    return_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_return.id", ondelete="RESTRICT"), nullable=False, comment="采购退货单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="退货数量")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="退货单价")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="行金额")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class PurPayment(Base):
    """供应商付款登记（pur_payment）。

    ⚠️ 单号 FK+日期+流水。docs/03 8.2 节漏了此规则，仅存在于建表脚本字段注释里，
       以脚本为准（阶段 1 sequence.py 已按此实现）。
    """

    __tablename__ = "pur_payment"
    __table_args__ = (
        UniqueConstraint("pay_no", name="uk_pay_no"),
        {"comment": "供应商付款登记"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="付款记录ID")
    pay_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="付款单号 FK+日期+流水")
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pur_supplier.id", ondelete="RESTRICT"), nullable=False, comment="供应商ID")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="付款金额")
    pay_method: Mapped[str] = mapped_column(String(16), nullable=False, comment="付款方式 CASH/TRANSFER/WECHAT/ALIPAY")
    pay_date: Mapped[date] = mapped_column(Date, nullable=False, comment="付款日期")
    operator: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="经办人ID")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
