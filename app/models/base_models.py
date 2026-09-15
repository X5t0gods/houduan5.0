"""基础数据域（base_）—— 4 张表。

对应 db/01_schema.sql 第一节。

⚠️ 特别说明：
- `base_store.manager_id` **不设外键**（脚本注释明说：避免与 sys_user 循环依赖）
- `base_unit.base_unit_id` 是自引用外键（辅助单位 → 基本单位）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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


class BaseStore(Base):
    """门店信息（base_store）。

    多门店数据隔离的根节点，几乎所有业务表都通过 store_id 引用此表。
    """

    __tablename__ = "base_store"
    __table_args__ = (
        UniqueConstraint("store_code", name="uk_store_code"),
        {"comment": "门店信息"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="门店ID")
    store_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="门店编码")
    store_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="门店名称")
    address: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="门店地址")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="联系电话")
    business_hours: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="营业时间，如 07:00-22:00")
    # ⛔ manager_id 不声明 ForeignKey —— 脚本注释明说"避免与 sys_user 循环依赖"
    # 若在此处加 FK，会与 sys_user.store_id → base_store.id 形成循环，导致建表失败
    manager_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="店长用户ID（不设外键，避免与 sys_user 循环依赖）")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1营业 0停业")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间（DB 侧 ON UPDATE 自动刷新）")


class BasePos(Base):
    """收银台（base_pos）。"""

    __tablename__ = "base_pos"
    __table_args__ = (
        UniqueConstraint("store_id", "pos_code", name="uk_store_pos"),
        {"comment": "收银台"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="收银台ID")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="所属门店ID")
    pos_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="收银台编码")
    pos_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="收银台名称")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class BaseUnit(Base):
    """计量单位（base_unit）。

    支持"基本单位 + 辅助单位"两级：如"瓶"是基本单位，"箱 = 12 瓶"是辅助单位。
    """

    __tablename__ = "base_unit"
    __table_args__ = (
        UniqueConstraint("unit_name", name="uk_unit_name"),
        {"comment": "计量单位"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="单位ID")
    unit_name: Mapped[str] = mapped_column(String(20), nullable=False, comment="单位名称，如 瓶/箱/斤")
    unit_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="单位类型 1基本单位 2辅助单位")
    # 自引用外键：辅助单位 → 基本单位
    base_unit_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("base_unit.id", ondelete="RESTRICT"), nullable=True, comment="所属基本单位ID")
    # DECIMAL(12,4)：换算系数精度到 4 位（如 1箱=12.0000瓶）
    conversion_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("1.0000"), comment="换算系数，1辅助单位=系数×基本单位")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class BaseShelf(Base):
    """货架/库位（base_shelf）。"""

    __tablename__ = "base_shelf"
    __table_args__ = (
        UniqueConstraint("store_id", "shelf_code", name="uk_store_shelf"),
        {"comment": "货架/库位"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="货架ID")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="所属门店ID")
    shelf_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="货架编码")
    shelf_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="货架名称")
    area: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="所属区域，如 生鲜区/日化区")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
