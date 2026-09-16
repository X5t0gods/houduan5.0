"""业务错误码枚举 - 与 docs/06_后端接口文档.md 第 1.4 节完全一致。

错误码按模块分段：
- 0: 成功
- 1xxx: 认证模块
- 2xxx: 基础数据/商品
- 3xxx: 供应商
- 4xxx: 采购
- 5xxx: 库存
- 6xxx: 收银（商品扫描）
- 7xxx: 收银（支付/库存）
- 8xxx: 销售（退货）
- 9xxx: 会员
- 10xxx: 智能分析

⛔ 数值与文案一字不差，前端已内置对应的中文提示文案。
"""

from enum import IntEnum


class ErrorCode(IntEnum):
    """业务错误码枚举。

    前端根据 code 值显示对应的中文提示，
    因此数值和含义必须与接口文档严格一致。
    """

    # ---------- 通用 ----------
    SUCCESS = 0

    # ---------- 认证模块 (1xxx) ----------
    CAPTCHA_INVALID = 1001
    LOGIN_FAILED = 1002
    ACCOUNT_LOCKED = 1003
    ACCOUNT_DISABLED = 1004
    NO_PERMISSION = 1040

    # ---------- 基础数据/商品 (2xxx) ----------
    REQUIRED_MISSING = 2001
    PRODUCT_DUPLICATE = 2002
    PRICE_BELOW_COST = 2003
    # 阶段 3 新增：商品/分类业务错误（docs/06 1.4 节未定义，本阶段补充；
    # 前端 request.ts:96 有 `ERROR_MESSAGE[res.code] ?? res.message ?? '操作失败'` fallback，
    # 新增码安全但必须后端返回中文 message）
    PRODUCT_NOT_FOUND = 2004              # 商品不存在
    CATEGORY_INVALID = 2005               # 分类不存在或操作非法（包含层级超限、环引用、有子分类）
    CATEGORY_REFERENCED = 2006            # 分类被商品引用，只能停用不可删除
    PRODUCT_HAS_BUSINESS_FLOW = 2007      # 商品已产生业务流水，不可删除

    # ---------- 供应商 (3xxx) ----------
    SUPPLIER_DISABLED = 3001

    # ---------- 采购 (4xxx) ----------
    ORDER_STATUS_INVALID = 4001
    RECEIVE_QTY_EXCEED = 4002
    # 阶段 6 新增（沿用阶段 3 做法：docs/06 1.4 节未定义，前端 request.ts:96
    # 有 `ERROR_MESSAGE[code] ?? res.message ?? '操作失败'` fallback，新增码安全
    # 但后端必须返回中文 message）
    PURCHASE_ORDER_NOT_FOUND = 4003       # 采购订单不存在
    PURCHASE_STATUS_FORBID = 4004         # 该状态下不允许此操作
    SUPPLIER_NOT_FOUND = 4005             # 供应商不存在或已停用

    # ---------- 库存 (5xxx) ----------
    CHECK_DIFF_REASON_MISSING = 5001
    DOC_ALREADY_AUDITED = 5002

    # ---------- 收银-商品扫描 (6xxx) ----------
    BARCODE_NOT_FOUND = 6001
    PRODUCT_DISABLED = 6002
    STOCK_NOT_ENOUGH = 6003

    # ---------- 收银-支付/库存 (7xxx) ----------
    PAY_AMOUNT_MISMATCH = 7001
    MEMBER_BALANCE_NOT_ENOUGH = 7002
    MEMBER_POINT_NOT_ENOUGH = 7003
    STOCK_NOT_ENOUGH_SALE = 7004

    # ---------- 销售-退货 (8xxx) ----------
    RETURN_EXPIRED = 8001
    RETURN_QTY_EXCEED = 8002
    TRADE_NOT_FOUND = 8003

    # ---------- 会员 (9xxx) ----------
    PHONE_ALREADY_MEMBER = 9001
    AMOUNT_INVALID = 9002
    GIFT_RATIO_EXCEED = 9003

    # ---------- 智能分析 (10xxx) ----------
    BI_DATA_NOT_ENOUGH = 10001
    BI_PARAM_INVALID = 10002


# 错误码 → 中文文案映射（用于兜底，当 BusinessError 未指定 message 时自动取此文案）
ERROR_MESSAGES: dict[int, str] = {
    ErrorCode.SUCCESS: "成功",
    ErrorCode.CAPTCHA_INVALID: "验证码错误或已失效，请重新输入",
    ErrorCode.LOGIN_FAILED: "用户名或密码错误",
    ErrorCode.ACCOUNT_LOCKED: "账号已被锁定，请稍后再试",
    ErrorCode.ACCOUNT_DISABLED: "账号已停用，请联系管理员",
    ErrorCode.NO_PERMISSION: "没有该操作的权限",
    ErrorCode.REQUIRED_MISSING: "必填项缺失",
    ErrorCode.PRODUCT_DUPLICATE: "商品编码或条码已存在",
    ErrorCode.PRICE_BELOW_COST: "售价低于进价，请确认后重试",
    # 阶段 3 新增的 4 个码的默认文案（具体调用时可传定制 message 覆盖，
    # 如 CATEGORY_REFERENCED 需带上实际引用数）
    ErrorCode.PRODUCT_NOT_FOUND: "商品不存在",
    ErrorCode.CATEGORY_INVALID: "分类不存在或操作非法",
    ErrorCode.CATEGORY_REFERENCED: "该分类已被商品引用，只能停用不可删除",
    ErrorCode.PRODUCT_HAS_BUSINESS_FLOW: "该商品已产生业务流水，不可删除",
    ErrorCode.SUPPLIER_DISABLED: "供应商已停用",
    ErrorCode.ORDER_STATUS_INVALID: "当前单据状态不允许收货",
    ErrorCode.RECEIVE_QTY_EXCEED: "收货数量超出订单剩余数量",
    # 阶段 6 新增 3 个码的默认文案（调用时可传定制 message 覆盖）
    ErrorCode.PURCHASE_ORDER_NOT_FOUND: "采购订单不存在",
    ErrorCode.PURCHASE_STATUS_FORBID: "该状态下不允许此操作",
    ErrorCode.SUPPLIER_NOT_FOUND: "供应商不存在或已停用",
    ErrorCode.CHECK_DIFF_REASON_MISSING: "盘点差异未填写原因",
    ErrorCode.DOC_ALREADY_AUDITED: "单据已审核，无法重复操作",
    ErrorCode.BARCODE_NOT_FOUND: "未找到该条码对应的商品",
    ErrorCode.PRODUCT_DISABLED: "该商品已停用",
    ErrorCode.STOCK_NOT_ENOUGH: "库存不足",
    ErrorCode.PAY_AMOUNT_MISMATCH: "支付金额与应收金额不一致",
    ErrorCode.MEMBER_BALANCE_NOT_ENOUGH: "会员储值余额不足",
    ErrorCode.MEMBER_POINT_NOT_ENOUGH: "会员积分不足",
    ErrorCode.STOCK_NOT_ENOUGH_SALE: "库存不足，无法完成销售",
    ErrorCode.RETURN_EXPIRED: "超出退货期限",
    ErrorCode.RETURN_QTY_EXCEED: "退货数量超出可退数量",
    ErrorCode.TRADE_NOT_FOUND: "原销售单不存在",
    ErrorCode.PHONE_ALREADY_MEMBER: "该手机号已是会员",
    ErrorCode.AMOUNT_INVALID: "金额不合法",
    ErrorCode.GIFT_RATIO_EXCEED: "赠送比例超出限制",
    ErrorCode.BI_DATA_NOT_ENOUGH: "交易数据不足，无法进行关联分析",
    ErrorCode.BI_PARAM_INVALID: "挖掘参数不合法",
}


def get_error_message(code: int) -> str:
    """根据错误码获取对应的中文文案。

    Args:
        code: 业务错误码。

    Returns:
        对应的中文提示文案，未知错误码返回通用提示。
    """
    return ERROR_MESSAGES.get(code, "未知错误")
