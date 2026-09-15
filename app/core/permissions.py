"""权限码常量定义 - 与 docs/06 第 1.8 节、数据库 sys_permission.perm_code 一字不差。

⛔ 29 个权限码字符串必须与前端完全一致，错一个字母 = 一整个菜单消失。
本阶段只定义常量，不写 require_perm 依赖（它依赖 JWT，属阶段 2）。

阶段 2 扩展点：
- 实现 require_perm(perm_code) 依赖，逐接口校验权限
- 实现 data_scope 注入（ALL / STORE / DEPT / SELF）
"""


class Perm:
    """系统权限码常量类。

    命名规则：模块前缀 + 功能标识，与前端路由 meta.permission 一一对应。
    使用方式：require_perm(Perm.POS_USE)（阶段 2 实现）
    """

    # ---------- 经营总览 ----------
    DASHBOARD_VIEW = "dashboard:view"  # 查看经营总览

    # ---------- 收银台 ----------
    POS_USE = "pos:use"  # 使用收银台
    POS_SESSION = "pos:session"  # 交班对账

    # ---------- 会员管理 ----------
    MEMBER_LIST = "member:list"  # 会员列表
    MEMBER_DETAIL = "member:detail"  # 会员详情
    MEMBER_LEVEL = "member:level"  # 等级与积分规则

    # ---------- 促销管理 ----------
    PROMO_LIST = "promo:list"  # 促销列表
    PROMO_EDIT = "promo:edit"  # 促销配置
    PROMO_COUPON = "promo:coupon"  # 优惠券管理

    # ---------- 商品管理 ----------
    GOODS_PRODUCT_LIST = "goods:product:list"  # 商品列表
    GOODS_PRODUCT_EDIT = "goods:product:edit"  # 商品建档与修改
    GOODS_CATEGORY = "goods:category"  # 分类管理

    # ---------- 采购管理 ----------
    PUR_ORDER_LIST = "pur:order:list"  # 采购订单列表
    PUR_ORDER_CREATE = "pur:order:create"  # 采购建单
    PUR_RECEIPT = "pur:receipt"  # 收货入库
    PUR_SUPPLIER = "pur:supplier"  # 供应商管理

    # ---------- 库存管理 ----------
    INV_STOCK_LIST = "inv:stock:list"  # 库存查询
    INV_FLOW = "inv:flow"  # 库存流水
    INV_CHECK = "inv:check"  # 库存盘点
    INV_LOSS = "inv:loss"  # 报损管理
    INV_TRANSFER = "inv:transfer"  # 门店调拨

    # ---------- 统计报表 ----------
    REPORT_SALES = "report:sales"  # 销售报表 / 会员报表
    REPORT_GOODS = "report:goods"  # 商品与毛利报表
    REPORT_INV = "report:inv"  # 库存与采购报表

    # ---------- 智能分析 ----------
    BI_ANALYSIS = "bi:analysis"  # 智能分析 / 趋势预测
    BI_COMPARE = "bi:compare"  # 算法性能对比

    # ---------- 系统管理 ----------
    SYS_USER = "sys:user"  # 用户管理
    SYS_ROLE = "sys:role"  # 角色与权限
    SYS_CONFIG = "sys:config"  # 参数与备份


# 全部权限码列表（便于阶段 2 做权限校验与测试）
ALL_PERMISSIONS: list[str] = [
    Perm.DASHBOARD_VIEW,
    Perm.POS_USE,
    Perm.POS_SESSION,
    Perm.MEMBER_LIST,
    Perm.MEMBER_DETAIL,
    Perm.MEMBER_LEVEL,
    Perm.PROMO_LIST,
    Perm.PROMO_EDIT,
    Perm.PROMO_COUPON,
    Perm.GOODS_PRODUCT_LIST,
    Perm.GOODS_PRODUCT_EDIT,
    Perm.GOODS_CATEGORY,
    Perm.PUR_ORDER_LIST,
    Perm.PUR_ORDER_CREATE,
    Perm.PUR_RECEIPT,
    Perm.PUR_SUPPLIER,
    Perm.INV_STOCK_LIST,
    Perm.INV_FLOW,
    Perm.INV_CHECK,
    Perm.INV_LOSS,
    Perm.INV_TRANSFER,
    Perm.REPORT_SALES,
    Perm.REPORT_GOODS,
    Perm.REPORT_INV,
    Perm.BI_ANALYSIS,
    Perm.BI_COMPARE,
    Perm.SYS_USER,
    Perm.SYS_ROLE,
    Perm.SYS_CONFIG,
]
