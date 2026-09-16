"""API v1 路由汇总 - 挂载全部 10 个模块的路由。

阶段 0：health
阶段 2：+ auth（6 接口） + _dev/authorized（DEBUG 权限调试路由）
后续阶段：product / inventory / purchase / sale / member / promotion / report / bi / system
"""

from fastapi import APIRouter, Depends

from app.api.v1.auth import router as auth_router
from app.api.v1.category import router as category_router
from app.api.v1.health import router as health_router
from app.api.v1.product import router as product_router
from app.api.v1.sale import router as sale_router
from app.api.v1.unit import router as unit_router
from app.core.config import settings
from app.core.response import success
from app.deps import require_perm

# 创建 v1 主路由
api_router = APIRouter()

# ---------- 已挂载 ----------
api_router.include_router(health_router)
api_router.include_router(auth_router)
# 阶段 3：商品基础数据
api_router.include_router(product_router)
api_router.include_router(category_router)
api_router.include_router(unit_router)
# 阶段 4：销售与收银
api_router.include_router(sale_router)

# ---------- 后续阶段挂载（占位注释） ----------
# api_router.include_router(inventory_router, prefix="/inventory", tags=["库存管理"])
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

    # ⭐ 阶段 2 新增：受权限保护的调试路由（用于验证 require_perm 真的生效）
    # spec 4.11：用最小代价验证权限依赖，比编造业务接口更干净
    # 阶段 3 起会删除此路由
    @api_router.get(
        "/_dev/authorized",
        summary="[DEBUG] 测试权限依赖（需 sys:user）",
        description=(
            "仅供调试：验证 `require_perm('sys:user')` 依赖真的生效。\n\n"
            "- 未登录 → HTTP 401\n"
            "- `cashier01` 登录（无 sys:user 权限）→ HTTP 403 + code=1040\n"
            "- `admin` 登录 → HTTP 200 + `{ok: true}`\n\n"
            "DEBUG=false 时此路由必须不存在（返回 404）。阶段 3 起会删除。"
        ),
        include_in_schema=True,
        tags=["开发调试"],
        dependencies=[Depends(require_perm("sys:user"))],
    )
    def dev_authorized() -> dict:
        """受 sys:user 权限保护的调试接口。

        Returns:
            统一响应体，data = {"ok": True}。
        """
        return success({"ok": True})
