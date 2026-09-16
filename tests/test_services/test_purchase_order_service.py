"""采购订单生命周期测试（阶段 6 CP3 交付物）。

覆盖 spec 验收项 27/28/29 + 状态机（4.1）：
- 建单 → DRAFT；PUT 成功；审核 false → 仍 DRAFT + reject_reason；审核 true → AUDITED；作废 → VOID
- ⛔ 对 AUDITED 订单调 PUT/void → 4004
- 订单列表含 supplier_name（中文名）+ created_by_name
- 订单详情 items[] 含 product_code/product_name/spec/unit_name（JOIN 出来的）
- 供应商停用 → 3001；订单不存在 → 4003
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
from app.schemas.purchase import (
    AuditRequest,
    PurchaseOrderCreate,
    PurchaseOrderItemIn,
    PurchaseOrderUpdate,
    VoidRequest,
)
from app.services import purchase_service


@pytest.fixture(autouse=True)
def cleanup_purchase_orders() -> Generator[None, None, None]:
    """每个测试后清理采购订单（pur_order 初始为空，任何订单都是测试产生的）。"""
    yield
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM pur_order_item"))
        s.execute(text("DELETE FROM pur_order"))
        # 重置 CG 取号器（按日键，删当天记录）
        s.execute(text("DELETE FROM sys_no_seq WHERE seq_key='CG'"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def _make_create_params(**overrides) -> PurchaseOrderCreate:
    base = {
        "supplier_id": 1,
        "store_id": 1,
        "items": [
            PurchaseOrderItemIn(product_id=1, quantity=Decimal("10"), unit_price=Decimal("4.50")),
        ],
        "remark": "TEST",
    }
    base.update(overrides)
    return PurchaseOrderCreate(**base)


@pytest.mark.integration
class TestOrderCreate:
    """建单（spec 5.3）。"""

    def test_create_draft(self, db_session: Session) -> None:
        """建单 → DRAFT，order_no 以 CG 开头，total_amount 正确。"""
        result = purchase_service.create_order(
            db_session, _make_create_params(), store_id=1, operator_id=1,
        )
        assert result.status == "DRAFT"
        assert result.order_no.startswith("CG")
        # 10 × 4.50 = 45.00
        assert result.total_amount == Decimal("45.00")
        assert result.source_type == "MANUAL"  # 缺省

    def test_create_tolerates_unknown_fields(self, db_session: Session) -> None:
        """前端多传 product_name/spec 等纯展示字段 → extra=ignore 容忍。"""
        params = PurchaseOrderCreate.model_validate({
            "supplier_id": 1, "store_id": 1,
            "items": [{
                "product_id": 1, "quantity": 5, "unit_price": 4.50,
                "product_name": "有机小番茄",  # 纯展示，应被忽略
                "spec": "500g", "unit_name": "斤",
            }],
        })
        result = purchase_service.create_order(db_session, params, store_id=1, operator_id=1)
        assert result.status == "DRAFT"

    def test_create_supplier_stopped_3001(self, db_session: Session) -> None:
        """供应商停用 → 3001。"""
        # 临时停用供应商 1
        db_session.execute(text("UPDATE pur_supplier SET status=0 WHERE id=1"))
        db_session.commit()
        try:
            with pytest.raises(BusinessError) as exc_info:
                purchase_service.create_order(
                    db_session, _make_create_params(), store_id=1, operator_id=1,
                )
            assert exc_info.value.code == ErrorCode.SUPPLIER_DISABLED
        finally:
            db_session.execute(text("UPDATE pur_supplier SET status=1 WHERE id=1"))
            db_session.commit()

    def test_create_supplier_not_found_4005(self, db_session: Session) -> None:
        """供应商不存在 → 4005。"""
        with pytest.raises(BusinessError) as exc_info:
            purchase_service.create_order(
                db_session, _make_create_params(supplier_id=99999), store_id=1, operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.SUPPLIER_NOT_FOUND


@pytest.mark.integration
class TestOrderStateMachine:
    """状态机（spec 验收 27 + 4.1）。"""

    def test_full_lifecycle(self, db_session: Session) -> None:
        """建单→PUT→审核false(仍DRAFT+reject)→审核true(AUDITED)→作废被拒4004。"""
        # 1. 建单 → DRAFT
        order = purchase_service.create_order(
            db_session, _make_create_params(), store_id=1, operator_id=1,
        )
        assert order.status == "DRAFT"

        # 2. PUT 成功（DRAFT 可改）
        updated = purchase_service.update_order(
            db_session, order.id,
            PurchaseOrderUpdate(remark="改备注", items=[
                PurchaseOrderItemIn(product_id=1, quantity=Decimal("20"), unit_price=Decimal("4.50")),
            ]),
            operator_id=1,
        )
        assert updated.total_amount == Decimal("90.00")  # 20×4.5

        # 3. 审核 approved=false → 仍 DRAFT + reject_reason
        purchase_service.audit_order(
            db_session, order.id, AuditRequest(approved=False, reason="价格偏高"), operator_id=1,
        )
        detail = purchase_service.get_order_detail(db_session, order.id)
        assert detail.status == "DRAFT"
        assert detail.reject_reason == "价格偏高"

        # 4. 审核 approved=true → AUDITED
        purchase_service.audit_order(
            db_session, order.id, AuditRequest(approved=True), operator_id=1,
        )
        detail = purchase_service.get_order_detail(db_session, order.id)
        assert detail.status == "AUDITED"

        # 5. AUDITED 后 PUT → 4004
        with pytest.raises(BusinessError) as exc_info:
            purchase_service.update_order(
                db_session, order.id, PurchaseOrderUpdate(remark="再改"), operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.PURCHASE_STATUS_FORBID

        # 6. AUDITED 后 void → 4004
        with pytest.raises(BusinessError) as exc_info:
            purchase_service.void_order(
                db_session, order.id, VoidRequest(reason="想作废"), operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.PURCHASE_STATUS_FORBID

    def test_void_draft(self, db_session: Session) -> None:
        """DRAFT 作废 → VOID。"""
        order = purchase_service.create_order(
            db_session, _make_create_params(), store_id=1, operator_id=1,
        )
        purchase_service.void_order(
            db_session, order.id, VoidRequest(reason="下错单"), operator_id=1,
        )
        detail = purchase_service.get_order_detail(db_session, order.id)
        assert detail.status == "VOID"

    def test_order_not_found_4003(self, db_session: Session) -> None:
        """订单不存在 → 4003。"""
        with pytest.raises(BusinessError) as exc_info:
            purchase_service.get_order_detail(db_session, 99999)
        assert exc_info.value.code == ErrorCode.PURCHASE_ORDER_NOT_FOUND


@pytest.mark.integration
class TestOrderQuery:
    """查询字段（spec 验收 28/29）。"""

    def test_list_has_supplier_and_creator_name(self, db_session: Session) -> None:
        """验收 28：列表含 supplier_name（中文）+ created_by_name。"""
        purchase_service.create_order(
            db_session, _make_create_params(supplier_id=2), store_id=1, operator_id=1,
        )
        orders, total = purchase_service.list_orders(db_session, page=1, page_size=10)
        assert total >= 1
        first = orders[0]
        assert first.supplier_name == "华联粮油批发"  # 供应商2中文名，不是ID
        assert first.created_by_name is not None

    def test_detail_items_have_product_info(self, db_session: Session) -> None:
        """验收 29：详情 items[] 含 product_code/name/spec/unit_name（JOIN 出来的）。"""
        order = purchase_service.create_order(
            db_session, _make_create_params(), store_id=1, operator_id=1,
        )
        detail = purchase_service.get_order_detail(db_session, order.id)
        assert detail.items is not None
        assert len(detail.items) == 1
        item = detail.items[0]
        # 表里无这些字段，必须 JOIN 出来
        assert item.product_code is not None
        assert item.product_name is not None
        assert item.unit_name is not None
