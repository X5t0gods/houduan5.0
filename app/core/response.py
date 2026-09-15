"""统一响应体构造器 - 固定 5 个字段，一个不多一个不少。

响应体结构（与 docs/06 第 1.3 节一致）：
{
    "code": 0,
    "message": "success",
    "data": {},
    "request_id": "uuid",
    "timestamp": "YYYY-MM-DD HH:mm:ss"
}

⛔ 文件流接口（导出/小票）不包 ApiResult，直接 StreamingResponse。
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.context import get_request_id
from app.core.error_codes import ErrorCode, get_error_message


def _now_str() -> str:
    """获取当前时间字符串（Asia/Shanghai 时区）。

    Returns:
        格式为 YYYY-MM-DD HH:mm:ss 的时间字符串。
    """
    return datetime.now(ZoneInfo(settings.TZ)).strftime("%Y-%m-%d %H:%M:%S")


def success(data: Any = None) -> dict[str, Any]:
    """构造成功响应体（code=0）。

    Args:
        data: 业务数据，无返回内容时为 None。

    Returns:
        包含 5 个固定字段的响应字典。
    """
    return {
        "code": ErrorCode.SUCCESS,
        "message": "success",
        "data": data,
        "request_id": get_request_id() or "-",
        "timestamp": _now_str(),
    }


def fail(
    code: int | ErrorCode,
    message: str | None = None,
    data: Any = None,
) -> dict[str, Any]:
    """构造失败响应体（code 非 0）。

    ⛔ 业务失败一律 HTTP 200 + 非 0 code，不要用 HTTP 状态码表达业务错误。

    Args:
        code: 业务错误码。
        message: 自定义错误信息，为 None 时自动取错误码对应文案。
        data: 可选的附加数据。

    Returns:
        包含 5 个固定字段的响应字典。
    """
    return {
        "code": int(code),
        "message": message or get_error_message(code),
        "data": data,
        "request_id": get_request_id() or "-",
        "timestamp": _now_str(),
    }
