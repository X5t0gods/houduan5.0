"""统一响应体与序列化测试。

验证：
- 成功响应体结构（5 个固定字段）
- Decimal/时间序列化正确（Money 输出 number，DateTimeStr 输出 YYYY-MM-DD HH:mm:ss）
- 分页结构正确
"""

from datetime import datetime
from decimal import Decimal

from app.core.context import set_request_id
from app.core.response import fail, success
from app.schemas.base import BaseSchema, DateTimeStr, Money, Qty, Rate


class TestResponseBuilder:
    """测试响应体构造器。"""

    def test_success_response_structure(self) -> None:
        """成功响应体必须包含 5 个固定字段。"""
        set_request_id("test-request-id-123")
        result = success(data={"key": "value"})

        assert "code" in result
        assert "message" in result
        assert "data" in result
        assert "request_id" in result
        assert "timestamp" in result
        assert result["code"] == 0
        assert result["message"] == "success"
        assert result["data"] == {"key": "value"}
        assert result["request_id"] == "test-request-id-123"

    def test_fail_response_structure(self) -> None:
        """失败响应体 code 非 0，message 为错误文案。"""
        set_request_id("test-request-id-456")
        result = fail(code=2001, message="必填项缺失")

        assert result["code"] == 2001
        assert result["message"] == "必填项缺失"
        assert result["data"] is None
        assert result["request_id"] == "test-request-id-456"

    def test_fail_response_auto_message(self) -> None:
        """失败响应体未指定 message 时自动取错误码文案。"""
        result = fail(code=1002)
        assert result["message"] == "用户名或密码错误"


class TestSerialization:
    """测试 Pydantic 序列化行为（阶段 0 最容易被写错的地方）。"""

    def test_money_serializes_to_number(self) -> None:
        """Money 类型：Decimal("12.30") 序列化后必须是 number 12.3，不是字符串 "12.30"。

        这是必踩的坑：Pydantic v2 默认把 Decimal 序列化成字符串，
        前端类型是 number 且直接参与运算，字符串会导致 NaN。
        """

        class SampleModel(BaseSchema):
            price: Money

        model = SampleModel(price=Decimal("12.30"))
        dumped = model.model_dump(mode="json")

        # 必须是 float（number），不是 str
        assert isinstance(dumped["price"], float)
        assert dumped["price"] == 12.3

    def test_qty_serializes_to_number(self) -> None:
        """Qty 类型：Decimal("1.500") 序列化后必须是 number 1.5。"""

        class SampleModel(BaseSchema):
            quantity: Qty

        model = SampleModel(quantity=Decimal("1.500"))
        dumped = model.model_dump(mode="json")

        assert isinstance(dumped["quantity"], float)
        assert dumped["quantity"] == 1.5

    def test_datetime_serializes_to_formatted_string(self) -> None:
        """DateTimeStr 类型：datetime 序列化后必须是 YYYY-MM-DD HH:mm:ss，不是 ISO 带 T 格式。"""

        class SampleModel(BaseSchema):
            created_at: DateTimeStr

        dt = datetime(2026, 9, 15, 18, 30, 0)
        model = SampleModel(created_at=dt)
        dumped = model.model_dump(mode="json")

        assert dumped["created_at"] == "2026-09-15 18:30:00"
        # 确认不是 ISO 格式
        assert "T" not in dumped["created_at"]

    def test_rate_serializes_to_6_decimals(self) -> None:
        """Rate 类型（阶段 1 新增）：Decimal("1.234567") 序列化后不能被截断为 2 位。

        为什么需要 Rate：提升度 lift、置信度 confidence 等 BI 指标精度到 4-5 位，
        若误用 Money（2 位）会把 1.2345 截断成 1.23，直接影响论文实验数据。
        """

        class RateModel(BaseSchema):
            lift: Rate

        model = RateModel(lift=Decimal("1.234567"))
        dumped = model.model_dump(mode="json")

        # 必须是 float（number），不是 str
        assert isinstance(dumped["lift"], float)
        # 保留到 6 位（不被截断为 2 位）
        assert dumped["lift"] == 1.234567

    def test_rate_vs_money_precision_difference(self) -> None:
        """对照测试：同样的 Decimal 用 Money 会被截断，用 Rate 保留精度。"""

        class MoneyModel(BaseSchema):
            amount: Money

        class RateModel(BaseSchema):
            ratio: Rate

        value = Decimal("1.234567")
        assert MoneyModel(amount=value).model_dump(mode="json")["amount"] == 1.23
        assert RateModel(ratio=value).model_dump(mode="json")["ratio"] == 1.234567

    def test_rate_handles_small_values(self) -> None:
        """Rate 对小于 1 的值（如 support=0.00123）也保留精度。"""

        class RateModel(BaseSchema):
            support: Rate

        model = RateModel(support=Decimal("0.00123"))
        dumped = model.model_dump(mode="json")
        assert dumped["support"] == 0.00123


class TestPageResult:
    """测试分页结构。"""

    def test_page_result_fields(self) -> None:
        """PageResult 必须包含 list/total/page/page_size 四个字段。"""
        from app.schemas.common import PageResult

        page = PageResult(list=[{"id": 1}], total=100, page=1, page_size=20)
        dumped = page.model_dump()

        assert dumped["list"] == [{"id": 1}]
        assert dumped["total"] == 100  # total 是总记录数，不是当前页条数
        assert dumped["page"] == 1
        assert dumped["page_size"] == 20
