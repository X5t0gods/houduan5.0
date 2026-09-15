"""请求上下文模块 - 基于 contextvars 实现请求级隔离。

中间件写入 request_id，响应构造器与日志过滤器读取。
使用 contextvars 保证在同步线程池执行时也能正确隔离。
"""

from contextvars import ContextVar

# 当前请求的 request_id，未初始化时为 None（不抛错）
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    """设置当前请求的 request_id。

    Args:
        request_id: 请求唯一标识（UUID4 字符串）。
    """
    _request_id_var.set(request_id)


def get_request_id() -> str | None:
    """获取当前请求的 request_id。

    Returns:
        request_id 字符串，未初始化时返回 None。
    """
    return _request_id_var.get()


def clear_request_id() -> None:
    """清除当前请求的 request_id（请求结束时调用，避免串号）。"""
    _request_id_var.set(None)
