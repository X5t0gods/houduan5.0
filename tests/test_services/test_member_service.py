"""会员服务测试（阶段 7 CP1 交付物）。

覆盖 spec 验收项 1/2/3/4/5/6/38（CP1 部分）：
- 会员列表 5 条，含 level_name（中文）/discount_rate(number)/sleeping_days
- 后4位检索 8866 → 张秀兰
- 手机号查重 9001、卡号自动生成 M...
- 建档充值 init_balance → RECHARGE 流水
- 修改保护：balance/points 不可改
- 等级 discount_rate ∈ (0,1] 校验
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
from app.schemas.member import MemberCreate, MemberLevelUpdate, MemberUpdate
from app.services import member_service


@pytest.fixture(autouse=True)
def cleanup_member_data() -> Generator[None, None, None]:
    """每个测试后恢复 5 会员种子值 + 删测试会员 + 清流水。"""
    yield
    s = SessionLocal()
    try:
        # 删测试新建会员（今天 M2026 开头；demo 是 M2025）
        rows = s.execute(text(
            "SELECT id FROM mem_member WHERE member_no LIKE 'M2026%'"
        )).fetchall()
        for (mid,) in rows:
            s.execute(text("DELETE FROM mem_balance_flow WHERE member_id=:m"), {"m": mid})
            s.execute(text("DELETE FROM mem_point_flow WHERE member_id=:m"), {"m": mid})
            s.execute(text("DELETE FROM mem_member_tag WHERE member_id=:m"), {"m": mid})
            s.execute(text("DELETE FROM mem_member WHERE id=:m"), {"m": mid})
        # 清测试产生的流水（demo 会员的）
        s.execute(text("DELETE FROM mem_balance_flow"))
        s.execute(text("DELETE FROM mem_point_flow"))
        # 恢复 5 会员种子值
        s.execute(text(
            "UPDATE mem_member SET balance=386.50, gift_balance=30.00, points=1286, "
            "total_consume=8642.30, consume_count=96, level_id=3, status=1, "
            "real_name='张秀兰', phone='13812348866', "
            "last_consume_at='2026-09-13 18:20:00' WHERE id=1"
        ))
        s.execute(text(
            "UPDATE mem_member SET balance=128.00, gift_balance=0.00, points=542, "
            "total_consume=3218.60, consume_count=47, level_id=2, status=1 WHERE id=2"
        ))
        s.execute(text(
            "UPDATE mem_member SET balance=0.00, gift_balance=0.00, points=318, "
            "total_consume=2145.80, consume_count=33, level_id=2, status=1 WHERE id=3"
        ))
        s.execute(text(
            "UPDATE mem_member SET balance=50.00, gift_balance=10.00, points=96, "
            "total_consume=486.20, consume_count=12, level_id=1, status=1 WHERE id=4"
        ))
        s.execute(text(
            "UPDATE mem_member SET balance=200.00, gift_balance=20.00, points=412, "
            "total_consume=1268.40, consume_count=25, level_id=1, status=1 WHERE id=5"
        ))
        # 恢复等级种子值
        s.execute(text("UPDATE mem_level SET discount_rate=1.0000 WHERE id=1"))
        s.execute(text("UPDATE mem_level SET discount_rate=0.9800 WHERE id=2"))
        s.execute(text("UPDATE mem_level SET discount_rate=0.9500 WHERE id=3"))
        # 重置 M 取号器（按日键，删当天）
        s.execute(text("DELETE FROM sys_no_seq WHERE seq_key='M'"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestMemberList:
    """会员列表与检索（验收 1/2）。"""

    def test_list_5_members_with_level(self, db_session: Session) -> None:
        """验收 1：5 条，含 level_name（中文）/discount_rate/sleeping_days。"""
        members, total = member_service.list_members(db_session, page=1, page_size=20)
        assert total == 5
        m1 = next(m for m in members if m.id == 1)
        assert m1.level_name == "金卡会员"
        assert m1.discount_rate == Decimal("0.9500")
        assert m1.real_name == "张秀兰"
        # sleeping_days：last_consume_at=2026-09-13，今天2026-09-16 → 3天
        assert m1.sleeping_days == 3

    def test_search_by_phone_suffix(self, db_session: Session) -> None:
        """验收 2：后4位 8866 → 张秀兰。"""
        results = member_service.search_members(db_session, "8866")
        assert len(results) >= 1
        assert any(m.real_name == "张秀兰" for m in results)

    def test_search_returns_discount_rate(self, db_session: Session) -> None:
        """检索结果含 discount_rate（收银台算会员折扣用）。"""
        results = member_service.search_members(db_session, "8866")
        zhang = next(m for m in results if m.real_name == "张秀兰")
        assert zhang.discount_rate == Decimal("0.9500")


@pytest.mark.integration
class TestMemberCreate:
    """会员建档（验收 3/4/5）。"""

    def test_phone_duplicate_9001(self, db_session: Session) -> None:
        """验收 3：已存在手机号 → 9001。"""
        with pytest.raises(BusinessError) as exc_info:
            member_service.create_member(
                db_session,
                MemberCreate(phone="13812348866", real_name="重复"),  # 张秀兰的号
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.PHONE_ALREADY_MEMBER

    def test_member_no_auto_generated(self, db_session: Session) -> None:
        """验收 4：member_no 形如 M202609160001。"""
        result = member_service.create_member(
            db_session,
            MemberCreate(phone="13900001111", real_name="测试新客"),
            operator_id=1,
        )
        assert result.member_no.startswith("M2026")
        assert len(result.member_no) == 13  # M + 8日期 + 4流水

    def test_create_with_init_balance(self, db_session: Session) -> None:
        """验收 5：init_balance=100 → balance=100 且有 RECHARGE 流水。"""
        result = member_service.create_member(
            db_session,
            MemberCreate(phone="13900002222", real_name="充值新客", init_balance=Decimal("100")),
            operator_id=1,
        )
        assert result.balance == Decimal("100.00")
        assert result.total_consume == Decimal("0.00")  # 充值不算消费
        # RECHARGE 流水
        flow = db_session.execute(text(
            "SELECT flow_type, amount FROM mem_balance_flow WHERE member_id=:m"
        ), {"m": result.id}).fetchone()
        assert flow[0] == "RECHARGE"
        assert flow[1] == Decimal("100.00")

    def test_new_member_lowest_level(self, db_session: Session) -> None:
        """新会员恒最低等级（普通会员 level_id=1）。"""
        result = member_service.create_member(
            db_session,
            MemberCreate(phone="13900003333", real_name="等级测试"),
            operator_id=1,
        )
        assert result.level_id == 1


@pytest.mark.integration
class TestMemberUpdate:
    """修改保护（验收 6）。"""

    def test_update_cannot_change_balance(self, db_session: Session) -> None:
        """验收 6：PUT 传 balance/points → 落库不变（schema 无这些字段）。"""
        # model_validate 模拟前端传 balance/points（extra=ignore 丢弃）
        params = MemberUpdate.model_validate({
            "real_name": "张秀兰改名",
            "balance": 99999,
            "points": 99999,
        })
        assert not hasattr(params, "balance")
        assert not hasattr(params, "points")
        result = member_service.update_member(db_session, 1, params, operator_id=1)
        assert result.real_name == "张秀兰改名"
        # balance/points 不变（种子值）
        assert result.balance == Decimal("386.50")
        assert result.points == 1286

    def test_member_not_found_9004(self, db_session: Session) -> None:
        """会员不存在 → 9004。"""
        with pytest.raises(BusinessError) as exc_info:
            member_service.get_member_detail(db_session, 99999)
        assert exc_info.value.code == ErrorCode.MEMBER_NOT_FOUND


@pytest.mark.integration
class TestMemberLevel:
    """等级（验收 + spec 5.7）。"""

    def test_list_levels_sorted(self, db_session: Session) -> None:
        """等级列表按 sort 升序，3 条。"""
        levels = member_service.list_levels(db_session)
        assert len(levels) == 3
        assert levels[0].level_name == "普通会员"
        # discount_rate 是 Decimal（Rate 别名）
        assert levels[2].discount_rate == Decimal("0.9500")

    def test_update_level_discount_rate_gt_1_rejected(self, db_session: Session) -> None:
        """discount_rate > 1 → 拒绝。"""
        with pytest.raises(BusinessError) as exc_info:
            member_service.update_level(
                db_session, 1,
                MemberLevelUpdate(discount_rate=Decimal("1.5")),
                operator_id=1,
            )
        assert exc_info.value.code == ErrorCode.REQUIRED_MISSING

    def test_update_level_discount_rate_zero_rejected(self, db_session: Session) -> None:
        """discount_rate <= 0 → 拒绝。"""
        with pytest.raises(BusinessError):
            member_service.update_level(
                db_session, 1,
                MemberLevelUpdate(discount_rate=Decimal("0")),
                operator_id=1,
            )

    def test_update_level_valid(self, db_session: Session) -> None:
        """discount_rate=0.9 合法 → 成功。"""
        result = member_service.update_level(
            db_session, 1,
            MemberLevelUpdate(discount_rate=Decimal("0.90")),
            operator_id=1,
        )
        assert result.discount_rate == Decimal("0.9000")
