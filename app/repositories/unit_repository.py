"""计量单位数据访问层（阶段 3 交付物）。

⚠️ 单位表数据量小（10 条），本阶段不做缓存，直接查库。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base_models import BaseUnit


def list_all_units(session: Session) -> list[BaseUnit]:
    """列出所有启用单位，按 sort 升序（GET /units 用）。"""
    return list(session.execute(
        select(BaseUnit)
        .where(BaseUnit.status == 1)
        .order_by(BaseUnit.sort.asc(), BaseUnit.id.asc())
    ).scalars().all())


def get_unit_by_id(session: Session, unit_id: int) -> BaseUnit | None:
    """按 ID 查单位（商品建档校验用）。"""
    return session.execute(
        select(BaseUnit).where(BaseUnit.id == unit_id)
    ).scalar_one_or_none()


def get_unit_by_name(session: Session, unit_name: str) -> BaseUnit | None:
    """按名称查单位（Excel 导入用）。"""
    return session.execute(
        select(BaseUnit).where(BaseUnit.unit_name == unit_name).limit(1)
    ).scalar_one_or_none()
