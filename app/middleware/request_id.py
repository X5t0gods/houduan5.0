"""Request ID 中间件 - 为每个请求生成唯一标识，实现全链路追踪。

功能：
- 每个请求生成 uuid4 作为 request_id
- 若请求头带 X-Request-Id 则沿用（便于前后端联调追踪）
- 写入 contextvars（供响应构造器与日志过滤器读取）
- 在响应头回写 X-Request-Id
- try/finally 保证上下文清理，避免请求间串号
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import clear_request_id, set_request_id

# 请求头/响应头中的 request_id 字段名
REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Request ID 中间件。

    确保每个请求都有唯一的 request_id，贯穿日志、响应体和响应头。
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """处理请求：注入 request_id 到上下文，并在响应头回写。

        Args:
            request: 当前请求对象。
            call_next: 下一个处理器。

        Returns:
            带有 X-Request-Id 响应头的响应对象。
        """
        # 优先使用请求头中的 X-Request-Id（便于联调追踪），否则生成新的 uuid4
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # 写入上下文变量（供响应构造器与日志过滤器读取）
        set_request_id(request_id)

        try:
            response = await call_next(request)
            # 在响应头回写 request_id，前端/运维可据此追踪
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            # 清理上下文，避免线程复用时串号
            clear_request_id()
