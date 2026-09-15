"""通用 Schema 模型 - ApiResult / PageResult / PageQuery。

与 docs/06 第 1.3、1.5 节严格对齐。
前端不做字段映射，字段名必须完全一致。
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResult(BaseModel, Generic[T]):
    """统一响应体模型 - 固定 5 个字段，一个不多一个不少。

    前端 request.ts 的响应拦截器逻辑：
    - code === 0 时直接取 data
    - code 非 0 时按错误码弹提示并 reject
    - HTTP 401 时清登录态跳登录页

    Attributes:
        code: 业务状态码，0 表示成功，非 0 为业务错误。
        message: 提示信息，成功时为 "success"。
        data: 业务数据，无返回内容时为 null。
        request_id: 请求唯一标识，用于日志追踪。
        timestamp: 服务端处理时间（YYYY-MM-DD HH:mm:ss）。
    """

    code: int = Field(default=0, description="业务状态码，0=成功")
    message: str = Field(default="success", description="提示信息")
    data: T | None = Field(default=None, description="业务数据")
    request_id: str = Field(default="-", description="请求唯一标识")
    timestamp: str = Field(default="", description="服务端处理时间")


class PageResult(BaseModel, Generic[T]):
    """分页响应结构。

    注意：total 必须是满足条件的总记录数，不是当前页条数。
    前端分页组件依赖 total 计算总页数，若返回当前页条数会导致分页失效。

    字段名说明：JSON 契约中字段名为 `list`（见 docs/06 第 1.5 节），但 `list` 与
    Python 内建名重名，在同类体内写 `list: list[T]` 会被Pydantic 解析成
    `FieldInfo[T]` 导致 TypeError。因此内部字段名为 `items`，通过 alias +
    serialization_alias 保证输入/输出 JSON 都仍是 `list`，前端零感知。

    Attributes:
        items (JSON: list): 当前页数据列表。
        total: 满足条件的总记录数。
        page: 当前页码。
        page_size: 每页条数。
    """

    # populate_by_name=True: 允许 PageResult(items=...) 与 PageResult(list=...) 两种写法
    # serialize_by_alias=True: model_dump() 默认输出 alias（JSON 仍为 `list`）
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    items: list[T] = Field(
        default_factory=list,
        alias="list",
        serialization_alias="list",
        description="当前页数据",
    )
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, description="当前页码")
    page_size: int = Field(default=20, description="每页条数")


class PageQuery(BaseModel):
    """分页查询基类 - 所有列表接口通用的请求参数。

    各接口可继承此类并扩展额外的过滤字段。

    Attributes:
        page: 页码，从 1 开始，默认 1。
        page_size: 每页条数，默认 20，可选 10/20/50/100。
        keyword: 模糊检索关键字（各接口语义不同）。
        start_date: 起始日期 YYYY-MM-DD（含）。
        end_date: 结束日期 YYYY-MM-DD（含）。
        store_id: 门店 ID，用于多门店数据隔离。
    """

    page: int = Field(default=1, ge=1, description="页码，从1开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数")
    keyword: str | None = Field(default=None, description="模糊检索关键字")
    start_date: str | None = Field(default=None, description="起始日期 YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="结束日期 YYYY-MM-DD")
    store_id: int | None = Field(default=None, description="门店ID")

    @property
    def offset(self) -> int:
        """计算 SQL OFFSET 值。

        Returns:
            偏移量 = (page - 1) * page_size
        """
        return (self.page - 1) * self.page_size


class HealthData(BaseModel):
    """健康检查接口响应数据。

    Attributes:
        status: 服务状态（ok）。
        version: 应用版本号。
        python_version: Python 运行时版本。
        db_status: 数据库连接状态（connected/disconnected）。
        db_latency_ms: 数据库连接延迟（毫秒）。
        uptime_seconds: 服务运行时长（秒）。
        start_time: 服务启动时间。
        server_time: 当前服务器时间。
    """

    status: str = Field(default="ok", description="服务状态")
    version: str = Field(default="", description="应用版本")
    python_version: str = Field(default="", description="Python版本")
    db_status: str = Field(default="disconnected", description="数据库状态")
    db_latency_ms: int = Field(default=0, description="数据库延迟(ms)")
    uptime_seconds: int = Field(default=0, description="运行时长(秒)")
    start_time: str = Field(default="", description="启动时间")
    server_time: str = Field(default="", description="服务器时间")
