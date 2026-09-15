"""通用依赖模块 - FastAPI 依赖注入函数。

本阶段提供：
- get_db: 数据库会话依赖（转发 app.core.database.get_db）
- PageParams: 分页参数依赖（从 Query 解析）

阶段 1/2 扩展点：
- current_user: 当前登录用户依赖（需要 JWT 解析）
- current_store: 当前门店依赖
- require_perm: 权限校验依赖
"""

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db as _get_db

# 数据库会话依赖（转发 core.database.get_db）
# 使用方式：def endpoint(db: DbSession = Depends(get_db))
get_db = _get_db

# 类型别名，便于路由函数签名简洁
DbSession = Annotated[Session, Depends(get_db)]


class PageParams:
    """分页参数依赖 - 从 Query String 解析分页参数。

    使用方式：
        def list_items(page: PageParams = Depends(PageParams)):
            offset = page.offset
            limit = page.page_size

    阶段 1/2 会在此基础上加 current_user 等依赖。

    Attributes:
        page: 页码，从 1 开始，默认 1。
        page_size: 每页条数，默认 20，最大 100。
        keyword: 模糊检索关键字。
        start_date: 起始日期 YYYY-MM-DD。
        end_date: 结束日期 YYYY-MM-DD。
        store_id: 门店 ID。
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="页码，从1开始")] = 1,
        page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
        keyword: Annotated[str | None, Query(description="模糊检索关键字")] = None,
        start_date: Annotated[str | None, Query(description="起始日期 YYYY-MM-DD")] = None,
        end_date: Annotated[str | None, Query(description="结束日期 YYYY-MM-DD")] = None,
        store_id: Annotated[int | None, Query(description="门店ID")] = None,
    ) -> None:
        """初始化分页参数。

        Args:
            page: 页码，从 1 开始。
            page_size: 每页条数，默认 20，最大 100。
            keyword: 模糊检索关键字。
            start_date: 起始日期。
            end_date: 结束日期。
            store_id: 门店 ID。
        """
        self.page = page
        self.page_size = page_size
        self.keyword = keyword
        self.start_date = start_date
        self.end_date = end_date
        self.store_id = store_id

    @property
    def offset(self) -> int:
        """计算 SQL OFFSET 值。

        Returns:
            偏移量 = (page - 1) * page_size
        """
        return (self.page - 1) * self.page_size
