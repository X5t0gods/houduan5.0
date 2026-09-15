"""权限码常量定义 - 与 docs/06 第 1.8 节、数据库 sys_permission.perm_code 一字不差。

⛔ 29 个功能权限码字符串必须与前端完全一致，错一个字母 = 一整个菜单消失。

阶段 2 扩展：
- 新增 3 个**目录码**常量（menu:daily / menu:stock / menu:analysis）
  仅用于权限树层级展示，不进入 /auth/me 的 permissions（perm_type=1 过滤掉）
- 新增 perm_type 常量（PERM_TYPE_DIRECTORY=1 / PERM_TYPE_FUNCTION=2）
  ⚠️ 数据库中**不存在 perm_type=3**（按钮），docs/06 与建表脚本注释里写的
  “3 按钮”实际未被使用，属文档与实现不一致处

阶段 2 已接入 require_perm 依赖（见 app/deps.py）；data_scope 过滤已提供
骨架函数 resolve_store_filter，阶段 3 起由各 repository 调用。
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


class Menu:
    """目录权限码常量（perm_type=1，仅用于权限树层级展示）。

    ⚠️ **不进入** `/auth/me` 的 permissions 数组，也不作为 `require_perm` 的参数。
    阶段 10 的“角色与权限”页面渲染权限树时需要（作为一级分组节点）。
    """

    DAILY = "menu:daily"        # 日常经营（一级目录）
    STOCK = "menu:stock"        # 进销存（一级目录）
    ANALYSIS = "menu:analysis"  # 分析与设置（一级目录）


# ---------- perm_type 常量 ----------
# ⚠️ 数据库中不存在 PERM_TYPE_BUTTON=3（按钮），文档描述与实现不一致（spec 1.2 冲突 ③）。
# 本阶段只定义实际存在的两种，避免后续误用不存在的枚举值。
PERM_TYPE_DIRECTORY: int = 1   # 目录（权限树层级展示）
PERM_TYPE_FUNCTION: int = 2    # 菜单/页面级功能权限


# 全部**功能**权限码列表（便于阶段 2 做权限校验与测试，不包含目录码）
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

# 全部目录码列表（阶段 10 权限树渲染使用）
ALL_MENUS: list[str] = [
    Menu.DAILY,
    Menu.STOCK,
    Menu.ANALYSIS,
]
