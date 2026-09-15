"""FastAPI 应用入口 - 实例创建、生命周期、中间件、异常处理器、路由挂载。

启动方式：
- 开发：uvicorn app.main:app --reload
- 直接运行：python -m app.main
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import set_start_time
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.middleware.request_id import RequestIdMiddleware

logger = get_logger(__name__)

# OpenAPI tags 元数据 - 按 10 个模块分组，保证 /docs 不会乱成一团
tags_metadata = [
    {"name": "认证与权限", "description": "登录、登出、令牌刷新、验证码、当前用户信息"},
    {"name": "商品管理", "description": "商品CRUD、分类、单位、条码查询、批量导入"},
    {"name": "库存管理", "description": "库存查询、流水、预警、盘点、报损、调拨"},
    {"name": "采购与供应商", "description": "采购单、收货、退货、供应商、对账、付款"},
    {"name": "销售与收银", "description": "购物车、结算、销售单、退货、挂单、交班"},
    {"name": "会员管理", "description": "会员CRUD、等级、充值、储值、积分、消费档案"},
    {"name": "促销管理", "description": "促销规则、优惠券、试算、效果分析、匹配"},
    {"name": "统计报表", "description": "看板、销售日报、商品排行、毛利、进销存、导出"},
    {"name": "智能数据分析", "description": "关联规则挖掘、推荐、预测、补货建议、算法对比"},
    {"name": "系统管理", "description": "用户、角色、权限、参数、字典、日志、备份"},
    {"name": "开发调试", "description": "仅 DEBUG=true 时可用，用于验证基础设施行为"},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理。

    启动时：
    - 初始化日志系统
    - 记录启动时间（供健康检查和 /system/info 复用）
    - 输出启动信息

    关闭时：
    - 释放数据库连接池
    """
    # ---------- 启动 ----------
    setup_logging()

    # 记录启动时间（使用配置的时区）
    start_time = datetime.now(ZoneInfo(settings.TZ))
    set_start_time(start_time)

    logger.info(
        f"服务已启动，监听 {settings.HOST}:{settings.PORT} | "
        f"环境={settings.APP_ENV} | 版本={settings.APP_VERSION}"
    )

    yield

    # ---------- 关闭 ----------
    logger.info("服务正在关闭，释放数据库连接池...")
    engine.dispose()
    logger.info("服务已关闭")


# 创建 FastAPI 实例
app = FastAPI(
    title="中小型社区超市销售管理系统 · 后端接口",
    version=settings.APP_VERSION,
    description=(
        "## 阶段 0：基础设施\n\n"
        "当前为工程骨架阶段，仅包含健康检查接口。\n\n"
        "- `/docs` - Swagger UI 交互式文档\n"
        "- `/redoc` - ReDoc 格式文档\n"
        "- `/api/v1/health` - 健康检查\n\n"
        "### 技术栈\n"
        "- FastAPI + Pydantic v2 + SQLAlchemy 2.0（同步）\n"
        "- MySQL 8 + PyMySQL\n"
        "- bcrypt 密码哈希（不使用 passlib）\n\n"
        "### 约定\n"
        "- 业务失败一律 HTTP 200 + 非 0 code\n"
        "- HTTP 状态码只表达传输层/认证层（401/403/404/500）\n"
        "- 金额/数量在 JSON 中为 number（不是字符串）\n"
        "- 时间格式 YYYY-MM-DD HH:mm:ss\n"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)

# ---------- 中间件（注意顺序：后添加的先执行） ----------

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],  # 允许前端读取 request_id
)

# Request ID 中间件（在 CORS 之后添加，确保先执行）
app.add_middleware(RequestIdMiddleware)

# ---------- 异常处理器 ----------
register_exception_handlers(app)

# ---------- 路由挂载 ----------
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# ---------- 开发入口 ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
