"""销售域（sal_）—— 7 张表。

对应 db/01_schema.sql 第七节。

⚠️ 事务 T1（收银）：sal_trade + sal_trade_item + sal_trade_payment +
   inv_stock 扣减 + inv_stock_flow 写入 + mem_member 余额/积分 +
   mem_balance_flow / mem_point_flow + pro_promotion 触发统计 +
   pro_coupon_record 核销，**同一事务内完成**。

⚠️ 幂等：sal_trade.request_id 唯一索引（前端自动注入 UUID），
   重复到达时直接回放首次结果，不重复执行。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SalTrade(Base):
    """销售主单（sal_trade）—— 事务 T1 的根表。

    ⚠️ `request_id` 幂等键：前端 UUID，唯一索引 uk_request_id 兜住并发重复提交，
       捕获 IntegrityError 后重查即可返回首次结果。

    ⚠️ `cost_amount` / `gross_profit` 在结算时基于 sal_trade_item.cost_price 快照计算，
       **不用 prd_product.avg_cost 当前值**，否则历史毛利会被后续进价污染。

    ⚠️ `print_count` 小票打印次数（重打累加），对应接口字段 print_count。
    """

    __tablename__ = "sal_trade"
    __table_args__ = (
        UniqueConstraint("trade_no", name="uk_trade_no"),
        UniqueConstraint("request_id", name="uk_request_id"),
        {"comment": "销售主单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="销售单ID")
    trade_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="销售单号 XS+日期+流水")
    # 幂等键：前端生成 UUID，防重复扣款
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="幂等键（前端生成UUID，防重复扣款）")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    pos_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_pos.id", ondelete="RESTRICT"), nullable=False, comment="收银台ID")
    session_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sal_session.id", ondelete="RESTRICT"), nullable=True, comment="所属收银班次ID")
    cashier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="RESTRICT"), nullable=False, comment="收银员ID")
    member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mem_member.id", ondelete="RESTRICT"), nullable=True, comment="会员ID（非会员为空）")
    total_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0.000"), comment="商品总数量")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="商品金额合计")
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="优惠合计")
    point_deduct: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="积分抵扣金额")
    receivable: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="应收金额")
    received: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="实收金额")
    change_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="找零金额")
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="销售成本合计")
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="毛利额")
    round_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="抹零金额")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'FINISHED'"), comment="状态 FINISHED已完成/RETURNED已退货/VOID已作废")
    trade_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="交易时间")
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="小票打印次数（重打累加，对应接口 print_count）")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SalTradeItem(Base):
    """销售明细（sal_trade_item）。

    ⚠️ 快照字段（保证历史单据不被后续变更污染）：
       `product_name` / `spec` / `unit_name` / `cost_price` 全部在结算时锁定。
       报表毛利必须用 cost_price，不能用 prd_product.avg_cost 当前值。
    """

    __tablename__ = "sal_trade_item"
    __table_args__ = {"comment": "销售明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    trade_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sal_trade.id", ondelete="RESTRICT"), nullable=False, comment="销售单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    product_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="商品名称快照")
    spec: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="规格快照")
    unit_name: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="单位快照")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="销售数量")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="成交单价")
    origin_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="原价（用于展示优惠）")
    # DECIMAL(12,4)：出库时点单位成本快照，锁定毛利
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="出库时点单位成本（锁定毛利）")
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="行优惠金额")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="行金额")
    batch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="出库批次ID（无 FK，脚本未声明）")
    promo_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pro_promotion.id", ondelete="RESTRICT"), nullable=True, comment="命中的促销ID")
    promo_type: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="命中的促销类型")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class SalTradePayment(Base):
    """销售支付明细（sal_trade_payment）—— 支持混合支付。

    ⚠️ 一笔 sal_trade 可对应多条 payment（现金 + 微信 + 储值 + 积分）。
       Σ amount 必须等于 sal_trade.received，否则错误码 7001（支付金额与应收金额不一致）。
    """

    __tablename__ = "sal_trade_payment"
    __table_args__ = {"comment": "销售支付明细（支持混合支付）"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    trade_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sal_trade.id", ondelete="RESTRICT"), nullable=False, comment="销售单ID")
    pay_method: Mapped[str] = mapped_column(String(16), nullable=False, comment="支付方式 CASH现金/WECHAT微信/ALIPAY支付宝/CARD银行卡/BALANCE储值/POINTS积分/OTHER其他")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="支付金额")
    pay_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="第三方支付流水号")
    pay_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'SUCCESS'"), comment="状态 SUCCESS成功/FAIL失败/REFUNDED已退款")
    pay_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="支付时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class SalReturn(Base):
    """销售退货单（sal_return）。

    ⚠️ 退货规则（阶段 4）：
    - 超出退货期限 → 错误码 8001
    - 退货数量超出可退数量 → 错误码 8002
    - 原销售单不存在 → 错误码 8003
    - **退款只退本金**（会员储值支付时），防"充100送20→退款套120"
    """

    __tablename__ = "sal_return"
    __table_args__ = (
        UniqueConstraint("return_no", name="uk_return_no"),
        {"comment": "销售退货单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="退货单ID")
    return_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="退货单号 TH+日期+流水")
    source_trade_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sal_trade.id", ondelete="RESTRICT"), nullable=False, comment="原销售单ID")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mem_member.id", ondelete="RESTRICT"), nullable=True, comment="会员ID")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="退货金额")
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="成本回冲金额")
    refund_method: Mapped[str] = mapped_column(String(16), nullable=False, comment="退款方式 CASH/WECHAT/ALIPAY/CARD/BALANCE")
    refund_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'PENDING'"), comment="退款状态 PENDING待退款/REFUNDED已退款/FAILED退款失败")
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="退款完成时间")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退货原因")
    operator: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="操作人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SalReturnItem(Base):
    """销售退货明细（sal_return_item）。

    ⚠️ `unit_price` 取原销售明细的成交价，`cost_price` 取原出库时点成本，
       都用快照，不用当前值。
    """

    __tablename__ = "sal_return_item"
    __table_args__ = {"comment": "销售退货明细"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    return_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sal_return.id", ondelete="RESTRICT"), nullable=False, comment="退货单ID")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="退货数量")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="退货单价（取原成交价）")
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="原销售成本价")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="行金额")
    batch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="回滚批次ID（无 FK）")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class SalHold(Base):
    """收银挂单（sal_hold）。

    ⚠️ 单号 `hold_no` 格式**本次补充定义**为 GD + yyyyMMdd + 4 位（与其余单据一致），
       建表脚本与 docs/03 8.2 节均未规定，待确认（阶段 1 sequence.py 已按此实现）。
    ⚠️ `cart_json` 存整份购物车快照（含商品、数量、单价、促销命中），取单时直接恢复。
    """

    __tablename__ = "sal_hold"
    __table_args__ = (
        UniqueConstraint("hold_no", name="uk_hold_no"),
        {"comment": "收银挂单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="挂单ID")
    hold_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="挂单号（本次补充定义：GD+日期+4位流水）")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    pos_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_pos.id", ondelete="RESTRICT"), nullable=False, comment="收银台ID")
    member_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="会员ID（无 FK，脚本未声明）")
    cart_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, comment="购物车快照")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="商品件数")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="挂单金额")
    operator: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="操作人ID")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'HOLDING'"), comment="状态 HOLDING挂起/RESUMED已取单/CANCELED已取消")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="挂单时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SalSession(Base):
    """收银班次（sal_session）—— 交班对账。

    状态机：OPEN → CLOSED → AUDITED
    ⚠️ 单号 JB + yyyyMMdd + 4 位。
    """

    __tablename__ = "sal_session"
    __table_args__ = (
        UniqueConstraint("session_no", name="uk_session_no"),
        {"comment": "收银班次（交班对账）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="班次ID")
    session_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="班次号 JB+日期+流水")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    pos_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_pos.id", ondelete="RESTRICT"), nullable=False, comment="收银台ID")
    cashier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="RESTRICT"), nullable=False, comment="收银员ID")
    open_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="开班时间")
    close_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="交班时间")
    init_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="备用金")
    sale_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="销售金额")
    sale_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="销售笔数")
    return_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="退货金额")
    void_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="作废金额")
    cash_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="现金收款")
    wechat_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="微信收款")
    alipay_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="支付宝收款")
    card_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="银行卡收款")
    balance_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="储值收款")
    actual_cash: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实点现金")
    cash_diff: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="现金差异")
    diff_remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="差异说明")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'OPEN'"), comment="状态 OPEN营业中/CLOSED待审核/AUDITED已审核")
    audited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="审核人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
