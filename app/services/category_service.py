"""分类业务编排（阶段 3 交付物）。

关键设计
--------
1. **树组装在内存里做**（spec 4.8）：一次性查全量 18 条，用字典索引 O(N) 组装。
   ⛔ 不用递归 SQL 也不用每层一次查询（N+1）——分类总量小，没必要优化。
2. **children 为空时返回 `[]` 而非 `null`**（前端 el-tree 依赖）。
3. **修改分类禁止把 parent 改成自己的后代**（会把树搞成环，spec 4.9-4.11）。
4. **删除前双校验**：有子分类 → 2005；被商品引用 → 2006（message 带真实引用数）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.models.prd_models import PrdCategory
from app.repositories import auth_repository
from app.repositories import category_repository as repo
from app.schemas.product import Category, CategoryCreate, CategoryUpdate

logger = get_logger(__name__)


# 最大层级（超过则拒绝）
MAX_CATEGORY_LEVEL: int = 3


def _to_schema(cat: PrdCategory, children: list[Category] | None = None) -> Category:
    """把 ORM 对象转成 Category schema（children 由外部装配）。"""
    return Category(
        id=cat.id,
        parent_id=cat.parent_id,
        category_code=cat.category_code,
        category_name=cat.category_name,
        level=cat.level,
        sort=cat.sort,
        status=cat.status,
        children=children or [],
        created_at=cat.created_at,
        updated_at=cat.updated_at,
    )


def get_category_tree(session: Session) -> list[Category]:
    """组装分类树（GET /categories）。

    算法：
    1. 一次性查全部 18 条（含停用，前端可能要展示）
    2. 用 dict 建立 id → Category 的索引
    3. 二次遍历，把每个节点挂到父节点的 children 里
    4. 返回 parent_id=0 的顶级节点列表

    ⚠️ 排序：repository 已按 parent_id + sort + id 排好；本函数遍历时保持顺序。

    Returns:
        顶级分类列表，每个含 children（叶子节点 children 为空数组）。
    """
    all_cats = repo.list_all_categories(session)

    # 一次遍历建立 id → Category（不含 children）
    nodes: dict[int, Category] = {cat.id: _to_schema(cat) for cat in all_cats}

    # 二次遍历挂 children
    roots: list[Category] = []
    for cat in all_cats:
        node = nodes[cat.id]
        if cat.parent_id == 0:
            roots.append(node)
        else:
            parent = nodes.get(cat.parent_id)
            if parent is not None:
                parent.children.append(node)
            # 若父节点不存在（脏数据），忽略该节点不挂到任何地方
            # 不抛异常，避免整棵树崩掉

    return roots


def create_category(session: Session, params: CategoryCreate, operator_id: int) -> Category:
    """新增分类（POST /categories）。

    校验顺序：
    1. category_code 唯一 → 冲突 2002
    2. parent_id != 0 时父分类必须存在 → 2005
    3. 计算 level = parent.level + 1（parent_id=0 时 level=1）
    4. level > 3 → 2005（message 含"三级"）

    Args:
        session: 数据库会话。
        params: 分类创建参数。
        operator_id: 当前登录用户 ID（写审计日志用）。

    Returns:
        新建的 Category（含 children=[]）。

    Raises:
        BusinessError: 2002 编码重复 / 2005 父分类不存在或层级超限。
    """
    # 1. 编码唯一
    if repo.get_category_by_code(session, params.category_code) is not None:
        raise BusinessError(
            ErrorCode.PRODUCT_DUPLICATE,
            f"分类编码 {params.category_code} 已存在",
        )

    # 2. 父分类校验 + level 计算
    if params.parent_id == 0:
        level = 1
    else:
        parent = repo.get_category_by_id(session, params.parent_id)
        if parent is None:
            raise BusinessError(
                ErrorCode.CATEGORY_INVALID,
                f"父分类 id={params.parent_id} 不存在",
            )
        level = parent.level + 1

    # 3. 层级限制
    if level > MAX_CATEGORY_LEVEL:
        raise BusinessError(
            ErrorCode.CATEGORY_INVALID,
            f"分类最多三级，无法在 level={level - 1} 的分类下再建子分类",
        )

    # 4. 插入
    try:
        cat = repo.insert_category(
            session,
            category_code=params.category_code,
            category_name=params.category_name,
            parent_id=params.parent_id,
            level=level,
            sort=params.sort,
            status=params.status,
        )
    except IntegrityError as e:
        session.rollback()
        # 兜底：并发场景下唯一索引冲突
        raise BusinessError(
            ErrorCode.PRODUCT_DUPLICATE,
            f"分类编码 {params.category_code} 已存在",
        ) from e

    # 5. 写审计日志
    _write_op_log(session, operator_id, "新增分类", cat.id, None, {
        "category_code": cat.category_code,
        "category_name": cat.category_name,
        "parent_id": cat.parent_id,
        "level": cat.level,
    })
    session.commit()
    logger.info(f"新增分类：id={cat.id}, code={cat.category_code}, name={cat.category_name}")

    return _to_schema(cat)


def update_category(
    session: Session, category_id: int, params: CategoryUpdate, operator_id: int,
) -> Category:
    """修改分类（PUT /categories/{id}）。

    校验：
    1. 分类必须存在 → 2005
    2. 若改 parent_id：新 parent 必须存在、不能是自己、不能是自己的后代（防环）
    3. 若改 category_code：唯一性校验（排除自身）
    4. level 需要重算（parent 变了或自身 level 变了都要级联更新子孙）

    ⚠️ 前端 category.vue:112 会传整个 node（含 children），Pydantic extra='ignore' 已容忍。

    Args:
        session: 数据库会话。
        category_id: 分类 ID。
        params: 修改参数（partial）。
        operator_id: 当前登录用户 ID。

    Returns:
        更新后的 Category。

    Raises:
        BusinessError: 2005（不存在/环引用/父分类不存在）、2002（编码重复）。
    """
    cat = repo.get_category_by_id(session, category_id)
    if cat is None:
        raise BusinessError(ErrorCode.CATEGORY_INVALID, f"分类 id={category_id} 不存在")

    before_snapshot = {
        "category_code": cat.category_code,
        "category_name": cat.category_name,
        "parent_id": cat.parent_id,
        "sort": cat.sort,
        "status": cat.status,
    }

    values: dict[str, Any] = {}

    # 编码唯一性（排除自身）
    if params.category_code is not None and params.category_code != cat.category_code:
        existing = repo.get_category_by_code(session, params.category_code)
        if existing is not None and existing.id != category_id:
            raise BusinessError(
                ErrorCode.PRODUCT_DUPLICATE,
                f"分类编码 {params.category_code} 已被 id={existing.id} 占用",
            )
        values["category_code"] = params.category_code

    if params.category_name is not None:
        values["category_name"] = params.category_name
    if params.sort is not None:
        values["sort"] = params.sort
    if params.status is not None:
        values["status"] = params.status

    # parent_id 变更需要严格校验（防环 + 重算 level）
    if params.parent_id is not None and params.parent_id != cat.parent_id:
        new_parent_id = params.parent_id

        # 不能把自己挂到自己下面
        if new_parent_id == category_id:
            raise BusinessError(ErrorCode.CATEGORY_INVALID, "父分类不能是自己")

        # 计算新 level
        if new_parent_id == 0:
            new_level = 1
        else:
            # 新父分类不能是自己的后代（否则形成环）
            descendants = repo.get_descendant_ids(session, category_id)
            if new_parent_id in descendants:
                raise BusinessError(
                    ErrorCode.CATEGORY_INVALID,
                    "父分类不能是自己的子孙分类（会形成环）",
                )
            new_parent = repo.get_category_by_id(session, new_parent_id)
            if new_parent is None:
                raise BusinessError(
                    ErrorCode.CATEGORY_INVALID,
                    f"父分类 id={new_parent_id} 不存在",
                )
            new_level = new_parent.level + 1

        # 层级限制：自身 + 子孙都不能超过 3 级
        # 计算自身子树的最大深度
        max_subtree_depth = _get_subtree_depth(session, category_id)
        if new_level + max_subtree_depth - 1 > MAX_CATEGORY_LEVEL:
            raise BusinessError(
                ErrorCode.CATEGORY_INVALID,
                f"移动后子树最深将达到 {new_level + max_subtree_depth - 1} 级，超过三级上限",
            )

        values["parent_id"] = new_parent_id
        values["level"] = new_level

    if not values:
        # 没有实际变更，直接返回当前状态
        return _to_schema(cat)

    repo.update_category_fields(session, category_id, values)

    # 若 level 变了，级联更新所有子孙的 level
    if "level" in values:
        _cascade_update_levels(session, category_id, values["level"])

    # 刷新对象获取最新值
    session.refresh(cat)

    _write_op_log(session, operator_id, "修改分类", category_id, before_snapshot, {
        **before_snapshot,
        **{k: v for k, v in values.items()},
    })
    session.commit()
    logger.info(f"修改分类：id={category_id}, 变更字段={list(values.keys())}")

    return _to_schema(cat)


def _get_subtree_depth(session: Session, category_id: int) -> int:
    """获取以 category_id 为根的子树最大深度（含自身，至少为 1）。"""
    depth = 1
    frontier = [category_id]
    while frontier:
        rows = session.execute(
            select(PrdCategory.id).where(PrdCategory.parent_id.in_(frontier))
        ).scalars().all()
        if not rows:
            break
        depth += 1
        frontier = list(rows)
    return depth


def _cascade_update_levels(session: Session, root_id: int, root_level: int) -> None:
    """级联更新某分类所有子孙的 level（BFS）。"""
    current_level = root_level
    frontier = [root_id]
    while frontier:
        current_level += 1
        children = session.execute(
            select(PrdCategory.id).where(PrdCategory.parent_id.in_(frontier))
        ).scalars().all()
        if not children:
            break
        session.execute(
            update(PrdCategory)
            .where(PrdCategory.id.in_(children))
            .values(level=current_level)
        )
        frontier = list(children)
    session.flush()


def delete_category(session: Session, category_id: int, operator_id: int) -> None:
    """删除分类（DELETE /categories/{id}）。

    校验顺序（spec 4.9-4.11）：
    1. 分类必须存在 → 2005
    2. 有子分类 → 2005（message 明确"请先处理子分类"）
    3. 被商品引用 → 2006（message **必须带真实引用数**，前端直接展示）

    Args:
        session: 数据库会话。
        category_id: 分类 ID。
        operator_id: 当前登录用户 ID。

    Raises:
        BusinessError: 2005 / 2006。
    """
    cat = repo.get_category_by_id(session, category_id)
    if cat is None:
        raise BusinessError(ErrorCode.CATEGORY_INVALID, f"分类 id={category_id} 不存在")

    # 有子分类
    children_count = repo.count_children(session, category_id)
    if children_count > 0:
        raise BusinessError(
            ErrorCode.CATEGORY_INVALID,
            f"该分类下还有 {children_count} 个子分类，请先处理子分类",
        )

    # 被商品引用
    ref_count = repo.count_products_referencing(session, category_id)
    if ref_count > 0:
        # ⚠️ message 必须带真实数字（前端 category.vue:111 直接展示）
        raise BusinessError(
            ErrorCode.CATEGORY_REFERENCED,
            f"该分类已被 {ref_count} 个商品引用，只能停用不可删除",
        )

    # 真删
    code = cat.category_code
    name = cat.category_name
    repo.delete_category_by_id(session, category_id)
    _write_op_log(session, operator_id, "删除分类", category_id,
                  {"category_code": code, "category_name": name}, None)
    session.commit()
    logger.info(f"删除分类：id={category_id}, code={code}, name={name}")


def _write_op_log(
    session: Session,
    user_id: int,
    action: str,
    target_id: int,
    before: dict | None,
    after: dict | None,
) -> None:
    """内部工具：写 sys_operation_log（module=商品，target_type=prd_category）。"""
    auth_repository.write_operation_log(
        session,
        user_id=user_id,
        user_name=None,  # 简化：只写 user_id，user_name 冗余字段留空
        module="商品",
        action=action,
        target_type="prd_category",
        target_id=target_id,
        before_value=before,
        after_value=after,
        result=1,
    )
