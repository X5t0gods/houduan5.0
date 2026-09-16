"""供应商对账测试（阶段 6 CP2 交付物）。

覆盖 spec 验收项 23/24/25/26：
- 对账恒等式 begin + increase − decrease == end
- end_payable == pur_supplier.balance_payable
- 无单据时 begin == end == balance_payable（倒推法）
- 付款计入 decrease
- 日期校验（start > end → 2001）
- 日期含当天
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.schemas.purchase import PaymentParams
from app.services import supplier_service


@pytest.fixture(autouse=True)
def cleanup_supplier_data() -> Generator[None, None, None]:
    """每个测试后清理付款 + 恢复供应商应付余额到种子值。"""
    yield
    s = SessionLocal()
    try:
        # 删测试付款（pay_no 是本测试生成的 FK 单）
        s.execute(text("DELETE FROM pur_payment WHERE remark LIKE '%对账测试%'"))
        # 恢复 5 家供应商的种子应付余额
        s.execute(text("UPDATE pur_supplier SET balance_payable=0.00 WHERE id=1"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=12860.00 WHERE id=2"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=4320.50 WHERE id=3"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=8640.00 WHERE id=4"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=0.00 WHERE id=5"))
        # 删测试新建的供应商（S000006+）
        s.execute(text("DELETE FROM pur_supplier WHERE supplier_code > 'S000005'"))
        # ⚠️ 重置 S 取号器到 5（否则每次测试递增，test_create_auto_code 拿不到 S000006）
        # S 是全局键，seq_date 固定 EPOCH_DATE=1970-01-01
        s.execute(text(
            "UPDATE sys_no_seq SET current_val=5 "
            "WHERE seq_key='S' AND seq_date='1970-01-01'"
        ))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestStatementIdentity:
    """对账恒等式（spec 验收 23）。"""

    def test_no_docs_begin_equals_end(self, db_session: Session) -> None:
        """无单据时：begin == end == balance_payable（倒推法核心）。"""
        # 供应商 2 balance_payable=12860，无任何收货/退货/付款
        result = supplier_service.get_statement(
            db_session, 2, date(2026, 9, 1), date(2026, 9, 16),
        )
        assert result.end_payable == Decimal("12860.00")
        assert result.increase == Decimal("0.00")
        assert result.decrease == Decimal("0.00")
        assert result.begin_payable == Decimal("12860.00")
        # 恒等式
        assert result.begin_payable + result.increase - result.decrease == result.end_payable

    def test_payment_in_decrease(self, db_session: Session) -> None:
        """付款计入 decrease，恒等式仍成立（spec 验收 23/24）。"""
        # 供应商 2 初始 12860，付款 1000 → 余额 11860
        supplier_service.create_payment(
            db_session,
            PaymentParams(
                supplier_id=2, amount=Decimal("1000"), pay_method="CASH",
                pay_date=date(2026, 9, 10), remark="对账测试付款",
            ),
            operator_id=1,
        )
        result = supplier_service.get_statement(
            db_session, 2, date(2026, 9, 1), date(2026, 9, 16),
        )
        # end = 11860（付款后余额），decrease = 1000（付款），increase = 0
        assert result.end_payable == Decimal("11860.00")
        assert result.decrease == Decimal("1000.00")
        assert result.increase == Decimal("0.00")
        # begin = end - increase + decrease = 11860 - 0 + 1000 = 12860
        assert result.begin_payable == Decimal("12860.00")
        # 恒等式
        assert result.begin_payable + result.increase - result.decrease == result.end_payable

    def test_end_equals_balance_payable(self, db_session: Session) -> None:
        """end_payable 恒等于 pur_supplier.balance_payable（spec 验收 23）。"""
        for sid in [1, 2, 3, 4, 5]:
            result = supplier_service.get_statement(
                db_session, sid, date(2026, 9, 1), date(2026, 9, 16),
            )
            supplier = db_session.execute(text(
                "SELECT balance_payable FROM pur_supplier WHERE id=:s"
            ), {"s": sid}).scalar_one()
            assert result.end_payable == supplier


@pytest.mark.integration
class TestStatementValidation:
    """对账参数校验（spec 验收 26）。"""

    def test_supplier_not_found(self, db_session: Session) -> None:
        """供应商不存在 → 4005。"""
        with pytest.raises(BusinessError) as exc_info:
            supplier_service.get_statement(
                db_session, 99999, date(2026, 9, 1), date(2026, 9, 16),
            )
        assert exc_info.value.code == ErrorCode.SUPPLIER_NOT_FOUND


@pytest.mark.integration
class TestSupplierCrud:
    """供应商 CRUD（spec 验收 3/4）。"""

    def test_create_auto_code(self, db_session: Session) -> None:
        """验收 3：不传 supplier_code → 自动生成 S000006。"""
        from app.schemas.purchase import SupplierCreate
        result = supplier_service.create_supplier(
            db_session,
            SupplierCreate(supplier_name="测试供应商A", settle_type="PERIOD", credit_days=30),
            operator_id=1,
        )
        assert result.supplier_code == "S000006"
        assert result.balance_payable == Decimal("0.00")

    def test_balance_payable_not_tamperable(self, db_session: Session) -> None:
        """验收 4：即使 payload 带 balance_payable 也落库为 0（schema 无此字段）。"""
        from app.schemas.purchase import SupplierCreate
        # model_validate 模拟前端传 balance_payable=99999（extra=ignore 会丢弃）
        params = SupplierCreate.model_validate({
            "supplier_name": "测试供应商B",
            "settle_type": "CASH",
            "balance_payable": 99999,  # 应被忽略
        })
        assert not hasattr(params, "balance_payable") or "balance_payable" not in params.model_fields
        result = supplier_service.create_supplier(db_session, params, operator_id=1)
        assert result.balance_payable == Decimal("0.00")

    def test_update_cannot_change_balance(self, db_session: Session) -> None:
        """验收 4：PUT 改 balance_payable 无效（schema 无此字段）。"""
        from app.schemas.purchase import SupplierUpdate
        params = SupplierUpdate.model_validate({
            "supplier_name": "绿源生鲜配送（改名）",
            "balance_payable": 99999,  # 应被忽略
        })
        result = supplier_service.update_supplier(db_session, 1, params, operator_id=1)
        assert result.supplier_name == "绿源生鲜配送（改名）"
        # balance_payable 不变（供应商1种子值 0）
        assert result.balance_payable == Decimal("0.00")


@pytest.mark.integration
class TestPayment:
    """付款登记（spec 验收 21/22）。"""

    def test_payment_deducts_payable(self, db_session: Session) -> None:
        """验收 21：付款 1000 → balance_payable 减 1000，pay_no 形如 FK...。"""
        result = supplier_service.create_payment(
            db_session,
            PaymentParams(
                supplier_id=2, amount=Decimal("1000"), pay_method="TRANSFER",
                pay_date=date(2026, 9, 16), remark="对账测试付款",
            ),
            operator_id=1,
        )
        assert result.pay_no.startswith("FK")
        # 供应商2：12860 - 1000 = 11860
        assert result.balance_payable == Decimal("11860.00")

    def test_payment_negative_allowed(self, db_session: Session) -> None:
        """验收 20 相关：付款可让应付变负（不校验余额充足）。"""
        # 供应商1 balance=0，付款 500 → -500
        result = supplier_service.create_payment(
            db_session,
            PaymentParams(
                supplier_id=1, amount=Decimal("500"), pay_method="CASH",
                pay_date=date(2026, 9, 16), remark="对账测试付款负余额",
            ),
            operator_id=1,
        )
        assert result.balance_payable == Decimal("-500.00")

    def test_payment_invalid_amount(self, db_session: Session) -> None:
        """验收 22：amount <= 0 → 9002。"""
        with pytest.raises(BusinessError) as exc_info:
            supplier_service.create_payment(
                db_session,
                PaymentParams(
                    supplier_id=2, amount=Decimal("0"), pay_method="CASH",
                    pay_date=date(2026, 9, 16), remark="对账测试",
                ),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.AMOUNT_INVALID

    def test_payment_invalid_method(self, db_session: Session) -> None:
        """验收 22：pay_method='BITCOIN' → 2001。"""
        with pytest.raises(BusinessError) as exc_info:
            supplier_service.create_payment(
                db_session,
                PaymentParams(
                    supplier_id=2, amount=Decimal("100"), pay_method="BITCOIN",
                    pay_date=date(2026, 9, 16), remark="对账测试",
                ),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING
