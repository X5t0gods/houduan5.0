"""分类路由（阶段 3 交付物）—— 4 个接口。

对应 docs/06 §3.2.8-3.2.11：
| # | 方法 | 路径 | 权限 |
| 8 | GET    | /categories     | goods:product:list |
| 9 | POST   | /categories     | goods:category |
| 10| PUT    | /categories/{id}| goods:category |
| 11| DELETE | /categories/{id}| goods:category |
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.permissions import Perm
from app.core.response import success
from app.deps import DbSession, require_perm
from app.schemas.product import Category, CategoryCreate, CategoryUpdate
from app.services import category_service

router = APIRouter(prefix="/categories", tags=["商品管理"])


@router.get(
    "",
    summary="获取分类树",
    description=(
        "返回**树形结构**（children 嵌套），最多三级，共 18 条演示数据。\n\n"
        "⚠️ 关键实现（spec 4.8）：一次性查全量在内存组装树，"
        "**不用递归 SQL 也不用每层一次查询**（N+1）。\n"
        "⚠️ 叶子节点 `children` 是空数组 `[]`（**不是 null**），前端 el-tree 依赖此约定。\n"
        "排序：按 sort 升序、id 升序。"
    ),
)
def list_categories(
    db: DbSession,
    _: Annotated[Any, Depends(require_perm(Perm.GOODS_PRODUCT_LIST))],
) -> dict[str, Any]:
    """获取分类树。

    Args:
        db: 数据库会话。

    Returns:
        统一响应体，data = list[Category]（树形）。
    """
    tree = category_service.get_category_tree(db)
    # Category 是自引用 Pydantic 模型，需 mode='json' 才能递归 dump
    return success([c.model_dump(mode="json") for c in tree])


@router.post(
    "",
    summary="新增分类",
    description=(
        "新增分类节点。**level 由后端自动计算**（parent_id=0 → level=1，否则 parent.level+1）。\n\n"
        "校验：\n"
        "- `category_code` 唯一 → 冲突返回 `2002`\n"
        "- `parent_id != 0` 时父分类必须存在 → 否则 `2005`\n"
        "- 层级超过 3 → `2005`（message 含'三级'）\n\n"
        "⚠️ 请求模型 `extra='ignore'`：容忍前端提交的 `children` 等未知字段（spec 1.2 ⑨）。"
    ),
)
def create_category(
    params: CategoryCreate,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_CATEGORY))],
) -> dict[str, Any]:
    """新增分类。

    Args:
        params: 分类创建参数。
        db: 数据库会话。
        current_user: 当前登录用户（require_perm 依赖注入的 UserContext）。

    Returns:
        统一响应体，data = 新建的 Category。
    """
    result = category_service.create_category(db, params, operator_id=current_user.user_id)
    return success(result.model_dump(mode="json"))


@router.put(
    "/{category_id}",
    summary="修改分类",
    description=(
        "修改分类字段（partial update）。\n\n"
        "⚠️ 关键校验（spec 4.9-4.11）：\n"
        "- 分类不存在 → `2005`\n"
        "- **禁止把父分类改成自己或自己的子孙**（会把树搞成环）→ `2005`\n"
        "- 移动后子树最深超过 3 级 → `2005`\n"
        "- 改 category_code 时校验唯一（排除自身）→ 冲突 `2002`\n\n"
        "⚠️ 前端 category.vue:112 会传整个 node（含 children），"
        "Pydantic `extra='ignore'` 已容忍（spec 1.2 ⑨）。\n"
        "⚠️ level 变化时**级联更新所有子孙的 level**。"
    ),
)
def update_category(
    category_id: int,
    params: CategoryUpdate,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_CATEGORY))],
) -> dict[str, Any]:
    """修改分类。

    Args:
        category_id: 分类 ID。
        params: 修改参数（partial）。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = 更新后的 Category。
    """
    result = category_service.update_category(db, category_id, params, current_user.user_id)
    return success(result.model_dump(mode="json"))


@router.delete(
    "/{category_id}",
    summary="删除分类",
    description=(
        "物理删除分类。**双校验**（spec 4.9-4.11）：\n\n"
        "- 分类不存在 → `2005`\n"
        "- 有子分类 → `2005`（message：'该分类下还有 N 个子分类，请先处理子分类'）\n"
        "- **被商品引用** → `2006`（message **必须带真实引用数**："
        "'该分类已被 N 个商品引用，只能停用不可删除'）\n\n"
        "⚠️ 前端 category.vue:111 的提示文案直接引用后端 message，"
        "所以引用数必须准确。"
    ),
)
def delete_category(
    category_id: int,
    db: DbSession,
    current_user: Annotated[Any, Depends(require_perm(Perm.GOODS_CATEGORY))],
) -> dict[str, Any]:
    """删除分类。

    Args:
        category_id: 分类 ID。
        db: 数据库会话。
        current_user: 当前登录用户。

    Returns:
        统一响应体，data = null。
    """
    category_service.delete_category(db, category_id, current_user.user_id)
    return success(None)


# 让 lint 知道 Category 是被间接使用的（作为 response 类型注解在文档中体现）
_ = Category
