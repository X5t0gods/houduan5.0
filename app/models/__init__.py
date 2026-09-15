"""模型层 - SQLAlchemy 2.0 声明式映射（62 张表）。

职责：定义 ORM 模型，与 db/01_schema.sql 逐列对齐。
禁止事项：
- 不写业务方法、不写校验逻辑、不写 @property 计算字段
- 不写 relationship()（决策 2：避免隐式懒加载与 N+1）
- 不用 Base.metadata.create_all() 建表（结构由 db/01_schema.sql 唯一维护）

按 8 个数据域拆分文件（阶段 1 交付物）：
- base_models: base_store / base_pos / base_unit / base_shelf（4 张）
- sys_models:  角色权限、日志、参数、字典、验证码、备份、单号序列（12 张）
- prd_models:  商品域（7 张）
- pur_models:  采购与供应商（9 张）
- inv_models:  库存（9 张）
- mem_models:  会员（6 张）
- pro_models:  促销（4 张）
- sal_models:  销售（7 张）
- bi_models:   智能分析（4 张）

__init__.py 汇总导入全部模型，保证 Base.metadata 完整（check_models.py 依赖此机制）。
"""

from app.models.base_models import BasePos, BaseShelf, BaseStore, BaseUnit
from app.models.bi_models import (
    BiAnalysisTask,
    BiAssociationRule,
    BiDailyStat,
    BiRecommendCache,
)
from app.models.inv_models import (
    InvBatch,
    InvCheck,
    InvCheckItem,
    InvLoss,
    InvLossItem,
    InvStock,
    InvStockFlow,
    InvTransfer,
    InvTransferItem,
)
from app.models.mem_models import (
    MemBalanceFlow,
    MemLevel,
    MemMember,
    MemMemberTag,
    MemPointFlow,
    MemTag,
)
from app.models.prd_models import (
    PrdBarcode,
    PrdCategory,
    PrdPriceHistory,
    PrdProduct,
    PrdProductTag,
    PrdSpu,
    PrdTag,
)
from app.models.pro_models import (
    ProCoupon,
    ProCouponRecord,
    ProPromotion,
    ProPromotionItem,
)
from app.models.pur_models import (
    PurOrder,
    PurOrderItem,
    PurPayment,
    PurReceipt,
    PurReceiptItem,
    PurReturn,
    PurReturnItem,
    PurSupplier,
    PurSupplierProduct,
)
from app.models.sal_models import (
    SalHold,
    SalReturn,
    SalReturnItem,
    SalSession,
    SalTrade,
    SalTradeItem,
    SalTradePayment,
)
from app.models.sys_models import (
    SysBackup,
    SysCaptcha,
    SysConfig,
    SysDict,
    SysLoginLog,
    SysNoSeq,
    SysOperationLog,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)

__all__ = [
    # base_ (4)
    "BaseStore",
    "BasePos",
    "BaseUnit",
    "BaseShelf",
    # sys_ (12)
    "SysRole",
    "SysPermission",
    "SysUser",
    "SysUserRole",
    "SysRolePermission",
    "SysOperationLog",
    "SysLoginLog",
    "SysConfig",
    "SysDict",
    "SysCaptcha",
    "SysBackup",
    "SysNoSeq",
    # prd_ (7)
    "PrdCategory",
    "PrdSpu",
    "PrdProduct",
    "PrdBarcode",
    "PrdPriceHistory",
    "PrdTag",
    "PrdProductTag",
    # pur_ (9)
    "PurSupplier",
    "PurSupplierProduct",
    "PurOrder",
    "PurOrderItem",
    "PurReceipt",
    "PurReceiptItem",
    "PurReturn",
    "PurReturnItem",
    "PurPayment",
    # inv_ (9)
    "InvStock",
    "InvBatch",
    "InvStockFlow",
    "InvCheck",
    "InvCheckItem",
    "InvLoss",
    "InvLossItem",
    "InvTransfer",
    "InvTransferItem",
    # mem_ (6)
    "MemLevel",
    "MemMember",
    "MemPointFlow",
    "MemBalanceFlow",
    "MemTag",
    "MemMemberTag",
    # pro_ (4)
    "ProPromotion",
    "ProPromotionItem",
    "ProCoupon",
    "ProCouponRecord",
    # sal_ (7)
    "SalTrade",
    "SalTradeItem",
    "SalTradePayment",
    "SalReturn",
    "SalReturnItem",
    "SalHold",
    "SalSession",
    # bi_ (4)
    "BiAnalysisTask",
    "BiAssociationRule",
    "BiRecommendCache",
    "BiDailyStat",
]
