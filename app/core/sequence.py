"""单号生成器 —— 16 类单据/编码的并发安全流水号（阶段 1 交付物）。

设计要点
--------
1. 使用 sys_no_seq 表 + MySQL LAST_INSERT_ID(expr) 惯用法，**一条 SQL** 完成
   "存在则自增、不存在则置 1"，⛔ 绝不使用 `SELECT MAX(...)+1`
   （docs/03 8.2 节明令禁止，并发必重号）。
2. sys_no_seq 用复合主键 (seq_key, seq_date)，⛔ 不加 AUTO_INCREMENT 列，
   避免首次插入路径上 LAST_INSERT_ID() 返回自增 ID（一个巨大值）而非表达式值。
3. 两条 SQL 必须在**同一 Session/Connection** 上连续执行 —— LAST_INSERT_ID()
   是会话级值，换连接就丢了。

事务归属
--------
默认与调用方业务写入**同一事务**。
- 理由：回滚时号段一起回滚，不产生空洞（否则审计对不上）
- 代价：长事务期间持有该键的行锁 —— 对门店级并发（10 台收银机）完全可接受

编号规则来源差异（代码内明确标注）
----------------------------------
- **FK（付款单号）**：docs/03 8.2 节漏了此规则，仅存在于 db/01_schema.sql
  pur_payment.pay_no 字段注释里 → **以脚本为准**，按 FK+yyyyMMdd+4 位实现。
- **GD（挂单号）**：docs/03 8.2 节与 db/01_schema.sql 均**未规定**格式 →
  本次**补充定义**为 GD+yyyyMMdd+4 位（与其余单据保持一致），**待确认**。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError

# 全局键（P/S）的 seq_date 固定用这个日期，与"按日重置"的键物理隔离
EPOCH_DATE: Final[date] = date(1970, 1, 1)


@dataclass(frozen=True)
class KeySpec:
    """单个 seq_key 的编号规则。

    Attributes:
        prefix: 单号前缀（如 "XS"、"CG"）；BATCH 用 "" 由 scope 拼接
        width: 流水号位数（zfill 到此宽度）
        daily: 是否按日重置（False = 全局递增，如 P/S）
        needs_scope: 是否需要 scope 参数（BATCH 键专用）
    """

    prefix: str
    width: int
    daily: bool
    needs_scope: bool = False


# 16 类编号规则 —— 与 db/01_schema.sql 字段注释、docs/03 8.2 节严格对齐
KEY_SPECS: Final[dict[str, KeySpec]] = {
    # ---------- 编码类：全局作用域，跨日不重置 ----------
    "P": KeySpec(prefix="P", width=6, daily=False),   # 商品编码 P000123
    "S": KeySpec(prefix="S", width=6, daily=False),   # 供应商编码 S000012
    # ---------- 编码类：按日作用域 ----------
    "M": KeySpec(prefix="M", width=4, daily=True),    # 会员卡号 M202609150001
    # BATCH 特殊：seq_key = "BATCH:{product_code}"，每个商品每天独立计数
    "BATCH": KeySpec(prefix="", width=2, daily=True, needs_scope=True),
    # ---------- 单据类：按日作用域，全部 prefix + yyyyMMdd + 4 位 ----------
    "CG": KeySpec(prefix="CG", width=4, daily=True),  # 采购订单
    "SH": KeySpec(prefix="SH", width=4, daily=True),  # 采购收货单
    "CT": KeySpec(prefix="CT", width=4, daily=True),  # 采购退货单
    # ⚠️ FK 在 docs/03 8.2 节漏了，仅存在于 db/01_schema.sql pur_payment.pay_no 注释里
    # → 以脚本为准（阶段 1 补充）
    "FK": KeySpec(prefix="FK", width=4, daily=True),  # 采购付款单
    "XS": KeySpec(prefix="XS", width=4, daily=True),  # 销售单
    "TH": KeySpec(prefix="TH", width=4, daily=True),  # 销售退货单
    "PD": KeySpec(prefix="PD", width=4, daily=True),  # 盘点单
    "BS": KeySpec(prefix="BS", width=4, daily=True),  # 报损单
    "DB": KeySpec(prefix="DB", width=4, daily=True),  # 调拨单
    "JB": KeySpec(prefix="JB", width=4, daily=True),  # 收银班次
    "CZ": KeySpec(prefix="CZ", width=4, daily=True),  # 会员充值单
    # ⚠️ GD（挂单号）文档与脚本都未规定格式，本次补充定义为 GD+yyyyMMdd+4 位
    # （与其余单据保持一致），**待确认**
    "GD": KeySpec(prefix="GD", width=4, daily=True),  # 挂单号
    # ⚠️ QC（期初库存单号）阶段 5 新增：db/02_init_data.sql 的 inv_stock_flow.source_no
    # 用 QC20260915001（QC + yyyyMMdd + 3 位），补商品建档期初库存时对齐此格式
    "QC": KeySpec(prefix="QC", width=3, daily=True),  # 期初库存单号
}


# 取号 SQL：一条语句完成"存在则自增、不存在则置 1"，并让会话变量持有结果
# ⛔ 坑 1：sys_no_seq 若带 AUTO_INCREMENT，则首次插入路径 LAST_INSERT_ID()
#   返回的是自增主键 ID（一个很大的数）而不是 1 —— 这就是脚本去掉自增列的原因
# ⛔ 坑 2：LAST_INSERT_ID() 是连接（会话）级的值，
#   下面两条语句必须在同一 Session 上连续执行，中间不能换连接、不能插其他查询
_UPSERT_SQL: Final = text(
    "INSERT INTO sys_no_seq (seq_key, seq_date, current_val) "
    "VALUES (:seq_key, :seq_date, LAST_INSERT_ID(1)) "
    "ON DUPLICATE KEY UPDATE current_val = LAST_INSERT_ID(current_val + 1)"
)

_SELECT_LAST_ID_SQL: Final = text("SELECT LAST_INSERT_ID()")


def _today() -> date:
    """返回当前日期（Asia/Shanghai 时区）。

    抽成函数便于测试 monkeypatch。
    """
    return datetime.now(ZoneInfo(settings.TZ)).date()


class NoSeqService:
    """单号生成器服务。

    使用示例：
        >>> seq = NoSeqService(session)  # session 来自调用方的事务
        >>> seq.next_no("XS")            # 'XS202609150001'
        >>> seq.next_no("P")             # 'P000001'（全局递增，跨日不重置）
        >>> seq.next_no("BATCH", scope="P000001")  # 'P000001-20260915-01'
        >>> seq.next_no("XS", at=date(2026, 9, 16))  # 注入日期便于测试跨日

    事务归属：见模块级 docstring。

    前端已录入编码的场景（阶段 3 商品建档）：
        P / S 键支持"外部已提供编码则跳过生成"的模式 —— 业务侧先检查是否已有编码，
        有则复用无则调用 next_no。**生成器本身不做此判断**，保持职责单一。
    """

    def __init__(self, session: Session) -> None:
        """初始化。

        Args:
            session: 调用方的 SQLAlchemy Session（应在同一事务中）。
                    ⛔ 不要传 SessionLocal() 新建的会话 —— 会脱离业务事务，
                    导致业务回滚而号段不回滚，产生空洞。
        """
        self._session = session

    def next_no(
        self,
        key: str,
        *,
        scope: str | None = None,
        at: date | None = None,
    ) -> str:
        """生成下一个单号。

        Args:
            key: 编号规则的键（见 KEY_SPECS，共 16 类）。
            scope: BATCH 键必传（商品编码，如 "P000123"），其余键必须为 None。
            at: 允许注入日期，便于测试跨日行为；不传则用当天日期（Asia/Shanghai）。

        Returns:
            完整单号字符串，如 "XS202609150001"、"P000123"、"P000123-20260915-01"。

        Raises:
            BusinessError: 键未定义 / BATCH 未传 scope / 非 BATCH 传了 scope /
                          流水号溢出宽度（如宽度 4 位但已达 9999）。
        """
        spec = KEY_SPECS.get(key)
        if spec is None:
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING,
                f"未定义的编号规则键：{key}（支持 {sorted(KEY_SPECS)}）",
            )

        # scope 校验：BATCH 必传，其余键禁传
        if spec.needs_scope and not scope:
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING,
                f"编号规则 {key} 必须传 scope 参数（如 BATCH 需传商品编码）",
            )
        if not spec.needs_scope and scope is not None:
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING,
                f"编号规则 {key} 不接受 scope 参数",
            )

        # 日期解析（测试友好）
        effective_date = at if at is not None else _today()

        # seq_key / seq_date 计算
        # BATCH 用 "BATCH:{product_code}" 作为 seq_key，保证不同商品各自计数
        seq_key = f"BATCH:{scope}" if spec.needs_scope and scope else key
        seq_date = effective_date if spec.daily else EPOCH_DATE

        # 取号（两条 SQL 必须在同一 Session）
        self._session.execute(
            _UPSERT_SQL, {"seq_key": seq_key, "seq_date": seq_date}
        )
        next_val = self._session.execute(_SELECT_LAST_ID_SQL).scalar_one()

        # 溢出检查：宽度 4 位即单日上限 9999 单，门店场景足够，
        # 但要**显式失败**而不是产生重号
        max_val = 10**spec.width - 1
        if next_val > max_val:
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING,
                f"编号 {key} 在 {seq_date} 的流水号已超出 {spec.width} 位宽度上限（{max_val}），"
                f"请联系管理员扩容或检查是否有异常刷号",
            )

        # 拼装单号
        seq_str = str(next_val).zfill(spec.width)
        if spec.needs_scope:
            # BATCH 格式：{product_code}-{yyyyMMdd}-{NN}，如 P000123-20260915-01
            return f"{scope}-{effective_date.strftime('%Y%m%d')}-{seq_str}"
        # 其余格式：{prefix}{yyyyMMdd?}{NN}
        date_part = effective_date.strftime("%Y%m%d") if spec.daily else ""
        return f"{spec.prefix}{date_part}{seq_str}"


# ---------- 便捷依赖（阶段 2 起在 FastAPI 路由中用 Depends 注入） ----------


def get_no_seq_service(session: Session) -> NoSeqService:
    """工厂函数：从 Session 构造 NoSeqService。

    Args:
        session: 调用方的 Session（一般来自 `Depends(get_db)`）。

    Returns:
        NoSeqService 实例。

    使用示例（阶段 2+ 路由）：
        def create_trade(db: Session = Depends(get_db)):
            seq = get_no_seq_service(db)
            trade_no = seq.next_no("XS")
    """
    return NoSeqService(session)
