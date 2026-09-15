"""健康检查接口 - GET /api/v1/health。

关键约定：
- 数据库连不上时，服务仍要能正常启动，此接口也仍要返回 200
- 只是 data.db_status 为 "disconnected"
- 该接口不加权限校验（健康检查要能被监控直接调）
- uptime_seconds / start_time 由 app.main 的 lifespan 启动时记录
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from app.core.config import settings
from app.core.database import check_db_connection
from app.core.response import success
from app.schemas.common import HealthData

router = APIRouter(tags=["系统管理"])

# 服务启动时间（由 main.py lifespan 设置）
_start_time: datetime | None = None


def set_start_time(start_time: datetime) -> None:
    """设置服务启动时间（由 lifespan 调用）。

    阶段 10 的 GET /system/info 要复用同一套计算，
    因此把逻辑抽成可复用函数，不写死在路由里。

    Args:
        start_time: 服务启动时的 datetime 对象。
    """
    global _start_time  # noqa: PLW0603
    _start_time = start_time


def get_uptime_info() -> dict[str, object]:
    """获取服务运行时长信息（可复用）。

    Returns:
        包含 uptime_seconds 和 start_time 字符串的字典。
    """
    if _start_time is None:
        return {"uptime_seconds": 0, "start_time": ""}

    now = datetime.now(ZoneInfo(settings.TZ))
    uptime = int((now - _start_time).total_seconds())
    return {
        "uptime_seconds": uptime,
        "start_time": _start_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get(
    "/health",
    summary="健康检查",
    description="返回服务运行状态、数据库连通性、版本号等信息。不加权限校验，可被监控直接调用。",
    response_description="健康状态数据",
)
def health_check() -> dict:
    """健康检查接口。

    数据库连不上时仍返回 HTTP 200（code=0），
    只是 db_status 为 "disconnected"。

    Returns:
        统一响应体，data 为 HealthData 结构。
    """
    # 检查数据库连通性（内部已捕获所有异常，不会抛出）
    db_connected, db_latency_ms = check_db_connection()

    # 获取运行时长信息
    uptime_info = get_uptime_info()

    # 当前服务器时间
    now = datetime.now(ZoneInfo(settings.TZ))
    server_time = now.strftime("%Y-%m-%d %H:%M:%S")

    # 构造响应数据
    health_data = HealthData(
        status="ok",
        version=settings.APP_VERSION,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        db_status="connected" if db_connected else "disconnected",
        db_latency_ms=db_latency_ms,
        uptime_seconds=uptime_info["uptime_seconds"],  # type: ignore[arg-type]
        start_time=uptime_info["start_time"],  # type: ignore[arg-type]
        server_time=server_time,
    )

    return success(health_data.model_dump())
