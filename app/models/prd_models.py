"""商品域（prd_）—— 7 张表。

对应 db/01_schema.sql 第三节。
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


class PrdCategory(Base):
    """商品分类（prd_category）—— 最多三级树。"""

    __tablename__ = "prd_category"
    __table_args__ = (
        UniqueConstraint("category_code", name="uk_category_code"),
        {"comment": "商品分类（最多三级）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="分类ID")
    parent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), comment="父分类ID，0为顶级")
    category_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="分类编码")
    category_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="分类名称")
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="层级 1/2/3")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PrdSpu(Base):
    """商品标准单元 SPU（prd_spu）—— 同款商品的抽象，如"可乐 330ml"是 SPU，"罐装/瓶装"是 SKU。"""

    __tablename__ = "prd_spu"
    __table_args__ = {"comment": "商品标准单元（SPU）"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="SPU ID")
    spu_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="标准商品名称")
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_category.id", ondelete="RESTRICT"), nullable=False, comment="所属分类ID")
    brand: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="品牌")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="商品描述")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PrdProduct(Base):
    """商品档案 SKU（prd_product）—— 库存与交易的最小单元。

    ⚠️ 关键约束：
    - `product_code` 由单号生成器 P 键产出（P + 6 位流水）
    - `avg_cost` 移动加权平均成本，DECIMAL(12,4) 精度防累计误差
    - 售价 < 进价时接口返回错误码 2003（前端二次确认后放行）
    - `status` 只能"停用"不能物理删除（有业务流水时）
    """

    __tablename__ = "prd_product"
    __table_args__ = (
        UniqueConstraint("product_code", name="uk_product_code"),
        {"comment": "商品档案（SKU）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="商品ID")
    product_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="商品编码，P+6位流水")
    product_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="商品名称")
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_category.id", ondelete="RESTRICT"), nullable=False, comment="所属末级分类ID")
    spu_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("prd_spu.id", ondelete="RESTRICT"), nullable=True, comment="所属SPU ID")
    brand: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="品牌")
    spec: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="规格，如 330ml")
    unit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_unit.id", ondelete="RESTRICT"), nullable=False, comment="基本单位ID")
    purchase_unit_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("base_unit.id", ondelete="RESTRICT"), nullable=True, comment="采购单位ID")
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="参考进价")
    sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="零售价")
    member_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="会员价")
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="最低售价（改价下限）")
    # DECIMAL(12,4)：移动加权平均成本，4 位精度防累计误差
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("0.0000"), comment="移动加权平均成本")
    is_weight: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否称重商品 1是 0否")
    is_perishable: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否保质期商品 1是 0否")
    shelf_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="保质期天数")
    is_allow_return: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="是否允许退货 1是 0否")
    is_allow_discount: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="是否参与促销 1是 0否")
    is_allow_point: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="是否参与积分 1是 0否")
    image_url: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="商品图片地址")
    shelf_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("base_shelf.id", ondelete="RESTRICT"), nullable=True, comment="默认陈列货架ID")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1在售 0停用（有业务流水时只可停用）")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PrdBarcode(Base):
    """商品条码（prd_barcode）—— 一商品多码，收银扫码入口。"""

    __tablename__ = "prd_barcode"
    __table_args__ = (
        UniqueConstraint("barcode", name="uk_barcode"),
        {"comment": "商品条码（一商品多码）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    barcode: Mapped[str] = mapped_column(String(32), nullable=False, comment="条码")
    barcode_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="类型 1主条码 2附加条码")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PrdPriceHistory(Base):
    """商品价格变更历史（prd_price_history）—— 只增不改。"""

    __tablename__ = "prd_price_history"
    __table_args__ = {"comment": "商品价格变更历史"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    old_purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="原进价")
    new_purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="新进价")
    old_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="原售价")
    new_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="新售价")
    change_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="变更类型 进价/售价/会员价")
    changed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="变更人ID")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="变更原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="变更时间")


class PrdTag(Base):
    """商品标签（prd_tag）—— 新品/爆款/季节性等。"""

    __tablename__ = "prd_tag"
    __table_args__ = (
        UniqueConstraint("tag_name", name="uk_tag_name"),
        {"comment": "商品标签"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="标签ID")
    tag_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="标签名称，如 新品/爆款/季节性")
    tag_color: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="标签颜色")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class PrdProductTag(Base):
    """商品-标签关联（prd_product_tag）。"""

    __tablename__ = "prd_product_tag"
    __table_args__ = (
        UniqueConstraint("product_id", "tag_id", name="uk_product_tag"),
        {"comment": "商品-标签关联"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="商品ID")
    tag_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_tag.id", ondelete="RESTRICT"), nullable=False, comment="标签ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
