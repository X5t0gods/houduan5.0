"""ProductService 集成测试（阶段 3 CP4 交付物）。

覆盖 spec 4.3-4.7 + 验收项 1/6/7/8/9/10/11/12：
- 校验规则（必填 / 分类存在 / 售价<进价 / 保质期依赖）
- 编码自动生成（P000026 递增）
- 条码 diff 更新（保留 created_at）
- 价格历史（三个价单独写、都没变不写）
- 删除保护（业务流水 → 2007；新建 → 成功且子表清理）
"""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.product import ProductCreate, ProductUpdate
from app.services import product_service


@pytest.fixture(autouse=True)
def cleanup_test_products() -> Generator[None, None, None]:
    """每个测试后清理测试商品（create_product 内部 commit，rollback 无法撤销）。

    匹配规则：product_name LIKE '%测试%' 或 '%待删%' 或 barcode LIKE '9999%'。
    ⚠️ 不会碰演示数据（P000001-P000020 名称不包含'测试'）。
    """
    yield
    s = SessionLocal()
    try:
        # 先找测试商品 id
        rows = s.execute(
            text("SELECT id FROM prd_product WHERE product_name LIKE '%测试%' "
                 "OR product_name LIKE '%待删%' OR product_name LIKE '%含额外字段%'")
        ).fetchall()
        for (pid,) in rows:
            s.execute(text("DELETE FROM prd_barcode WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_price_history WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_product_tag WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_product WHERE id=:p"), {"p": pid})
        # 清理测试条码（即使商品已删）
        s.execute(text("DELETE FROM prd_barcode WHERE barcode LIKE '9999%'"))
        s.commit()
    finally:
        s.close()


@pytest.mark.integration
class TestProductValidation:
    """spec 4.3 校验顺序。"""

    def _minimal_create_params(self, **overrides) -> ProductCreate:
        base = {
            "product_name": "测试商品",
            "category_id": 111,  # 叶菜类（三级）
            "unit_id": 1,
            "purchase_price": Decimal("5.00"),
            "sale_price": Decimal("8.00"),
        }
        base.update(overrides)
        return ProductCreate(**base)

    def test_missing_name_raises_2001(self, db_session: Session) -> None:
        """缺 product_name → 2001（Pydantic 层就会拦，但服务层兜底也测）。"""
        # Pydantic 已拦，跳过此路径，测试服务层直接调用
        params = self._minimal_create_params()
        params.product_name = ""  # 空字符串绕过 Pydantic min_length=1
        with pytest.raises(BusinessError) as exc:
            product_service.create_product(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.REQUIRED_MISSING
        db_session.rollback()

    def test_nonexistent_category_raises_2005(self, db_session: Session) -> None:
        """分类不存在 → 2005。"""
        params = self._minimal_create_params(category_id=99999)
        with pytest.raises(BusinessError) as exc:
            product_service.create_product(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.CATEGORY_INVALID
        db_session.rollback()

    def test_sale_below_purchase_raises_2003(self, db_session: Session) -> None:
        """⚠️ 售价 < 进价 → 2003（前端不拦，后端必须拦，spec 1.2 ①）。"""
        params = self._minimal_create_params(
            purchase_price=Decimal("10.00"), sale_price=Decimal("5.00"),
        )
        with pytest.raises(BusinessError) as exc:
            product_service.create_product(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.PRICE_BELOW_COST
        db_session.rollback()

    def test_perishable_missing_shelf_life_raises_2001(self, db_session: Session) -> None:
        """is_perishable=1 但 shelf_life_days 缺失 → 2001。"""
        params = self._minimal_create_params(is_perishable=1, shelf_life_days=None)
        with pytest.raises(BusinessError) as exc:
            product_service.create_product(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.REQUIRED_MISSING
        assert "保质期" in exc.value.message
        db_session.rollback()

    def test_duplicate_barcode_raises_2002(self, db_session: Session) -> None:
        """条码已存在 → 2002。"""
        params = self._minimal_create_params(barcodes=["6900000000141"])  # 商品 14 主条码
        with pytest.raises(BusinessError) as exc:
            product_service.create_product(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.PRODUCT_DUPLICATE
        db_session.rollback()

    def test_duplicate_product_code_raises_2002(self, db_session: Session) -> None:
        """商品编码已存在 → 2002。"""
        params = self._minimal_create_params(product_code="P000001")
        with pytest.raises(BusinessError) as exc:
            product_service.create_product(db_session, params, operator_id=1)
        assert exc.value.code == ErrorCode.PRODUCT_DUPLICATE
        db_session.rollback()


@pytest.mark.integration
class TestProductCreate:
    """spec 验收项 1：编码自动生成 P000026 递增。"""

    def test_auto_generate_product_code(self, db_session: Session) -> None:
        """不传 product_code 时自动生成，从 sys_no_seq.P.current_val+1 开始。

        ⚠️ 不能从 prd_product 表取 MAX(product_code)，因为前面测试可能已删除商品造成空洞，
        但 sys_no_seq.P 仍保持递增。应以 sys_no_seq 为准。
        """
        current_val = db_session.execute(
            text("SELECT current_val FROM sys_no_seq "
                 "WHERE seq_key='P' AND seq_date='1970-01-01'")
        ).scalar_one()

        params = ProductCreate(
            product_name="自动生成编码测试",
            category_id=111, unit_id=1,
            purchase_price=Decimal("5.00"), sale_price=Decimal("8.00"),
        )
        result = product_service.create_product(db_session, params, operator_id=1)
        expected_code = f"P{int(current_val) + 1:06d}"
        assert result.product_code == expected_code

    def test_init_stock_and_safe_stock_ignored(self, db_session: Session) -> None:
        """⚠️ spec 1.2 ②：init_stock / safe_stock 应被 Pydantic extra='ignore' 忽略。"""
        # 用 model_validate 模拟前端提交带额外字段的 payload
        raw_payload = {
            "product_name": "含额外字段测试",
            "category_id": 111, "unit_id": 1,
            "purchase_price": "5.00", "sale_price": "8.00",
            "init_stock": 100,       # 文档外字段
            "safe_stock": 10,        # 文档外字段
            "unknown_field": "xxx",  # 完全未知字段
        }
        params = ProductCreate.model_validate(raw_payload)
        # 断言未知字段没进入模型
        assert not hasattr(params, "init_stock")
        assert not hasattr(params, "safe_stock")
        assert not hasattr(params, "unknown_field")
        # 但正常字段能创建
        result = product_service.create_product(db_session, params, operator_id=1)
        assert result.product_name == "含额外字段测试"
        db_session.rollback()


@pytest.mark.integration
class TestProductUpdate:
    """spec 4.4 + 验收项 9/10：价格历史 + 条码 diff。"""

    def _create_test_product(self, db_session: Session) -> int:
        """创建一个测试商品，返回其 id。

        ⚠️ create_product 内部会 commit（正确行为），测试后由 cleanup_test_products fixture 清理。
        """
        params = ProductCreate(
            product_name="更新测试商品",
            category_id=111, unit_id=1,
            purchase_price=Decimal("5.00"), sale_price=Decimal("8.00"),
            barcodes=["9999000000001", "9999000000002"],
        )
        result = product_service.create_product(db_session, params, operator_id=1)
        return result.id

    def test_price_history_written_on_sale_price_change(self, db_session: Session) -> None:
        """改 sale_price → prd_price_history 新增 1 条 change_type='售价'。"""
        pid = self._create_test_product(db_session)
        before = db_session.execute(
            text("SELECT COUNT(*) FROM prd_price_history WHERE product_id=:p"), {"p": pid}
        ).scalar_one()

        product_service.update_product(
            db_session, pid,
            ProductUpdate(sale_price=Decimal("9.99")),
            operator_id=1,
        )
        after = db_session.execute(
            text("SELECT COUNT(*) FROM prd_price_history WHERE product_id=:p"), {"p": pid}
        ).scalar_one()
        assert after == before + 1

        # 验证记录内容
        row = db_session.execute(
            text("SELECT change_type, old_sale_price, new_sale_price "
                 "FROM prd_price_history WHERE product_id=:p ORDER BY id DESC LIMIT 1"),
            {"p": pid},
        ).fetchone()
        assert row[0] == "售价"
        assert Decimal(row[1]) == Decimal("8.00")
        assert Decimal(row[2]) == Decimal("9.99")
        db_session.rollback()

    def test_price_history_not_written_on_remark_only_change(self, db_session: Session) -> None:
        """⚠️ 只改 remark 不写价格历史（spec 4.4 关键：防止垃圾数据）。"""
        pid = self._create_test_product(db_session)
        before = db_session.execute(
            text("SELECT COUNT(*) FROM prd_price_history WHERE product_id=:p"), {"p": pid}
        ).scalar_one()

        product_service.update_product(
            db_session, pid, ProductUpdate(remark="只是备注"), operator_id=1,
        )
        after = db_session.execute(
            text("SELECT COUNT(*) FROM prd_price_history WHERE product_id=:p"), {"p": pid}
        ).scalar_one()
        assert after == before, "只改备注不应写价格历史"
        db_session.rollback()

    def test_three_price_changes_write_three_records(self, db_session: Session) -> None:
        """同时改三个价 → 写 3 条历史记录（每个价一条）。"""
        pid = self._create_test_product(db_session)
        before = db_session.execute(
            text("SELECT COUNT(*) FROM prd_price_history WHERE product_id=:p"), {"p": pid}
        ).scalar_one()

        product_service.update_product(
            db_session, pid,
            ProductUpdate(
                purchase_price=Decimal("6.00"),
                sale_price=Decimal("10.00"),
                member_price=Decimal("9.00"),  # 从无到有也算变更
            ),
            operator_id=1,
        )
        after = db_session.execute(
            text("SELECT COUNT(*) FROM prd_price_history WHERE product_id=:p"), {"p": pid}
        ).scalar_one()
        assert after == before + 3

        # 三条 change_type 应各不相同
        types = db_session.execute(
            text("SELECT change_type FROM prd_price_history WHERE product_id=:p "
                 "ORDER BY id DESC LIMIT 3"),
            {"p": pid},
        ).scalars().all()
        assert set(types) == {"进价", "售价", "会员价"}
        db_session.rollback()

    def test_barcode_diff_preserves_created_at(self, db_session: Session) -> None:
        """⚠️ spec 4.4 + 验收项 10：条码 diff 更新，保留的条码 created_at 不变。"""
        pid = self._create_test_product(db_session)
        # 记录原始条码的 created_at
        original = db_session.execute(
            text("SELECT barcode, created_at FROM prd_barcode WHERE product_id=:p "
                 "ORDER BY barcode"),
            {"p": pid},
        ).fetchall()
        original_map = {r[0]: r[1] for r in original}
        assert set(original_map.keys()) == {"9999000000001", "9999000000002"}

        # diff：删掉 9999000000002，加上 9999000000003，保留 9999000000001
        product_service.update_product(
            db_session, pid,
            ProductUpdate(barcodes=["9999000000001", "9999000000003"]),
            operator_id=1,
        )

        after = db_session.execute(
            text("SELECT barcode, created_at FROM prd_barcode WHERE product_id=:p "
                 "ORDER BY barcode"),
            {"p": pid},
        ).fetchall()
        after_map = {r[0]: r[1] for r in after}

        # 保留的 9999000000001 created_at 未变
        assert after_map["9999000000001"] == original_map["9999000000001"]
        # 9999000000002 已删
        assert "9999000000002" not in after_map
        # 9999000000003 是新加的
        assert "9999000000003" in after_map
        db_session.rollback()


@pytest.mark.integration
class TestProductDelete:
    """spec 4.6 + 验收项 11/12。"""

    def test_delete_demo_product_returns_2007(self, db_session: Session) -> None:
        """⚠️ 演示商品（有 inv_stock）删除 → 2007 + message 含具体表名。"""
        with pytest.raises(BusinessError) as exc:
            product_service.delete_product(db_session, 1, operator_id=1)
        assert exc.value.code == ErrorCode.PRODUCT_HAS_BUSINESS_FLOW
        # message 应含表名（便于诊断）
        assert "inv_stock" in exc.value.message or "业务流水" in exc.value.message
        db_session.rollback()

    def test_delete_nonexistent_returns_2004(self, db_session: Session) -> None:
        with pytest.raises(BusinessError) as exc:
            product_service.delete_product(db_session, 99999, operator_id=1)
        assert exc.value.code == ErrorCode.PRODUCT_NOT_FOUND
        db_session.rollback()

    def test_delete_new_product_succeeds_and_cleans_barcodes(self, db_session: Session) -> None:
        """⚠️ 新建商品（无业务流水）能删；子表 prd_barcode 也被清理。"""
        params = ProductCreate(
            product_name="待删测试商品",
            category_id=111, unit_id=1,
            purchase_price=Decimal("5"), sale_price=Decimal("8"),
            barcodes=["9999000099999"],
        )
        new_product = product_service.create_product(db_session, params, operator_id=1)
        pid = new_product.id

        # 验证条码已插入
        bc_count = db_session.execute(
            text("SELECT COUNT(*) FROM prd_barcode WHERE product_id=:p"), {"p": pid}
        ).scalar_one()
        assert bc_count == 1

        # 删除
        product_service.delete_product(db_session, pid, operator_id=1)

        # 商品与条码都没了
        p_count = db_session.execute(
            text("SELECT COUNT(*) FROM prd_product WHERE id=:p"), {"p": pid}
        ).scalar_one()
        bc_count_after = db_session.execute(
            text("SELECT COUNT(*) FROM prd_barcode WHERE product_id=:p"), {"p": pid}
        ).scalar_one()
        assert p_count == 0
        assert bc_count_after == 0, "子表 prd_barcode 未清理，会残留孤儿记录"
        db_session.rollback()


@pytest.mark.integration
class TestBarcodeLookup:
    """spec 4.7 + 验收项 20：条码查重语义。"""

    def test_nonexistent_barcode_returns_none(self, db_session: Session) -> None:
        """⚠️ 找不到时返回 None（路由层转 code=0 + data=null），不是 6001。"""
        result = product_service.get_by_barcode(db_session, "NONEXISTENT_XYZ")
        assert result is None

    def test_main_barcode_found(self, db_session: Session) -> None:
        """商品 14 主条码 6900000000141 能查到。"""
        result = product_service.get_by_barcode(db_session, "6900000000141")
        assert result is not None
        assert result.id == 14
        assert result.product_name == "可口可乐"

    def test_secondary_barcode_found(self, db_session: Session) -> None:
        """⚠️ 商品 14 附加条码 6900000000995 也能查到（不只主条码）。"""
        result = product_service.get_by_barcode(db_session, "6900000000995")
        assert result is not None
        assert result.id == 14
