"""Excel 导入服务测试（阶段 3 CP4 交付物）。

覆盖 spec 4.13 + 验收项 21/22/23：
- 部分成功（第 3 行编码撞库、第 5 行名称空 → 其余成功）
- 批内重复（第 2/4 行同编码 → 两行都失败）
- 非 .xlsx → 2001
- 行数超上限 → 2001
"""

from __future__ import annotations

import io
from collections.abc import Generator

import pytest
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.services import product_service


@pytest.fixture(autouse=True)
def cleanup_imported_products() -> Generator[None, None, None]:
    """清理导入测试留下的商品与条码。"""
    yield
    s = SessionLocal()
    try:
        rows = s.execute(
            text("SELECT id FROM prd_product WHERE product_name LIKE '导入测试%'")
        ).fetchall()
        for (pid,) in rows:
            s.execute(text("DELETE FROM prd_barcode WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_price_history WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_product WHERE id=:p"), {"p": pid})
        s.execute(text("DELETE FROM prd_barcode WHERE barcode LIKE '8888%'"))
        s.commit()
    finally:
        s.close()


def _build_xlsx(rows: list[list]) -> bytes:
    """构造 xlsx 文件字节。第一行是表头，其余是数据。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["商品名称", "分类名称", "单位名称", "商品编码", "规格", "条码",
               "进价", "售价", "会员价", "是否称重", "保质期天数", "备注"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.integration
class TestImportBasic:
    """基础导入行为。"""

    def test_all_valid_rows_succeed(self, db_session: Session) -> None:
        """3 行全部合法 → success=3, failed=0。"""
        xlsx = _build_xlsx([
            ["导入测试 A", "叶菜类", "斤", "P888801", "500g", "8888000000001", 3, 5, 4.5, 0, None, ""],
            ["导入测试 B", "叶菜类", "斤", "P888802", "", "8888000000002", 3, 5, None, 0, None, ""],
            ["导入测试 C", "叶菜类", "斤", "P888803", "", "8888000000003", 3, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.total == 3
        assert result.success == 3
        assert result.failed == 0
        assert result.errors == []

    def test_auto_generate_code_when_empty(self, db_session: Session) -> None:
        """Excel 编码列为空 → 走 sys_no_seq 自动生成。"""
        xlsx = _build_xlsx([
            ["导入测试 D 自动编码", "叶菜类", "斤", "", "", "8888000000004", 3, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.success == 1
        # 验证生成的编码是 P + 6 位数字
        row = db_session.execute(
            text("SELECT product_code FROM prd_product WHERE product_name='导入测试 D 自动编码'")
        ).fetchone()
        assert row is not None
        code = row[0]
        assert code.startswith("P") and len(code) == 7 and code[1:].isdigit()


@pytest.mark.integration
class TestImportPartialSuccess:
    """spec 验收项 21：部分成功。"""

    def test_mixed_valid_and_invalid_rows(self, db_session: Session) -> None:
        """10 行：第 3 行编码撞库、第 5 行名称空 → success=8, failed=2。"""
        xlsx = _build_xlsx([
            ["导入测试 1", "叶菜类", "斤", "P888810", "", "8888000000010", 3, 5, None, 0, None, ""],
            ["导入测试 2", "叶菜类", "斤", "P888811", "", "8888000000011", 3, 5, None, 0, None, ""],
            ["导入测试 3 撞库", "叶菜类", "斤", "P000001", "", "8888000000012", 3, 5, None, 0, None, ""],  # 撞 P000001
            ["导入测试 4", "叶菜类", "斤", "P888813", "", "8888000000013", 3, 5, None, 0, None, ""],
            ["", "叶菜类", "斤", "P888814", "", "8888000000014", 3, 5, None, 0, None, ""],  # 名称空
            ["导入测试 6", "叶菜类", "斤", "P888815", "", "8888000000015", 3, 5, None, 0, None, ""],
            ["导入测试 7", "叶菜类", "斤", "P888816", "", "8888000000016", 3, 5, None, 0, None, ""],
            ["导入测试 8", "叶菜类", "斤", "P888817", "", "8888000000017", 3, 5, None, 0, None, ""],
            ["导入测试 9", "叶菜类", "斤", "P888818", "", "8888000000018", 3, 5, None, 0, None, ""],
            ["导入测试 10", "叶菜类", "斤", "P888819", "", "8888000000019", 3, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.total == 10
        assert result.success == 8
        assert result.failed == 2
        # errors 应含"第 3 行"和"第 5 行"
        error_text = " ".join(result.errors)
        assert "第 3 行" in error_text
        assert "第 5 行" in error_text
        # 8 条已落库
        db_count = db_session.execute(
            text("SELECT COUNT(*) FROM prd_product WHERE product_name LIKE '导入测试%' "
                 "AND product_code LIKE 'P8888%'")
        ).scalar_one()
        assert db_count == 8


@pytest.mark.integration
class TestImportBatchDuplicates:
    """spec 验收项 22：批内重复。"""

    def test_batch_duplicate_code_both_fail(self, db_session: Session) -> None:
        """第 2 行和第 4 行填同一编码 → 两行都失败。"""
        xlsx = _build_xlsx([
            ["导入测试 X1", "叶菜类", "斤", "P888820", "", "8888000000020", 3, 5, None, 0, None, ""],
            ["导入测试 X2 重复", "叶菜类", "斤", "P888821", "", "8888000000021", 3, 5, None, 0, None, ""],
            ["导入测试 X3", "叶菜类", "斤", "P888822", "", "8888000000022", 3, 5, None, 0, None, ""],
            ["导入测试 X4 重复", "叶菜类", "斤", "P888821", "", "8888000000023", 3, 5, None, 0, None, ""],  # 撞第 2 行
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.total == 4
        assert result.success == 2  # 只有 X1 和 X3 成功
        assert result.failed == 2
        # 第 2 和第 4 行都应在 errors 里，且理由是"批内重复"
        error_text = " ".join(result.errors)
        assert "第 2 行" in error_text
        assert "第 4 行" in error_text
        assert "本批次内重复" in error_text

    def test_batch_duplicate_barcode_both_fail(self, db_session: Session) -> None:
        """批内条码重复 → 两行都失败。"""
        xlsx = _build_xlsx([
            ["导入测试 Y1", "叶菜类", "斤", "P888830", "", "8888000000030", 3, 5, None, 0, None, ""],
            ["导入测试 Y2 条码重", "叶菜类", "斤", "P888831", "", "8888000000030", 3, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.failed == 2
        assert "本批次内重复" in " ".join(result.errors)


@pytest.mark.integration
class TestImportFileFormat:
    """spec 验收项 23：文件格式。"""

    def test_xls_rejected(self, db_session: Session) -> None:
        """`.xls` 文件 → 2001 + message 说明格式不支持。"""
        with pytest.raises(BusinessError) as exc:
            product_service.import_products(
                db_session, file_bytes=b"fake xls content",
                filename="legacy.xls", operator_id=1,
            )
        assert exc.value.code == ErrorCode.REQUIRED_MISSING
        assert ".xlsx" in exc.value.message

    def test_txt_rejected(self, db_session: Session) -> None:
        """`.txt` 文件 → 2001。"""
        with pytest.raises(BusinessError) as exc:
            product_service.import_products(
                db_session, file_bytes=b"not excel", filename="data.txt", operator_id=1,
            )
        assert exc.value.code == ErrorCode.REQUIRED_MISSING

    def test_invalid_xlsx_content_rejected(self, db_session: Session) -> None:
        """`.xlsx` 扩展名但内容不是合法 xlsx → 2001。"""
        with pytest.raises(BusinessError) as exc:
            product_service.import_products(
                db_session, file_bytes=b"not a real xlsx", filename="broken.xlsx", operator_id=1,
            )
        assert exc.value.code == ErrorCode.REQUIRED_MISSING


@pytest.mark.integration
class TestImportValidation:
    """每行校验规则（与 4.3 一致）。"""

    def test_sale_below_purchase_fails_that_row(self, db_session: Session) -> None:
        """售价<进价 → 该行失败，其他行仍成功。"""
        xlsx = _build_xlsx([
            ["导入测试 OK", "叶菜类", "斤", "P888840", "", "8888000000040", 3, 5, None, 0, None, ""],
            ["导入测试 低价", "叶菜类", "斤", "P888841", "", "8888000000041", 10, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.success == 1
        assert result.failed == 1
        assert "低于进价" in result.errors[0]

    def test_nonexistent_category_fails(self, db_session: Session) -> None:
        """分类名称不存在 → 该行失败。"""
        xlsx = _build_xlsx([
            ["导入测试 分类错", "不存在的分类", "斤", "P888850", "", "8888000000050", 3, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.failed == 1
        assert "不存在" in result.errors[0]

    def test_barcodes_split_by_comma_or_chinese_comma(self, db_session: Session) -> None:
        """条码列支持 `,` 或 `、` 分隔（一品多码）。"""
        xlsx = _build_xlsx([
            ["导入测试 多码", "叶菜类", "斤", "P888860", "",
             "8888000000060,8888000000061、8888000000062", 3, 5, None, 0, None, ""],
        ])
        result = product_service.import_products(
            db_session, file_bytes=xlsx, filename="test.xlsx", operator_id=1,
        )
        assert result.success == 1
        # 验证 3 个条码都已入库
        count = db_session.execute(
            text("SELECT COUNT(*) FROM prd_barcode WHERE barcode LIKE '8888000000060%' "
                 "OR barcode='8888000000061' OR barcode='8888000000062'")
        ).scalar_one()
        assert count == 3
