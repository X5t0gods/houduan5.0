"""库存域（inv_）—— 9 张表。

对应 db/01_schema.sql 第五节。

⚠️ 核心约束：
- `inv_stock_flow` 是库存数据的**唯一真相源**（只增不改），`inv_stock` 只是冗余快照
- 所有库存变动必须走 `stock_service` 单一入口，同时写流水与快照
- 勾稽恒等式：`inv_stock.quantity = Σ(flow.quantity × flow.direction)`
- 事务 T3（盘点）：inv_check + inv_check_item + 审核时生成 CHECK_ADJUST 流水 + inv_stock 更新
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InvStock(Base):
    """实时库存（inv_stock）—— 库存流水的冗余快照。

    ⚠️ 更新必须走条件更新防超卖：
       `UPDATE inv_stock SET quantity = quantity - :q
        WHERE store_id=? AND product_id=? AND quantity >= :q`
       rowcount=0 时抛错误码 7004（库存不足）。
    """

    __tablename__ = "inv_stock"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", name="uk_store_product"),
        {"comment": "实时库存（库存流水的冗余快照）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="当前库存数量")
    safe_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="安全库存下限")
    max_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True, comment="库存上限")
    # DECIMAL(12,4)：与 prd_product.avg_cost 精度一致，成本重算时不丢精度
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="当前移动加权平均成本")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class InvBatch(Base):
    """商品批次与保质期（inv_batch）。

    批次号规则（阶段 1 sequence.py）：P{product_code}-yyyyMMdd-NN，
    每个商品每天独立计数（seq_key = "BATCH:{product_code}"）。
    """

    __tablename__ = "inv_batch"
    __table_args__ = (
        UniqueConstraint("batch_no", name="uk_batch_no"),
        {"comment": "商品批次与保质期"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="批次ID")
    batch_no: Mapped[str] = mapped_column(String(40), nullable=False, comment="批次号")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    production_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="生产日期")
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="到期日期")
    init_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="批次初始数量")
    remain_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="批次剩余数量")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="批次单位成本")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1在库 0已耗尽 2已过期")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class InvStockFlow(Base):
    """库存流水（inv_stock_flow）—— 库存数据的唯一真相源，只增不改。

    ⚠️ 勾稽恒等式（论文与验收都要用）：
       `SELECT ... FROM inv_stock s
        WHERE s.quantity <> (SELECT COALESCE(SUM(f.quantity * f.direction), 0)
                             FROM inv_stock_flow f
                             WHERE f.store_id=s.store_id AND f.product_id=s.product_id)`
       应返回空集。

    ⚠️ 9 种 flow_type 见 app/models/enums.py::StockFlowType，一字不差。
    """

    __tablename__ = "inv_stock_flow"
    __table_args__ = {"comment": "库存流水（库存数据的唯一真相源，只增不改）"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="流水ID")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    flow_type: Mapped[str] = mapped_column(String(24), nullable=False, comment="流水类型 PURCHASE_IN/SALE_OUT/SALE_RETURN_IN/PURCHASE_RETURN_OUT/CHECK_ADJUST/LOSS_OUT/TRANSFER_IN/TRANSFER_OUT/INIT")
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="方向 1入库 -1出库")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="变动数量（正数）")
    before_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="变动前库存")
    after_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="变动后库存")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="单位成本")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="变动金额")
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, comment="来源单据类型")
    source_no: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源单据号")
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("inv_batch.id", ondelete="RESTRICT"), nullable=True, comment="批次ID")
    operator: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作人ID")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="发生时间")


class InvCheck(Base):
    """库存盘点单（inv_check）—— 事务 T3 主表。

    状态机：DRAFT → SUBMITTED → AUDITED（审核时生成 CHECK_ADJUST 流水）
    """

    __tablename__ = "inv_check"
    __table_args__ = (
        UniqueConstraint("check_no", name="uk_check_no"),
        {"comment": "库存盘点单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="盘点单ID")
    check_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="盘点单号 PD+日期+流水")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    check_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ALL'"), comment="盘点方式 ALL全盘/PART抽盘/CATEGORY按分类/SHELF按货架")
    check_scope: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="盘点范围描述")
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="账面快照时间")
    total_diff_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="盘点损益金额")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'DRAFT'"), comment="状态 DRAFT盘点中/SUBMITTED待审核/AUDITED已审核/VOID已作废")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="制单人ID")
    audited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="审核人ID")
    audited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审核时间")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class InvCheckItem(Base):
    """盘点明细（inv_check_item）。

    ⚠️ 差异必填原因（错误码 5001）：diff_qty != 0 时 diff_reason 必填。
    ⚠️ snapshot_qty 在盘点单创建时冻结，防止盘点期间库存变动影响差异计算。
    """

    __tablename__ = "inv_check_item"
    __table_args__ = {"comment": "盘点明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    check_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("inv_check.id", ondelete="RESTRICT"), nullable=False, comment="盘点单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    book_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="账面数量")
    snapshot_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="快照数量")
    actual_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True, comment="实盘数量")
    diff_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True, comment="差异数量 = 实盘 - 账面")
    # DECIMAL(8,4)：差异率精度 4 位（比率类，不是金额）
    diff_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True, comment="差异率")
    diff_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="差异原因 漏记/损耗/错盘/串码")
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="盘点时成本价")
    profit_loss: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="损益金额")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class InvLoss(Base):
    """库存报损单（inv_loss）。"""

    __tablename__ = "inv_loss"
    __table_args__ = (
        UniqueConstraint("loss_no", name="uk_loss_no"),
        {"comment": "库存报损单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="报损单ID")
    loss_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="报损单号 BS+日期+流水")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    loss_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="报损类型 BREAK破损/EXPIRE过期/FRESH生鲜损耗/OTHER其他")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="报损金额")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="报损原因")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'FINISHED'"), comment="状态")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="制单人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class InvLossItem(Base):
    """报损明细（inv_loss_item）。"""

    __tablename__ = "inv_loss_item"
    __table_args__ = {"comment": "报损明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    loss_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("inv_loss.id", ondelete="RESTRICT"), nullable=False, comment="报损单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="报损数量")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="单位成本")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="报损金额")
    batch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="批次ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class InvTransfer(Base):
    """门店调拨单（inv_transfer）—— P2 扩展需求，阶段 5 交付。

    状态机：DRAFT → OUT（调出，生成 TRANSFER_OUT 流水） → IN（调入，生成 TRANSFER_IN 流水）
    """

    __tablename__ = "inv_transfer"
    __table_args__ = (
        UniqueConstraint("transfer_no", name="uk_transfer_no"),
        {"comment": "门店调拨单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="调拨单ID")
    transfer_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="调拨单号 DB+日期+流水")
    from_store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="调出门店ID")
    to_store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="调入门店ID")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'DRAFT'"), comment="状态 DRAFT待调出/OUT已调出/IN已入库/VOID已作废")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="制单人ID")
    out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="调出时间")
    in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="调入时间")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class InvTransferItem(Base):
    """调拨明细（inv_transfer_item）。"""

    __tablename__ = "inv_transfer_item"
    __table_args__ = {"comment": "调拨明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    transfer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("inv_transfer.id", ondelete="RESTRICT"), nullable=False, comment="调拨单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="调拨数量")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="单位成本")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="调拨金额")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
