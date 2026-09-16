"""盘点服务测试（阶段 5 CP2 交付物）。

覆盖 spec 验收项 12-18（本阶段核心）：
- 创建盘点冻结快照（book_qty == snapshot_qty == quantity）
- **增量调整**（验收 13，本阶段核心设计验证）：快照 42.5 → 期间卖到 40.0 → 实盘 41.0
  → diff=-1.5 → 审核后库存 = 38.5（⛔ 不是 41.0，覆盖法会算错）
- 负库存拒绝（验收 14）
- 5001 校验（验收 15）
- diff_rate 取绝对值（验收 16）
- 重复审核 5002（验收 17）
- 只写有差异的流水（验收 18）
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
from app.schemas.inventory import CheckAuditRequest, CheckCreateRequest, CheckItemInput
from app.services import check_service


@pytest.fixture(autouse=True)
def cleanup_check_data() -> Generator[None, None, None]:
    """每个测试后清理盘点数据 + 恢复库存。"""
    yield
    s = SessionLocal()
    try:
        # 清盘点明细与主单
        rows = s.execute(text("SELECT id FROM inv_check")).fetchall()
        for (cid,) in rows:
            s.execute(text("DELETE FROM inv_check_item WHERE check_id=:c"), {"c": cid})
        s.execute(text("DELETE FROM inv_check"))
        # 清盘点调整流水
        s.execute(text("DELETE FROM inv_stock_flow WHERE flow_type='CHECK_ADJUST'"))
        # 恢复商品库存到初始值（门店 1）
        s.execute(text("UPDATE inv_stock SET quantity=42.500 WHERE store_id=1 AND product_id=1"))
        s.execute(text("UPDATE inv_stock SET quantity=18.200 WHERE store_id=1 AND product_id=2"))
        s.execute(text("UPDATE inv_stock SET quantity=8.600 WHERE store_id=1 AND product_id=4"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def _create_all_check(session: Session) -> int:
    """创建一个 ALL 全盘盘点单，返回 check_id。"""
    params = CheckCreateRequest(check_type="ALL", remark="测试盘点")
    sheet = check_service.create_check(session, params, store_id=1, user_id=1)
    return sheet.id


def _find_item(session: Session, check_id: int, product_id: int):
    """找到盘点单里某商品的明细。"""
    from app.repositories import check_repository as repo
    items = repo.list_check_items(session, check_id)
    return next((it for it in items if it.product_id == product_id), None)


@pytest.mark.integration
class TestCheckSnapshot:
    """验收 12：创建盘点冻结快照。"""

    def test_create_all_freezes_20_items(self, db_session: Session) -> None:
        """ALL 全盘 → 20 条明细，book_qty == snapshot_qty == 当时 quantity。"""
        check_id = _create_all_check(db_session)
        from app.repositories import check_repository as repo
        items = repo.list_check_items(db_session, check_id)
        assert len(items) == 20
        # 商品 1：quantity=42.5
        p1 = next(it for it in items if it.product_id == 1)
        assert p1.book_qty == Decimal("42.500")
        assert p1.snapshot_qty == Decimal("42.500")
        assert p1.actual_qty is None
        assert p1.diff_qty is None

    def test_check_no_format(self, db_session: Session) -> None:
        """盘点单号 PD 前缀。"""
        check_id = _create_all_check(db_session)
        from app.repositories import check_repository as repo
        check = repo.get_check_by_id(db_session, check_id)
        assert check.check_no.startswith("PD")
        assert check.status == "DRAFT"


@pytest.mark.integration
class TestIncrementalAdjust:
    """验收 13：增量调整（本阶段核心设计验证）。"""

    def test_incremental_not_override(self, db_session: Session) -> None:
        """快照 42.5 → 期间卖到 40.0 → 实盘 41.0 → 审核后 38.5（⛔ 不是 41.0）。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        assert item.snapshot_qty == Decimal("42.500")

        # 模拟盘点期间卖出 2.5：手工把库存改到 40.0
        db_session.execute(text(
            "UPDATE inv_stock SET quantity=40.000 WHERE store_id=1 AND product_id=1"
        ))
        db_session.commit()

        # 录入实盘 41.0
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("41.0"), diff_reason="损耗"),
        ])

        # 提交 + 审核
        check_service.submit_check(db_session, check_id)
        check_service.audit_check(
            db_session, check_id, CheckAuditRequest(approved=True), auditor_id=1,
        )

        # 验证：diff = 41.0 - 42.5 = -1.5；审核后库存 = 40.0 + (-1.5) = 38.5
        final_qty = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()
        assert final_qty == Decimal("38.500"), f"增量调整错误：期望 38.5，实际 {final_qty}"

    def test_diff_qty_and_flow(self, db_session: Session) -> None:
        """diff_qty=-1.5，写 CHECK_ADJUST 流水 direction=-1。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        db_session.execute(text(
            "UPDATE inv_stock SET quantity=40.000 WHERE store_id=1 AND product_id=1"
        ))
        db_session.commit()
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("41.0"), diff_reason="损耗"),
        ])
        check_service.submit_check(db_session, check_id)
        check_service.audit_check(
            db_session, check_id, CheckAuditRequest(approved=True), auditor_id=1,
        )

        # diff_qty = -1.5
        item_after = _find_item(db_session, check_id, 1)
        assert item_after.diff_qty == Decimal("-1.500")

        # 流水 direction=-1（盘亏）
        flow = db_session.execute(text(
            "SELECT flow_type, direction, quantity FROM inv_stock_flow "
            "WHERE product_id=1 AND flow_type='CHECK_ADJUST' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert flow[0] == "CHECK_ADJUST"
        assert flow[1] == -1
        assert flow[2] == Decimal("1.500")  # quantity 是绝对值

    def test_only_diff_items_write_flow(self, db_session: Session) -> None:
        """验收 18：diff=0 的明细不写流水。只改商品1，其余19个 diff=0 → 只1条流水。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("45.0"), diff_reason="漏记"),
        ])
        check_service.submit_check(db_session, check_id)
        check_service.audit_check(
            db_session, check_id, CheckAuditRequest(approved=True), auditor_id=1,
        )
        # 只有商品 1 有差异 → 只 1 条 CHECK_ADJUST 流水
        count = db_session.execute(text(
            "SELECT COUNT(*) FROM inv_stock_flow WHERE flow_type='CHECK_ADJUST'"
        )).scalar_one()
        assert count == 1


@pytest.mark.integration
class TestNegativeStockReject:
    """验收 14：负库存被拒。"""

    def test_audit_rejects_negative_stock(self, db_session: Session) -> None:
        """快照 42.5 → 期间卖到 1 → 实盘 0 → diff=-42.5 → 审核后 1-42.5<0 → 拒绝。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        # 模拟盘点期间几乎卖光
        db_session.execute(text(
            "UPDATE inv_stock SET quantity=1.000 WHERE store_id=1 AND product_id=1"
        ))
        db_session.commit()
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("0"), diff_reason="损耗"),
        ])
        check_service.submit_check(db_session, check_id)

        # 审核应被拒绝（1 + (0-42.5) = -41.5 < 0）
        with pytest.raises(BusinessError) as exc_info:
            check_service.audit_check(
                db_session, check_id, CheckAuditRequest(approved=True), auditor_id=1,
            )
        assert exc_info.value.code == ErrorCode.DOC_ALREADY_AUDITED

        # 库存不变（仍是 1.0），状态不变（仍 SUBMITTED）
        db_session.rollback()  # 回滚审核事务
        qty = db_session.execute(text(
            "SELECT quantity FROM inv_stock WHERE store_id=1 AND product_id=1"
        )).scalar_one()
        assert qty == Decimal("1.000")


@pytest.mark.integration
class TestCheckValidation:
    """验收 15/16/17。"""

    def test_5001_missing_diff_reason(self, db_session: Session) -> None:
        """有差异但未填 diff_reason → 提交返回 5001，message 含商品。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        # 录入实盘但不填原因
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("45.0"), diff_reason=None),
        ])
        with pytest.raises(BusinessError) as exc_info:
            check_service.submit_check(db_session, check_id)
        assert exc_info.value.code == ErrorCode.CHECK_DIFF_REASON_MISSING
        assert "1" in exc_info.value.message  # message 含商品ID

    def test_diff_rate_absolute(self, db_session: Session) -> None:
        """验收 16：差异为负时 diff_rate 仍为正。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        # 实盘 40 < 快照 42.5 → diff=-2.5，diff_rate 应为正
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("40.0"), diff_reason="损耗"),
        ])
        item_after = _find_item(db_session, check_id, 1)
        assert item_after.diff_qty == Decimal("-2.500")
        assert item_after.diff_rate > 0  # 绝对值
        # 2.5 / 42.5 ≈ 0.0588
        assert item_after.diff_rate == Decimal("0.0588")

    def test_repeat_audit_5002(self, db_session: Session) -> None:
        """验收 17：已 AUDITED 再审核 → 5002。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("42.5"), diff_reason=None),
        ])
        check_service.submit_check(db_session, check_id)
        check_service.audit_check(
            db_session, check_id, CheckAuditRequest(approved=True), auditor_id=1,
        )
        # 再次审核 → 5002
        with pytest.raises(BusinessError) as exc_info:
            check_service.audit_check(
                db_session, check_id, CheckAuditRequest(approved=True), auditor_id=1,
            )
        assert exc_info.value.code == ErrorCode.DOC_ALREADY_AUDITED

    def test_reject_returns_to_draft(self, db_session: Session) -> None:
        """驳回（approved=false）→ 状态回 DRAFT。"""
        check_id = _create_all_check(db_session)
        item = _find_item(db_session, check_id, 1)
        check_service.update_check_items(db_session, check_id, [
            CheckItemInput(id=item.id, actual_qty=Decimal("42.5"), diff_reason=None),
        ])
        check_service.submit_check(db_session, check_id)
        check_service.audit_check(
            db_session, check_id, CheckAuditRequest(approved=False, remark="数据存疑"),
            auditor_id=1,
        )
        from app.repositories import check_repository as repo
        check = repo.get_check_by_id(db_session, check_id)
        assert check.status == "DRAFT"
