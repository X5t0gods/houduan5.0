"""分类树工具（阶段 7 交付物）—— 分类 → 自身及全部子孙的展开。

⚠️ 用途：促销列表的 `product_count`（spec 冲突表 ④）需要统计某促销覆盖的商品数：
- PRODUCT 类型：明细条数
- CATEGORY 类型：分类**及其子分类**下的商品数（⛔ 必须递归子分类）
- target_id=0（全场）：全部商品数

⚠️ 与阶段 4 的 `build_category_ancestor_map`（子→祖先，用于命中判定）**方向相反**：
   本工具是「分类→子孙」（用于统计覆盖商品数）。两者是同一套递归逻辑的两个方向。

⚠️ 复用阶段 3 的 `category_repository.get_descendant_ids`（BFS 子孙，不含自身）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.prd_models import PrdCategory, PrdProduct
from app.repositories import category_repository


def expand_with_descendants(session: Session, category_ids: list[int]) -> set[int]:
    """把分类 ID 列表展开为「自身 + 全部子孙」的集合。

    Args:
        session: 数据库会话。
        category_ids: 起始分类 ID 列表。

    Returns:
        含自身与所有子孙的分类 ID 集合。
    """
    result: set[int] = set()
    for cid in category_ids:
        result.add(cid)
        result |= category_repository.get_descendant_ids(session, cid)
    return result


def count_products_in_categories(session: Session, category_ids: list[int]) -> int:
    """统计「分类及其子分类」下的商品数（用于 CATEGORY 促销的 product_count）。"""
    if not category_ids:
        return 0
    all_cat_ids = expand_with_descendants(session, category_ids)
    if not all_cat_ids:
        return 0
    return int(session.execute(
        select(func.count(PrdProduct.id)).where(PrdProduct.category_id.in_(all_cat_ids))
    ).scalar_one())


def count_all_products(session: Session) -> int:
    """统计全部商品数（用于 target_id=0 全场促销的 product_count）。"""
    return int(session.execute(select(func.count(PrdProduct.id))).scalar_one())


def get_category_ids(session: Session) -> list[int]:
    """列出所有分类 ID（辅助）。"""
    return list(session.execute(select(PrdCategory.id)).scalars().all())
