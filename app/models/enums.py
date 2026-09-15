"""字符串状态常量与数值枚举 —— 从 db/01_schema.sql 字段 COMMENT 提取。

⚠️ 取值来源以脚本注释为准，不要自己发明。
⚠️ 不使用 sqlalchemy.Enum —— 数据库里存的是 VARCHAR/TINYINT，用 Python 常量类即可，
   避免枚举类型迁移时的兼容问题。

使用方式：
    from app.models.enums import TradeStatus, StockFlowType
    if trade.status == TradeStatus.FINISHED: ...
"""

from __future__ import annotations


# ---------- 通用启用/停用（TINYINT 1/0） ----------
class StatusFlag:
    """通用状态位（TINYINT）。多数表的 status 字段用这个。

    接口出参为 number（0/1），不是 bool —— 阶段 0 已定。
    """

    ENABLED = 1
    DISABLED = 0


class ResultFlag:
    """操作结果（TINYINT 1成功 0失败）。用于 sys_operation_log.result / sys_backup.result / sys_login_log.result。"""

    SUCCESS = 1
    FAIL = 0


# ---------- 权限与用户域 ----------
class DataScope:
    """sys_role.data_scope —— 数据范围。

    阶段 2 起 require_perm 依赖会读取此字段做 repository 层的数据过滤。
    """

    ALL = "ALL"      # 全部门店
    STORE = "STORE"  # 本门店
    DEPT = "DEPT"    # 本岗位
    SELF = "SELF"    # 仅本人


class PermType:
    """sys_permission.perm_type —— 权限节点类型（TINYINT）。"""

    DIRECTORY = 1  # 目录
    MENU = 2       # 菜单（页面级）
    BUTTON = 3     # 按钮（操作级）


class ConfigValueType:
    """sys_config.value_type —— 参数值类型。"""

    STRING = "string"
    INT = "int"
    DECIMAL = "decimal"
    BOOL = "bool"
    JSON = "json"


class BackupTriggerType:
    """sys_backup.trigger_type —— 备份触发方式。"""

    MANUAL = "MANUAL"
    AUTO = "AUTO"


class BackupType:
    """sys_backup.backup_type —— 备份类型。"""

    FULL = "FULL"
    INCR = "INCR"


# ---------- 基础数据域 ----------
class UnitType:
    """base_unit.unit_type —— 单位类型（TINYINT）。"""

    BASE = 1      # 基本单位
    AUXILIARY = 2  # 辅助单位


class BarcodeType:
    """prd_barcode.barcode_type —— 条码类型（TINYINT）。"""

    PRIMARY = 1     # 主条码
    SECONDARY = 2   # 附加条码


# ---------- 商品域 ----------
class PriceChangeType:
    """prd_price_history.change_type —— 价格变更类型（VARCHAR，中文枚举）。"""

    PURCHASE = "进价"
    SALE = "售价"
    MEMBER = "会员价"


# ---------- 供应商与采购域 ----------
class SettleType:
    """pur_supplier.settle_type —— 结算方式。"""

    CASH = "CASH"      # 现结
    PERIOD = "PERIOD"  # 账期


class PurchaseOrderStatus:
    """pur_order.status —— 采购订单状态机。

    流转：DRAFT → AUDITED → PARTIAL → RECEIVED → FINISHED
                                        ↘ VOID
    """

    DRAFT = "DRAFT"        # 待审核
    AUDITED = "AUDITED"    # 已审核
    PARTIAL = "PARTIAL"    # 部分收货
    RECEIVED = "RECEIVED"  # 已收货
    FINISHED = "FINISHED"  # 已完成
    VOID = "VOID"          # 已作废


class PurchaseOrderSourceType:
    """pur_order.source_type —— 订单来源。"""

    MANUAL = "MANUAL"        # 手工建单
    REPLENISH = "REPLENISH"  # 补货建议自动生成


class ReceiptStatus:
    """pur_receipt.status —— 收货单状态。"""

    FINISHED = "FINISHED"
    VOID = "VOID"


class ReturnStatus:
    """pur_return.status / sal_return.refund_status 通用退货状态。"""

    FINISHED = "FINISHED"
    VOID = "VOID"


class PayMethod:
    """pur_payment.pay_method / sal_trade_payment.pay_method / sal_return.refund_method。

    ⚠️ pur_payment 只允许 CASH/TRANSFER/WECHAT/ALIPAY（对公付款），
       sal_trade_payment 支持全部（含 CARD/BALANCE/POINTS/OTHER）。
       此处列全集，业务侧按场景过滤。
    """

    CASH = "CASH"        # 现金
    WECHAT = "WECHAT"    # 微信
    ALIPAY = "ALIPAY"    # 支付宝
    CARD = "CARD"        # 银行卡
    BALANCE = "BALANCE"  # 会员储值
    POINTS = "POINTS"    # 积分抵扣
    TRANSFER = "TRANSFER"  # 银行转账（对公）
    OTHER = "OTHER"


class RefundStatus:
    """sal_return.refund_status —— 销售退货退款状态。"""

    PENDING = "PENDING"    # 待退款
    REFUNDED = "REFUNDED"  # 已退款
    FAILED = "FAILED"      # 退款失败


class PayStatus:
    """sal_trade_payment.pay_status —— 支付明细状态。"""

    SUCCESS = "SUCCESS"
    FAIL = "FAIL"
    REFUNDED = "REFUNDED"


# ---------- 库存域 ----------
class StockFlowType:
    """inv_stock_flow.flow_type —— 库存流水类型（库存数据的唯一真相源）。

    ⚠️ 全部 9 种取值必须一字不差，前端流水筛选下拉依赖此列表。
    """

    PURCHASE_IN = "PURCHASE_IN"              # 采购入库
    SALE_OUT = "SALE_OUT"                    # 销售出库
    SALE_RETURN_IN = "SALE_RETURN_IN"        # 销售退货入库
    PURCHASE_RETURN_OUT = "PURCHASE_RETURN_OUT"  # 采购退货出库
    CHECK_ADJUST = "CHECK_ADJUST"            # 盘点调整
    LOSS_OUT = "LOSS_OUT"                    # 报损出库
    TRANSFER_IN = "TRANSFER_IN"              # 调拨入库
    TRANSFER_OUT = "TRANSFER_OUT"            # 调拨出库
    INIT = "INIT"                            # 期初建账


class FlowDirection:
    """inv_stock_flow.direction / mem_balance_flow.direction / mem_point_flow.direction。

    TINYINT：1 表示增加/入库，-1 表示减少/出库。
    库存勾稽 SQL：`Σ(quantity × direction)` 应等于当前库存。
    """

    IN = 1
    OUT = -1


class CheckStatus:
    """inv_check.status —— 盘点单状态机。

    流转：DRAFT → SUBMITTED → AUDITED（审核后生成 CHECK_ADJUST 流水）
                              ↘ VOID
    """

    DRAFT = "DRAFT"          # 盘点中
    SUBMITTED = "SUBMITTED"  # 待审核
    AUDITED = "AUDITED"      # 已审核
    VOID = "VOID"            # 已作废


class CheckType:
    """inv_check.check_type —— 盘点方式。"""

    ALL = "ALL"            # 全盘
    PART = "PART"          # 抽盘
    CATEGORY = "CATEGORY"  # 按分类
    SHELF = "SHELF"        # 按货架


class LossType:
    """inv_loss.loss_type —— 报损类型。"""

    BREAK = "BREAK"    # 破损
    EXPIRE = "EXPIRE"  # 过期
    FRESH = "FRESH"    # 生鲜损耗
    OTHER = "OTHER"


class TransferStatus:
    """inv_transfer.status —— 调拨单状态机。

    流转：DRAFT → OUT → IN
                ↘ VOID
    """

    DRAFT = "DRAFT"  # 待调出
    OUT = "OUT"      # 已调出
    IN = "IN"        # 已入库
    VOID = "VOID"    # 已作废


class BatchStatus:
    """inv_batch.status —— 批次状态（TINYINT）。"""

    ACTIVE = 1    # 在库
    EMPTY = 0     # 已耗尽
    EXPIRED = 2   # 已过期


class DiffReason:
    """inv_check_item.diff_reason —— 盘点差异原因（错误码 5001 要求必填）。"""

    MISSING = "漏记"
    LOSS = "损耗"
    WRONG_COUNT = "错盘"
    WRONG_CODE = "串码"


# ---------- 会员域 ----------
class Gender:
    """mem_member.gender —— 性别（TINYINT）。"""

    UNKNOWN = 0
    MALE = 1
    FEMALE = 2


class MemberStatus:
    """mem_member.status —— 会员状态（TINYINT）。"""

    NORMAL = 1    # 正常
    FROZEN = 0    # 冻结
    LOST = 2      # 挂失


class MemberSource:
    """mem_member.source —— 会员来源。"""

    STORE = "STORE"    # 到店
    REFER = "REFER"    # 推荐
    ONLINE = "ONLINE"  # 线上


class UpgradeCondition:
    """mem_level.upgrade_condition —— 会员升级依据。"""

    CONSUME = "CONSUME"  # 累计消费
    POINTS = "POINTS"    # 累计积分


class BalanceFlowType:
    """mem_balance_flow.flow_type —— 会员储值流水类型。

    ⚠️ 关键规则：消费先扣赠送余额再扣本金；退款只退本金（防"充100送20→退款套120"）。
    """

    RECHARGE = "RECHARGE"  # 充值
    GIFT = "GIFT"          # 赠送
    CONSUME = "CONSUME"    # 消费
    REFUND = "REFUND"      # 退款
    ADJUST = "ADJUST"      # 手工调整


class PointFlowType:
    """mem_point_flow.flow_type —— 会员积分流水类型。"""

    EARN = "EARN"      # 消费获取
    RETURN = "RETURN"  # 消费退回（订单退货）
    DEDUCT = "DEDUCT"  # 积分抵扣
    EXPIRE = "EXPIRE"  # 过期扣减
    ADJUST = "ADJUST"  # 手工调整


# ---------- 促销域 ----------
class PromotionType:
    """pro_promotion.promo_type —— 促销类型。

    ⚠️ 计算顺序按 priority 升序（阶段 4 促销引擎实现）：
       SPECIAL(1) → DISCOUNT(2) → COMBO(3) → FULL_REDUCE(4) → FULL_GIFT(5) → COUPON(6) → MEMBER(7)
       MEMBER 类型不参与后端计算（由前端 store 单独汇总，避免重复扣减）。
    """

    SPECIAL = "SPECIAL"          # 特价
    DISCOUNT = "DISCOUNT"        # 折扣
    FULL_REDUCE = "FULL_REDUCE"  # 满减
    FULL_GIFT = "FULL_GIFT"      # 满赠
    COMBO = "COMBO"              # 组合套餐
    COUPON = "COUPON"            # 优惠券
    MEMBER = "MEMBER"            # 会员专享


class PromotionStatus:
    """pro_promotion.status —— 促销生命周期。"""

    DRAFT = "DRAFT"        # 未开始
    RUNNING = "RUNNING"    # 进行中
    STOPPED = "STOPPED"    # 已停用
    EXPIRED = "EXPIRED"    # 已结束


class PromotionItemType:
    """pro_promotion_item.item_type —— 促销适用对象类型。"""

    PRODUCT = "PRODUCT"    # 单个商品
    CATEGORY = "CATEGORY"  # 整个分类


class CouponType:
    """pro_coupon.coupon_type —— 优惠券类型。"""

    CASH = "CASH"          # 现金券
    DISCOUNT = "DISCOUNT"  # 折扣券
    GIFT = "GIFT"          # 赠品券


class CouponUseStatus:
    """pro_coupon_record.use_status —— 优惠券领用状态。"""

    UNUSED = "UNUSED"    # 未使用
    USED = "USED"        # 已核销
    EXPIRED = "EXPIRED"  # 已过期


class MemberTagType:
    """mem_tag.tag_type —— 会员标签类型。"""

    AUTO = "AUTO"      # 自动打标
    MANUAL = "MANUAL"  # 手工打标


# ---------- 销售域 ----------
class TradeStatus:
    """sal_trade.status —— 销售单状态。

    ⚠️ 销售单一经创建默认 FINISHED（收银即完成），
       RETURNED 表示已被退货单关联，VOID 表示作废（当日撤单）。
    """

    FINISHED = "FINISHED"  # 已完成
    RETURNED = "RETURNED"  # 已退货
    VOID = "VOID"          # 已作废


class HoldStatus:
    """sal_hold.status —— 挂单状态。"""

    HOLDING = "HOLDING"    # 挂起中
    RESUMED = "RESUMED"    # 已取单
    CANCELED = "CANCELED"  # 已取消


class SessionStatus:
    """sal_session.status —— 收银班次状态机。

    流转：OPEN → CLOSED → AUDITED
    """

    OPEN = "OPEN"        # 营业中
    CLOSED = "CLOSED"    # 待审核（已交班）
    AUDITED = "AUDITED"  # 已审核


# ---------- 智能分析域 ----------
class BiTaskType:
    """bi_analysis_task.task_type —— BI 任务类型。"""

    ASSOCIATION = "ASSOCIATION"  # 关联规则挖掘
    FORECAST = "FORECAST"        # 销量预测
    REPLENISH = "REPLENISH"      # 补货建议


class BiAlgorithm:
    """bi_analysis_task.algorithm —— 关联规则挖掘算法。

    ⚠️ 阶段 9 的 /bi/compare 接口要对比 Apriori vs FP-Growth 的耗时，
       这两个字符串会作为参数直接传入 mlxtend。
    """

    FP_GROWTH = "FP_GROWTH"
    APRIORI = "APRIORI"


class BiTaskStatus:
    """bi_analysis_task.status —— BI 任务生命周期。

    流转：PENDING → RUNNING → SUCCESS
                            ↘ FAILED
    """

    PENDING = "PENDING"  # 排队中
    RUNNING = "RUNNING"  # 执行中
    SUCCESS = "SUCCESS"  # 成功
    FAILED = "FAILED"    # 失败


class BiSuggestion:
    """bi_association_rule.suggestion —— 规则建议动作（中文枚举）。"""

    BUNDLE = "捆绑"
    DISPLAY = "陈列"
    COMBO = "套餐"
    RECOMMEND = "推荐"


# ---------- 单号前缀（阶段 1 sequence.py 使用） ----------
class SeqKey:
    """sys_no_seq.seq_key —— 16 类单号前缀。

    ⚠️ 与 db/01_schema.sql 中 sys_no_seq 表的 seq_key 注释一字不差：
       XS/CG/SH/CT/FK/PD/BS/DB/JB/CZ/GD/P/S/M/BATCH
       共 16 个 key（其中 P/S/M/BATCH 是编码类，其余是单据类）。
    """

    # 编码类（全局作用域，跨日不重置）
    PRODUCT = "P"        # 商品编码 P + 6 位
    SUPPLIER = "S"       # 供应商编码 S + 6 位
    # 编码类（按日作用域）
    MEMBER = "M"         # 会员卡号 M + yyyyMMdd + 4 位
    BATCH = "BATCH"      # 批次号 P{product_code}-yyyyMMdd-NN

    # 单据类（按日作用域）
    TRADE = "XS"         # 销售单
    TRADE_RETURN = "TH"  # 销售退货单
    PURCHASE = "CG"      # 采购订单
    RECEIPT = "SH"       # 采购收货单
    PUR_RETURN = "CT"    # 采购退货单
    PAYMENT = "FK"       # 采购付款单
    CHECK = "PD"         # 盘点单
    LOSS = "BS"          # 报损单
    TRANSFER = "DB"      # 调拨单
    SESSION = "JB"       # 收银班次
    RECHARGE = "CZ"      # 会员充值单
    HOLD = "GD"          # 挂单号
