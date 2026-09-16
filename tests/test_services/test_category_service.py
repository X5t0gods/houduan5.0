"""CategoryService 集成测试（阶段 3 CP4 交付物）。

覆盖 spec 4.8-4.11 与验收项 13-17：
- 树组装：18 条、5 顶级、叶菜类 level=3 children=[]
- 层级限制：三级下建子分类 → 2005
- 引用校验：删除被商品引用的分类 → 2006 + message 带真实数字
- 有子分类：删除有子分类的 → 2005
- 环检测：把 parent_id 改成自己的子孙 → 2005
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.product import CategoryCreate, CategoryUpdate
from app.services import category_service


@pytest.fixture(autouse=True)
def cleanup_test_categories() -> Generator[None, None, None]:
    """每个测试后清理新建的测试分类（create/update/delete 内部 commit，rollback 无法撤销）。

    匹配规则：category_code LIKE 'TEST%'。不会碰演示数据（C01/C02/C0101 等）。
    """
    yield
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM prd_category WHERE category_code LIKE 'TEST%'"))
        s.commit()
    finally:
        s.close()


@pytest.mark.integration
class TestCategoryTree:
    """验收项 13：分类树结构。"""

    def test_tree_has_5_top_level_nodes(self, db_session: Session) -> None:
        """顶级节点应有 5 个（生鲜果蔬/粮油调味/休闲食品/酒水饮料/日用百货）。"""
        tree = category_service.get_category_tree(db_session)
        assert len(tree) == 5
        top_names = [c.category_name for c in tree]
        assert "生鲜果蔬" in top_names

    def test_tree_total_18_nodes(self, db_session: Session) -> None:
        """整棵树应含 18 个节点（5 一级 + 9 二级 + 4 三级）。"""
        tree = category_service.get_category_tree(db_session)

        def count(nodes) -> int:
            return sum(1 + count(n.children) for n in nodes)

        assert count(tree) == 18

    def test_fresh_produce_has_3_children(self, db_session: Session) -> None:
        """「生鲜果蔬」下应有 3 个二级子分类（新鲜蔬菜/时令水果/肉禽蛋品）。"""
        tree = category_service.get_category_tree(db_session)
        fresh = next(c for c in tree if c.category_name == "生鲜果蔬")
        assert len(fresh.children) == 3
        child_names = {c.category_name for c in fresh.children}
        assert child_names == {"新鲜蔬菜", "时令水果", "肉禽蛋品"}

    def test_fresh_vegetable_has_2_leaves(self, db_session: Session) -> None:
        """「新鲜蔬菜」下应有 2 个三级子分类（叶菜类/茄果类）。"""
        tree = category_service.get_category_tree(db_session)
        fresh = next(c for c in tree if c.category_name == "生鲜果蔬")
        veg = next(c for c in fresh.children if c.category_name == "新鲜蔬菜")
        assert len(veg.children) == 2
        assert {c.category_name for c in veg.children} == {"叶菜类", "茄果类"}

    def test_leaf_children_is_empty_list_not_none(self, db_session: Session) -> None:
        """⚠️ 叶子节点的 children 必须是 [] 而非 None（前端 el-tree 依赖）。"""
        tree = category_service.get_category_tree(db_session)
        fresh = next(c for c in tree if c.category_name == "生鲜果蔬")
        veg = next(c for c in fresh.children if c.category_name == "新鲜蔬菜")
        leaf = next(c for c in veg.children if c.category_name == "叶菜类")
        assert leaf.children == []
        assert leaf.children is not None
        assert leaf.level == 3


@pytest.mark.integration
class TestCategoryLevelLimit:
    """验收项 14：层级限制。"""

    def test_create_4th_level_rejected(self, db_session: Session) -> None:
        """在三级分类下新建子分类 → 2005 + message 含'三级'。"""
        # 叶菜类 id=111 是三级
        params = CategoryCreate(
            category_code="TEST_4TH_LEVEL",
            category_name="测试四级",
            parent_id=111,
        )
        with pytest.raises(BusinessError) as exc:
            category_service.create_category(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        assert "三级" in exc.value.message
        db_session.rollback()

    def test_create_top_level_ok(self, db_session: Session) -> None:
        """parent_id=0 建一级分类 → 成功，level=1。"""
        params = CategoryCreate(
            category_code="TEST_TOP",
            category_name="测试顶级",
            parent_id=0,
        )
        result = category_service.create_category(db_session, params, operator_id=1)
        assert result.level == 1
        assert result.category_code == "TEST_TOP"
        db_session.rollback()

    def test_duplicate_code_rejected(self, db_session: Session) -> None:
        """编码重复 → 2002。"""
        params = CategoryCreate(
            category_code="C01",  # 已存在（生鲜果蔬）
            category_name="重复编码",
            parent_id=0,
        )
        with pytest.raises(BusinessError) as exc:
            category_service.create_category(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.PRODUCT_DUPLICATE
        db_session.rollback()


@pytest.mark.integration
class TestCategoryDelete:
    """验收项 15+16：删除校验。"""

    def test_delete_referenced_category_returns_2006_with_count(
        self, db_session: Session,
    ) -> None:
        """⚠️ 删除被商品引用的分类 → 2006 + message 必须带真实引用数。

        category_id=41 (饮料) 被 4 个商品引用（可口可乐/农夫山泉/雪碧/康师傅冰红茶）。
        """
        with pytest.raises(BusinessError) as exc:
            category_service.delete_category(db_session, 41, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_REFERENCED
        # ⚠️ message 必须含 "4 个商品"（前端直接展示这段文案）
        assert "4 个商品" in exc.value.message
        db_session.rollback()

    def test_delete_category_with_children_returns_2005(self, db_session: Session) -> None:
        """删除有子分类的 → 2005。"""
        # category_id=1 (生鲜果蔬) 有 3 个二级子分类
        with pytest.raises(BusinessError) as exc:
            category_service.delete_category(db_session, 1, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        assert "子分类" in exc.value.message
        db_session.rollback()

    def test_delete_nonexistent_returns_2005(self, db_session: Session) -> None:
        """删除不存在的分类 → 2005。"""
        with pytest.raises(BusinessError) as exc:
            category_service.delete_category(db_session, 99999, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        db_session.rollback()

    def test_delete_leaf_category_succeeds(self, db_session: Session) -> None:
        """删除无子无引用的分类 → 成功。"""
        # 先建一个测试分类
        params = CategoryCreate(
            category_code="TEST_DELETE_OK", category_name="待删除", parent_id=0,
        )
        new_cat = category_service.create_category(db_session, params, operator_id=1)
        # 再删掉
        category_service.delete_category(db_session, new_cat.id, operator_id=1)
        # 断言已不存在
        assert category_service.repo.get_category_by_id(db_session, new_cat.id) is None
        db_session.rollback()


@pytest.mark.integration
class TestCategoryCycleDetection:
    """验收项 17：环检测。"""

    def test_set_parent_to_own_descendant_rejected(self, db_session: Session) -> None:
        """⚠️ 把 category_id=1 (生鲜果蔬) 的 parent_id 改成 11 (新鲜蔬菜，它自己的儿子)
        → 2005，不能把树搞成环。
        """
        params = CategoryUpdate(parent_id=11)
        with pytest.raises(BusinessError) as exc:
            category_service.update_category(db_session, 1, params, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        assert "环" in exc.value.message or "子孙" in exc.value.message
        db_session.rollback()

    def test_set_parent_to_self_rejected(self, db_session: Session) -> None:
        """把 parent_id 改成自己 → 2005。"""
        params = CategoryUpdate(parent_id=1)
        with pytest.raises(BusinessError) as exc:
            category_service.update_category(db_session, 1, params, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        db_session.rollback()

    def test_set_parent_to_grandchild_rejected(self, db_session: Session) -> None:
        """把 category_id=1 的 parent 改成 111 (叶菜类，它的孙子) → 2005。"""
        params = CategoryUpdate(parent_id=111)
        with pytest.raises(BusinessError) as exc:
            category_service.update_category(db_session, 1, params, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        db_session.rollback()

    def test_move_deep_subtree_rejected_if_exceeds_3_levels(
        self, db_session: Session,
    ) -> None:
        """把有子树的分类移到二级下，导致子树超过 3 级 → 2005。

        构造：把 category_id=11 (新鲜蔬菜，有 2 个三级子分类) 移到某个二级分类下，
        则新的层级 = 3，子分类变 4 级，超限。
        """
        # 找一个二级分类作为新 parent，比如 21 (米面粮油)
        params = CategoryUpdate(parent_id=21)
        with pytest.raises(BusinessError) as exc:
            category_service.update_category(db_session, 11, params, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        assert "三级" in exc.value.message or "超过" in exc.value.message
        db_session.rollback()
