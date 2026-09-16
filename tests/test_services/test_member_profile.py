"""会员消费档案测试（阶段 7 CP2 交付物）。

覆盖 spec 验收项 15：
- avg_price = total_consume / consume_count
- consume_count=0 → avg_price=0（⛔不除零）
- favorite_categories / top_products 聚合（当前 sal_trade 无数据 → 空数组，不报错）
- 会员不存在 → 9004
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
from app.schemas.member import MemberCreate
from app.services import member_service


@pytest.fixture(autouse=True)
def cleanup_profile_data() -> Generator[None, None, None]:
    """清理测试会员 + 恢复种子。"""
    yield
    s = SessionLocal()
    try:
        rows = s.execute(text(
            "SELECT id FROM mem_member WHERE member_no LIKE 'M2026%'"
        )).fetchall()
        for (mid,) in rows:
            s.execute(text("DELETE FROM mem_balance_flow WHERE member_id=:m"), {"m": mid})
            s.execute(text("DELETE FROM mem_member WHERE id=:m"), {"m": mid})
        s.execute(text("DELETE FROM mem_balance_flow"))
        s.execute(text(
            "UPDATE mem_member SET balance=386.50, gift_balance=30.00, points=1286, "
            "total_consume=8642.30, consume_count=96, level_id=3, status=1 WHERE id=1"
        ))
        s.execute(text("DELETE FROM sys_no_seq WHERE seq_key IN ('M','CZ')"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestMemberProfile:
    """消费档案（spec 验收 15）。"""

    def test_avg_price_normal(self, db_session: Session) -> None:
        """会员1：avg_price = 8642.30 / 96 = 90.02。"""
        profile = member_service.get_member_profile(db_session, 1)
        assert profile.total_consume == Decimal("8642.30")
        assert profile.consume_count == 96
        assert profile.avg_price == Decimal("90.02")

    def test_avg_price_no_zero_division(self, db_session: Session) -> None:
        """验收 15：consume_count=0 → avg_price=0，不报 500。"""
        # 新建会员（consume_count=0）
        new_member = member_service.create_member(
            db_session,
            MemberCreate(phone="13900009999", real_name="档案测试"),
            operator_id=1,
        )
        profile = member_service.get_member_profile(db_session, new_member.id)
        assert profile.consume_count == 0
        assert profile.avg_price == Decimal("0.00")  # ⛔ 不除零

    def test_empty_arrays_no_error(self, db_session: Session) -> None:
        """sal_trade 无数据 → favorite_categories/top_products 空数组，不报错。"""
        profile = member_service.get_member_profile(db_session, 1)
        assert isinstance(profile.favorite_categories, list)
        assert isinstance(profile.top_products, list)
        # 当前无交易数据，应为空
        assert profile.favorite_categories == []
        assert profile.top_products == []

    def test_member_not_found_9004(self, db_session: Session) -> None:
        """会员不存在 → 9004。"""
        with pytest.raises(BusinessError) as exc_info:
            member_service.get_member_profile(db_session, 99999)
        assert exc_info.value.code == ErrorCode.MEMBER_NOT_FOUND
