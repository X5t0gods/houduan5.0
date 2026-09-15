"""API v1 路由汇总 - 挂载全部 10 个模块的路由。

本阶段只挂载 health 接口。
后续阶段会逐步添加：auth, product, inventory, purchase, sale,
member, promotion, report, bi, system 共 128 个接口。
"""

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.core.config import settings

# 创建 v1 主路由
api_router = APIRouter()

# ---------- 本阶段挂载 ----------
api_router.include_router(health_router)

# ---------- 后续阶段挂载（占位注释） ----------
# api_router.include_router(auth_router, prefix="/auth", tags=["认证与权限"])
# api_router.include_router(product_router, prefix="/products", tags=["商品管理"])
# api_router.include_router(inventory_router, prefix="/inventory", tags=["库存管理"])
# api_router.include_router(purchase_router, prefix="/purchases", tags=["采购与供应商"])
# api_router.include_router(sale_router, prefix="/sales", tags=["销售与收银"])
# api_router.include_router(member_router, prefix="/members", tags=["会员管理"])
# api_router.include_router(promotion_router, prefix="/promotions", tags=["促销管理"])
# api_router.include_router(report_router, prefix="/reports", tags=["统计报表"])
# api_router.include_router(bi_router, prefix="/bi", tags=["智能数据分析"])
# api_router.include_router(system_router, prefix="/system", tags=["系统管理"])


# ---------- DEBUG 模式专用路由 ----------
if settings.DEBUG:
    from app.core.error_codes import ErrorCode
    from app.core.exceptions import BusinessError

    @api_router.get(
        "/_dev/raise-business-error",
        summary="[DEBUG] 测试业务错误通道",
        description=(
            "仅供调试：验证业务错误走 HTTP 200 + 非 0 code 通道。"
            "DEBUG=false 时此路由不存在。"
        ),
        include_in_schema=True,
        tags=["开发调试"],
    )
    def raise_business_error() -> dict:
        """抛出 BusinessError 以验证全局异常处理器。

        预期行为：返回 HTTP 200 + code=2001 + message="必填项缺失"

        Raises:
            BusinessError: 始终抛出 REQUIRED_MISSING 错误。
        """
        raise BusinessError(ErrorCode.REQUIRED_MISSING)
