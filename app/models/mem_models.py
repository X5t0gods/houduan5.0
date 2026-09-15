"""会员域（mem_）—— 6 张表。

对应 db/01_schema.sql 第六节前半。

⚠️ 事务 T4（充值）：mem_member.balance/gift_balance/points + mem_balance_flow +
   mem_point_flow（若有赠积分）+ 单号 CZ 生成，同一事务。

⚠️ 关键规则（阶段 7）：
- 消费**先扣赠送余额再扣本金**（业务侧规则，模型不体现）
- 退款**只退本金**（防"充 100 送 20 → 退款套 120"）
- 积分抵扣上限为应收的 50%（可配 sys_config）
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


class MemLevel(Base):
    """会员等级（mem_level）。

    ⚠️ `discount_rate` DECIMAL(5,4)：0.0000-1.0000 表示 0-100 折，
       例如 0.9800 = 98 折。**不是金额**，阶段 1 起用 Rate 别名序列化。
    """

    __tablename__ = "mem_level"
    __table_args__ = (
        UniqueConstraint("level_name", name="uk_level_name"),
        {"comment": "会员等级"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="等级ID")
    level_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="等级名称，如 普通/银卡/金卡")
    level_value: Mapped[int] = mapped_column(Integer, nullable=False, comment="等级值，越大越高")
    # DECIMAL(5,4)：折扣率精度 4 位（0.9800 = 98 折）
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("1.0000"), comment="折扣率，如 0.9800 表示98折")
    upgrade_condition: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'CONSUME'"), comment="升级依据 CONSUME累计消费/POINTS累计积分")
    condition_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="升级门槛值")
    benefit_desc: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="权益说明")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class MemMember(Base):
    """会员档案（mem_member）。

    ⚠️ `member_no` 由单号生成器 M 键产出（M + yyyyMMdd + 4 位）。
    ⚠️ `phone` 唯一：错误码 9001（该手机号已是会员）。
    ⚠️ 收银台 F4 手机号后 4 位模糊匹配用 idx_phone_suffix 索引。
    """

    __tablename__ = "mem_member"
    __table_args__ = (
        UniqueConstraint("member_no", name="uk_member_no"),
        UniqueConstraint("phone", name="uk_phone"),
        {"comment": "会员档案"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="会员ID")
    member_no: Mapped[str] = mapped_column(String(24), nullable=False, comment="会员卡号 M+日期+流水")
    phone: Mapped[str] = mapped_column(String(20), nullable=False, comment="手机号")
    real_name: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="姓名")
    gender: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="性别 0未知 1男 2女")
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True, comment="生日")
    level_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mem_level.id", ondelete="RESTRICT"), nullable=False, comment="会员等级ID")
    # 双轨余额：本金 + 赠送金，消费先扣赠送金再扣本金；退款只退本金
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="储值本金余额")
    gift_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="储值赠送余额")
    points: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="可用积分")
    total_consume: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="累计消费金额")
    consume_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="累计消费笔数")
    last_consume_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近消费时间")
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'STORE'"), comment="会员来源 STORE到店/REFER推荐/ONLINE线上")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1正常 0冻结 2挂失")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class MemPointFlow(Base):
    """会员积分流水（mem_point_flow）—— 只增不改。

    勾稽恒等式：`mem_member.points = Σ(flow.points × flow.direction)`。
    """

    __tablename__ = "mem_point_flow"
    __table_args__ = {"comment": "会员积分流水（只增不改）"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="流水ID")
    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mem_member.id", ondelete="RESTRICT"), nullable=False, comment="会员ID")
    flow_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="类型 EARN消费获取/RETURN消费退回/DEDUCT积分抵扣/EXPIRE过期扣减/ADJUST手工调整")
    points: Mapped[int] = mapped_column(Integer, nullable=False, comment="变动积分（正数）")
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="方向 1增加 -1减少")
    before_points: Mapped[int] = mapped_column(Integer, nullable=False, comment="变动前积分")
    after_points: Mapped[int] = mapped_column(Integer, nullable=False, comment="变动后积分")
    source_no: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源单据号")
    operator: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作人ID")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="发生时间")


class MemBalanceFlow(Base):
    """会员储值流水（mem_balance_flow）—— 只增不改。

    ⚠️ **阶段 1 新增 `request_id` 列 + `uk_request_id` 唯一索引**：
       为 `POST /members/{id}/recharge` 提供幂等落库位置（前端自动注入 request_id）。
       字段可空的原因：只有充值/消费等资金入口会带幂等键，调整类流水没有；
       MySQL 的 UNIQUE 允许多个 NULL，因此"可空 + 唯一索引"正好满足
       "有值必唯一、无值不冲突"。

    勾稽恒等式：
    - `mem_member.balance = Σ(flow.amount × flow.direction)`
    - `mem_member.gift_balance = Σ(flow.gift_amount × flow.direction)`
    """

    __tablename__ = "mem_balance_flow"
    __table_args__ = (
        UniqueConstraint("request_id", name="uk_request_id"),
        {"comment": "会员储值流水（只增不改）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="流水ID")
    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mem_member.id", ondelete="RESTRICT"), nullable=False, comment="会员ID")
    flow_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="类型 RECHARGE充值/GIFT赠送/CONSUME消费/REFUND退款/ADJUST调整")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="本金变动金额")
    gift_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="赠送金变动金额")
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="方向 1增加 -1减少")
    before_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="变动前本金余额")
    after_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="变动后本金余额")
    before_gift: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="变动前赠送余额")
    after_gift: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="变动后赠送余额")
    pay_method: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="支付方式（充值时）")
    source_no: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源单据号")
    # ⭐ 阶段 1 新增：充值幂等键（前端生成 UUID，与 sal_trade.request_id 语义一致）
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="幂等键（前端生成，防重复充值）")
    operator: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作人ID")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="发生时间")


class MemTag(Base):
    """会员标签（mem_tag）—— 高价值/沉睡/生鲜偏好等。"""

    __tablename__ = "mem_tag"
    __table_args__ = (
        UniqueConstraint("tag_name", name="uk_tag_name"),
        {"comment": "会员标签"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="标签ID")
    tag_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="标签名称，如 高价值/沉睡/生鲜偏好")
    tag_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'AUTO'"), comment="类型 AUTO自动/MANUAL手工")
    tag_color: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="标签颜色")
    rule_desc: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="自动标签规则说明")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class MemMemberTag(Base):
    """会员-标签关联（mem_member_tag）。"""

    __tablename__ = "mem_member_tag"
    __table_args__ = (
        UniqueConstraint("member_id", "tag_id", name="uk_member_tag"),
        {"comment": "会员-标签关联"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mem_member.id", ondelete="RESTRICT"), nullable=False, comment="会员ID")
    tag_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mem_tag.id", ondelete="RESTRICT"), nullable=False, comment="标签ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
