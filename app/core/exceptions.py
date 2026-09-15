"""全局异常处理模块 - 统一业务错误通道。

核心约定（⛔ 不可更改）：
- 业务失败一律 HTTP 200 + 非 0 code
- HTTP 状态码只表达传输层/认证层：401 未登录、403 无权限、404 接口不存在、500 服务端异常
- 登录失败也必须返回 200 + 1002（不能返回 401，否则前端会清登录态把用户踢回登录页）
- 绝不要把原始异常堆栈返回给前端
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.context import get_request_id
from app.core.error_codes import ErrorCode, get_error_message
from app.core.logging import get_logger

logger = get_logger(__name__)


class BusinessError(Exception):
    """业务异常 - 所有可预期的业务错误都抛此异常。

    被全局异常处理器捕获后返回 HTTP 200 + 非 0 code。
    前端根据 code 显示对应的中文提示。

    Attributes:
        code: 业务错误码（ErrorCode 枚举值）。
        message: 错误提示信息，为空时自动取错误码对应文案。
        data: 可选的附加数据（默认 None）。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        data: Any = None,
    ) -> None:
        """初始化业务异常。

        Args:
            code: 业务错误码。
            message: 自定义错误信息，为 None 时自动取错误码对应文案。
            data: 可选的附加响应数据。
        """
        self.code = code
        self.message = message or get_error_message(code)
        self.data = data
        super().__init__(self.message)


def _build_response_body(
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """构建统一响应体（内部使用）。

    Args:
        code: 业务状态码。
        message: 提示信息。
        data: 业务数据。

    Returns:
        包含 5 个固定字段的响应字典。
    """
    now = datetime.now(ZoneInfo(settings.TZ))
    return {
        "code": code,
        "message": message,
        "data": data,
        "request_id": get_request_id() or "-",
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    """处理 BusinessError - 返回 HTTP 200 + 非 0 code。

    ⛔ 这是最关键的约定：业务错误必须 HTTP 200，不能用 4xx/5xx。
    前端 request.ts 只在 HTTP 401 时清登录态，若业务错误返回 401 会导致用户被踢。

    Args:
        request: 当前请求对象。
        exc: 业务异常实例。

    Returns:
        HTTP 200 响应，body 中 code 为非 0 错误码。
    """
    logger.warning(f"业务异常: code={exc.code}, message={exc.message}, path={request.url.path}")
    return JSONResponse(
        status_code=200,
        content=_build_response_body(exc.code, exc.message, exc.data),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理 Pydantic 参数校验失败 - 返回 HTTP 200 + code 2001。

    将首个出错字段与原因拼入 message，便于前端/开发者定位问题。

    Args:
        request: 当前请求对象。
        exc: 校验异常实例。

    Returns:
        HTTP 200 响应，code=2001。
    """
    errors = exc.errors()
    # 提取首个错误的字段路径与原因
    if errors:
        first = errors[0]
        field = ".".join(str(loc) for loc in first.get("loc", []))
        msg = first.get("msg", "参数校验失败")
        detail = f"{field}: {msg}" if field else msg
    else:
        detail = "请求参数校验失败"

    logger.warning(f"参数校验失败: {detail}, path={request.url.path}")
    return JSONResponse(
        status_code=200,
        content=_build_response_body(ErrorCode.REQUIRED_MISSING, detail),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """处理 HTTP 异常 - 保留原状态码，响应体仍用统一结构。

    适用于 401（未登录）、403（无权限）、404（接口不存在）、405（方法不允许）等。

    Args:
        request: 当前请求对象。
        exc: HTTP 异常实例。

    Returns:
        对应 HTTP 状态码的响应，body 含 code/message/request_id。

    ⚠️ HTTP 状态码 → 业务错误码的映射（阶段 2 spec 4.8 要求）：
    - **403 → 1040**（NO_PERMISSION）：前端 request.ts 已内置中文提示，
      但后端 body code 仍需与需求文档 1.4 节一致（1040）
    - 401 / 404 / 405 保持与 HTTP 状态码一致（前端主要看 HTTP 状态，不看 body code）
    """
    status_code = exc.status_code
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)

    # 403 → 业务错误码 1040（spec 4.8 明确要求）
    body_code = ErrorCode.NO_PERMISSION if status_code == 403 else status_code

    # 为常见状态码提供中文默认文案
    default_messages = {
        401: "未登录或登录已过期",
        403: "没有该操作的权限",
        404: "请求的接口不存在",
        405: "请求方法不允许",
    }
    if not message or message == "Not Found":
        message = default_messages.get(status_code, message)

    logger.info(f"HTTP 异常: status={status_code}, path={request.url.path}")
    return JSONResponse(
        status_code=status_code,
        content=_build_response_body(int(body_code), message),
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理 - 返回 HTTP 500 + 通用错误提示。

    ⛔ 绝不要把原始异常堆栈返回给前端（安全风险）。
    完整堆栈只记录到日志中。

    Args:
        request: 当前请求对象。
        exc: 未捕获的异常实例。

    Returns:
        HTTP 500 响应，message 为通用提示。
    """
    logger.exception(f"未捕获异常: path={request.url.path}, error={type(exc).__name__}")
    return JSONResponse(
        status_code=500,
        content=_build_response_body(500, "服务器异常，请稍后重试"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全部全局异常处理器到 FastAPI 应用。

    处理优先级：BusinessError > RequestValidationError > HTTPException > Exception

    Args:
        app: FastAPI 应用实例。
    """
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
