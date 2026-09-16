"""库存业务编排（阶段 5 交付物）。

CP1：库存查询（/inventory）、流水台账（/inventory/flows）、三类预警（/inventory/warnings）
CP2：流水导出（/inventory/flows/export）

⚠️ 门店过滤（spec 5.1）：库存按门店隔离，取当前登录用户的 store_id；
   admin（data_scope=ALL）看全部门店（store_id=None）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.repositories import inventory_repository as inv_repo
from app.repositories import stock_flow_repository as flow_repo
from app.schemas.inventory import StockFlow, StockItem, WarningsResult


def list_stock(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    stock_status: str | None = None,
    store_id: int | None = None,
) -> tuple[list[StockItem], int]:
    """实时库存查询（spec 5.1）。"""
    rows, total = inv_repo.list_stock(
        session,
        page=page,
        page_size=page_size,
        keyword=keyword,
        stock_status=stock_status,
        store_id=store_id,
    )
    return [StockItem(**r) for r in rows], total


def list_flows(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    flow_type: str | None = None,
    store_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[StockFlow], int]:
    """库存流水台账（spec 5.2）。"""
    rows, total = flow_repo.list_flows(
        session,
        page=page,
        page_size=page_size,
        keyword=keyword,
        flow_type=flow_type,
        store_id=store_id,
        start_date=start_date,
        end_date=end_date,
    )
    return [StockFlow(**r) for r in rows], total


def get_warnings(session: Session, store_id: int | None = None) -> WarningsResult:
    """三类预警（spec 5.4）。"""
    data = inv_repo.get_warnings(session, store_id)
    return WarningsResult(
        low_stock=[StockItem(**r) for r in data["low_stock"]],
        over_stock=[StockItem(**r) for r in data["over_stock"]],
        near_expiry=[StockItem(**r) for r in data["near_expiry"]],
    )
