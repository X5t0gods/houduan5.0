"""智能分析域（bi_）—— 4 张表。

对应 db/01_schema.sql 第八节。

⚠️ 决策 11（异步任务）：POST /bi/analysis/run 立即返回 task_id，
   真正的挖掘丢到 ThreadPoolExecutor/ProcessPoolExecutor，
   ⛔ 绝不能写在 async def 里直接跑（CPU 密集会卡死事件循环）。

⚠️ 决策 10（无 Redis 缓存）：bi_recommend_cache 服务启动时全量加载进内存，
   挖掘任务完成后重载。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
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


class BiAnalysisTask(Base):
    """智能分析任务（bi_analysis_task）。

    ⚠️ `cost_ms` 是论文实验章节的核心指标（Apriori vs FP-Growth 耗时对比），
       用 `time.perf_counter()` 精确计时后落库。
    ⚠️ 失败时把 `error_msg` 落库（对应错误码 BI_10001 数据不足 / BI_10002 参数非法）。
    """

    __tablename__ = "bi_analysis_task"
    __table_args__ = {"comment": "智能分析任务"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="任务ID")
    task_type: Mapped[str] = mapped_column(String(24), nullable=False, comment="任务类型 ASSOCIATION关联规则/FORECAST销量预测/REPLENISH补货建议")
    algorithm: Mapped[str | None] = mapped_column(String(24), nullable=True, comment="算法 FP_GROWTH/APRIORI")
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="挖掘参数（最小支持度/置信度/提升度/时间窗口等）")
    data_size: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="参与分析的交易笔数")
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="参与分析的商品数")
    rule_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="产出规则数")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'PENDING'"), comment="状态 PENDING排队/RUNNING执行中/SUCCESS成功/FAILED失败")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开始时间")
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="结束时间")
    cost_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时（毫秒）")
    result_summary: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="结果摘要")
    error_msg: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="错误信息")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="触发人ID（为空表示定时任务）")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class BiAssociationRule(Base):
    """商品关联规则（bi_association_rule）—— 论文核心产出。

    ⚠️ 精度分档（不是金额，全部用 Rate 别名序列化）：
    - `support` / `confidence` DECIMAL(8,5)：0-1 之间的比率，5 位精度
    - `lift` / `conviction` DECIMAL(10,4)：提升度可 >1，4 位精度
    - `leverage` DECIMAL(10,5)：杠杆率可正可负，5 位精度

    ⚠️ `antecedent` / `consequent` 是 JSON 数组，存商品 ID（如 [12, 45]）；
       另冗余 `antecedent_name` / `consequent_name` 便于展示，避免每次 JOIN。
    """

    __tablename__ = "bi_association_rule"
    __table_args__ = {"comment": "商品关联规则"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="规则ID")
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bi_analysis_task.id", ondelete="RESTRICT"), nullable=False, comment="所属分析任务ID")
    antecedent: Mapped[list[int]] = mapped_column(JSON, nullable=False, comment="前件商品ID集合，如 [12,45]")
    consequent: Mapped[list[int]] = mapped_column(JSON, nullable=False, comment="后件商品ID集合，如 [78]")
    antecedent_name: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="前件商品名称（冗余，便于展示）")
    consequent_name: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="后件商品名称（冗余，便于展示）")
    support: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False, comment="支持度")
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False, comment="置信度")
    lift: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, comment="提升度")
    leverage: Mapped[Decimal | None] = mapped_column(Numeric(10, 5), nullable=True, comment="杠杆率")
    conviction: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True, comment="确信度")
    suggestion: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="建议动作 捆绑/陈列/套餐/推荐")
    interpretation: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="自动生成的业务解读文本")
    adopted: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否已被采纳 1是 0否")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class BiRecommendCache(Base):
    """收银台关联推荐缓存（bi_recommend_cache）。

    ⚠️ 决策 10：无 Redis 部署下的替代方案 —— 服务启动时全量加载进内存，
       挖掘任务完成后重载；收银台请求 ≤ 100ms 命中内存。
    ⚠️ `score = lift × confidence`（阶段 9 计算），降序排列取前 N 条推荐。
    """

    __tablename__ = "bi_recommend_cache"
    __table_args__ = (
        UniqueConstraint("product_id", "recommend_product_id", name="uk_product_recommend"),
        {"comment": "收银台关联推荐缓存"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="触发商品ID")
    recommend_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("prd_product.id", ondelete="RESTRICT"), nullable=False, comment="推荐商品ID")
    # DECIMAL(10,4)：推荐得分 = 提升度 × 置信度，4 位精度
    score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, server_default=text("0.0000"), comment="推荐得分（提升度×置信度）")
    rule_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bi_association_rule.id", ondelete="RESTRICT"), nullable=True, comment="来源规则ID")
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="推荐理由，如 常与啤酒一起购买")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class BiDailyStat(Base):
    """日统计聚合表（bi_daily_stat）—— 看板与报表数据源。

    ⚠️ 决策 10：APScheduler 每夜重算（阶段 8 交付），前端看板直接查此表 ≤ 3s。
    ⚠️ 金额精度 DECIMAL(14,2)：单日汇总可能超 12 位，扩到 14 位防溢出；
       件数 DECIMAL(14,3) 支持称重商品小数汇总。
    """

    __tablename__ = "bi_daily_stat"
    __table_args__ = (
        UniqueConstraint("stat_date", "store_id", name="uk_date_store"),
        {"comment": "日统计聚合表（看板与报表数据源）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    stat_date: Mapped[date] = mapped_column(Date, nullable=False, comment="统计日期")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=False, comment="门店ID")
    sale_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0.00"), comment="销售额")
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0.00"), comment="退货金额")
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0.00"), comment="净销售额")
    sale_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="销售笔数")
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="客单价")
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0.00"), comment="销售成本")
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0.00"), comment="毛利额")
    # DECIMAL(8,4)：毛利率 4 位精度（0.0000-1.0000）
    gross_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, server_default=text("0.0000"), comment="毛利率")
    member_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0.00"), comment="会员消费金额")
    new_member: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="新增会员数")
    sale_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, server_default=text("0.000"), comment="销售件数")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
