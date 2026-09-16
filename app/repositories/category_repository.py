"""分类数据访问层（阶段 3 交付物）。

⚠️ 分层约束：只 flush 不 commit（决策 8），事务边界归 service 层。
⚠️ 分类总量小（18 条），**一次性查全量在内存组装树**，不做递归 SQL（spec 4.8）。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.prd_models import PrdCategory, PrdProduct


def list_all_categories(session: Session) -> list[PrdCategory]:
    """列出所有分类（含停用），按 parent_id + sort + id 排序。

    ⚠️ 一次性查全量（分类总量 ≤ 数百条），service 层在内存里组装树。
    """
    return list(session.execute(
        select(PrdCategory).order_by(
            PrdCategory.parent_id.asc(),
            PrdCategory.sort.asc(),
            PrdCategory.id.asc(),
        )
    ).scalars().all())


def get_category_by_id(session: Session, category_id: int) -> PrdCategory | None:
    """按 ID 查分类。"""
    return session.execute(
        select(PrdCategory).where(PrdCategory.id == category_id)
    ).scalar_one_or_none()


def get_category_by_code(session: Session, code: str) -> PrdCategory | None:
    """按 category_code 查分类（唯一性校验用）。"""
    return session.execute(
        select(PrdCategory).where(PrdCategory.category_code == code)
    ).scalar_one_or_none()


def get_category_by_name(session: Session, name: str) -> PrdCategory | None:
    """按 category_name 查分类（Excel 导入按名称匹配用）。

    ⚠️ category_name 没有唯一索引，理论上可能重名；返回第一条匹配的。
    """
    return session.execute(
        select(PrdCategory).where(PrdCategory.category_name == name).limit(1)
    ).scalar_one_or_none()


def count_children(session: Session, parent_id: int) -> int:
    """统计某分类下的直接子分类数量。"""
    return session.execute(
        select(func.count()).select_from(PrdCategory).where(PrdCategory.parent_id == parent_id)
    ).scalar_one()


def count_products_referencing(session: Session, category_id: int) -> int:
    """统计引用某分类的商品数量（删除前校验用）。

    ⚠️ 与 spec 4.9-4.11 一致：被引用时**只能停用不能删除**，返回 2006。
    message 里需要真实数字，因此本函数返回 int 而非 bool。
    """
    return session.execute(
        select(func.count()).select_from(PrdProduct).where(PrdProduct.category_id == category_id)
    ).scalar_one()


def get_descendant_ids(session: Session, category_id: int) -> set[int]:
    """获取某分类的**所有后代 ID**（含子、孙、曾孙...）。

    ⚠️ 用于修改分类时防止把 parent_id 改成自己的后代（会把树搞成环）。
    实现：BFS 遍历，一次查一层。分类总量小（≤ 数百），性能可接受。

    Args:
        session: 数据库会话。
        category_id: 起始分类 ID。

    Returns:
        所有后代 ID 的集合（不含自身）。
    """
    descendants: set[int] = set()
    frontier = [category_id]
    while frontier:
        rows = session.execute(
            select(PrdCategory.id).where(PrdCategory.parent_id.in_(frontier))
        ).scalars().all()
        new_ids = [r for r in rows if r not in descendants]
        if not new_ids:
            break
        descendants.update(new_ids)
        frontier = new_ids
    return descendants


def insert_category(
    session: Session,
    *,
    category_code: str,
    category_name: str,
    parent_id: int,
    level: int,
    sort: int = 0,
    status: int = 1,
) -> PrdCategory:
    """插入一条分类记录。

    ⚠️ level 由 service 层根据 parent 计算后传入，repository 不做业务判断。
    """
    category = PrdCategory(
        category_code=category_code,
        category_name=category_name,
        parent_id=parent_id,
        level=level,
        sort=sort,
        status=status,
    )
    session.add(category)
    session.flush()  # 拿到 category.id
    return category


def update_category_fields(
    session: Session, category_id: int, values: dict,
) -> None:
    """更新分类字段（只更新传入的键）。"""
    if not values:
        return
    session.execute(
        update(PrdCategory).where(PrdCategory.id == category_id).values(**values)
    )
    session.flush()


def delete_category_by_id(session: Session, category_id: int) -> None:
    """物理删除一条分类。

    ⚠️ 调用方必须先校验：无子分类 + 无商品引用（否则会抛外键错误 500）。
    """
    session.execute(delete(PrdCategory).where(PrdCategory.id == category_id))
    session.flush()
