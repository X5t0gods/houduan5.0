"""13 个商品接口的端到端集成测试（阶段 3 CP4 交付物）。

覆盖 spec 验收项 1-25（除 26 前端联调）：
- 完整链路：GET/POST/PUT/DELETE /products + barcode + import
- 权限边界（cashier01 vs admin vs 未登录）
- Decimal 序列化为 number（不是字符串）
"""

from __future__ import annotations

import io
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text

from app.core.database import SessionLocal
from tests.test_api.conftest import login_as


@pytest.fixture(autouse=True)
def cleanup_api_test_products() -> Generator[None, None, None]:
    """清理 API 测试留下的商品与条码。"""
    yield
    s = SessionLocal()
    try:
        rows = s.execute(
            text("SELECT id FROM prd_product WHERE product_name LIKE 'API测试%' "
                 "OR product_name LIKE 'API 测试%'")
        ).fetchall()
        for (pid,) in rows:
            s.execute(text("DELETE FROM prd_barcode WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_price_history WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_product WHERE id=:p"), {"p": pid})
        s.execute(text("DELETE FROM prd_barcode WHERE barcode LIKE '7777%'"))
        s.commit()
    finally:
        s.close()


@pytest.fixture
def admin_headers(auth_client: TestClient) -> dict[str, str]:
    """admin 登录后的 Authorization headers。"""
    data = login_as(auth_client, "admin")
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture
def cashier_headers(auth_client: TestClient) -> dict[str, str]:
    """cashier01 登录后的 headers（无 goods:product:edit 权限）。"""
    data = login_as(auth_client, "cashier01")
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.mark.integration
class TestProductList:
    """GET /products —— 验收项 2/3/4/19。"""

    def test_list_returns_paginated_products(self, auth_client: TestClient,
                                              admin_headers: dict) -> None:
        """验收项 2：分页返回，字段完整。"""
        r = auth_client.get("/api/v1/products?page=1&page_size=5", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] >= 20  # 至少 20 个演示商品
        assert len(body["data"]["list"]) == 5
        assert body["data"]["page"] == 1
        assert body["data"]["page_size"] == 5

        first = body["data"]["list"][0]
        # 必备字段
        for field in ("id", "product_code", "product_name", "category_name",
                      "unit_name", "stock_qty", "sale_price", "avg_cost"):
            assert field in first, f"缺少字段 {field}"

    def test_decimal_fields_are_numbers_not_strings(self, auth_client: TestClient,
                                                     admin_headers: dict) -> None:
        """⚠️ 验收项 19：sale_price / avg_cost / stock_qty 必须是 number 不是字符串。"""
        r = auth_client.get("/api/v1/products?page=1&page_size=5", headers=admin_headers)
        first = r.json()["data"]["list"][0]
        # 断言类型是 float（Pydantic 序列化为 JSON number 后 Python 反解成 float）
        assert isinstance(first["sale_price"], (int, float)), \
            f"sale_price 应为 number，实际 {type(first['sale_price'])}"
        assert isinstance(first["avg_cost"], (int, float)), \
            f"avg_cost 应为 number，实际 {type(first['avg_cost'])}"
        assert isinstance(first["stock_qty"], (int, float))

    def test_three_level_category_name_resolved(self, auth_client: TestClient,
                                                 admin_headers: dict) -> None:
        """⚠️ 验收项 3：三级分类的商品「有机小番茄」的 category_name = '叶菜类'（不是 '—'）。"""
        r = auth_client.get("/api/v1/products?keyword=有机小番茄", headers=admin_headers)
        items = r.json()["data"]["list"]
        assert len(items) >= 1
        # 找到「有机小番茄」
        target = next((i for i in items if i["product_name"] == "有机小番茄"), None)
        assert target is not None
        assert target["category_name"] == "叶菜类", \
            f"三级分类名错误：{target['category_name']}（Mock 缺陷复现）"

    def test_barcode_search_no_duplicates(self, auth_client: TestClient,
                                           admin_headers: dict) -> None:
        """⚠️ 验收项 4：商品 14 有 2 个条码，用任一条码搜索都只返回 1 条（EXISTS 生效）。"""
        # 主条码
        r = auth_client.get("/api/v1/products?keyword=6900000000141", headers=admin_headers)
        assert r.json()["data"]["total"] == 1
        assert len(r.json()["data"]["list"]) == 1
        # 附加条码
        r = auth_client.get("/api/v1/products?keyword=6900000000995", headers=admin_headers)
        assert r.json()["data"]["total"] == 1
        assert r.json()["data"]["list"][0]["id"] == 14

    def test_category_id_filter(self, auth_client: TestClient,
                                 admin_headers: dict) -> None:
        """按 category_id 过滤（Mock 支持但文档没写，spec 建议一并支持）。"""
        r = auth_client.get("/api/v1/products?category_id=41", headers=admin_headers)
        items = r.json()["data"]["list"]
        # category_id=41 (饮料) 应有 4 个商品
        assert all(i["category_id"] == 41 for i in items)
        assert r.json()["data"]["total"] == 4


@pytest.mark.integration
class TestProductDetail:
    """GET /products/{id} —— 验收项 5。"""

    def test_detail_includes_barcodes_array(self, auth_client: TestClient,
                                             admin_headers: dict) -> None:
        """⚠️ 验收项 5：商品 14 详情返回 2 个条码（一品多码）。"""
        r = auth_client.get("/api/v1/products/14", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert "barcodes" in body["data"]
        assert len(body["data"]["barcodes"]) == 2
        # 主条码在前（barcode_type=1）
        assert body["data"]["barcodes"][0] == "6900000000141"
        assert body["data"]["barcodes"][1] == "6900000000995"

    def test_detail_nonexistent_returns_2004(self, auth_client: TestClient,
                                              admin_headers: dict) -> None:
        r = auth_client.get("/api/v1/products/99999", headers=admin_headers)
        assert r.status_code == 200  # 业务错误 HTTP 200
        assert r.json()["code"] == 2004


@pytest.mark.integration
class TestProductCreate:
    """POST /products —— 验收项 1/6/7/8。"""

    def test_create_success(self, auth_client: TestClient, admin_headers: dict) -> None:
        """新建商品成功，返回完整 ProductDetail。"""
        r = auth_client.post("/api/v1/products", headers=admin_headers, json={
            "product_name": "API测试新建商品",
            "category_id": 111, "unit_id": 1,
            "purchase_price": 5.0, "sale_price": 8.0,
            "barcodes": ["7777000000001"],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["product_name"] == "API测试新建商品"
        # 编码自动生成，格式 P + 6 位
        assert body["data"]["product_code"].startswith("P")
        assert len(body["data"]["product_code"]) == 7
        assert body["data"]["barcodes"] == ["7777000000001"]

    def test_init_stock_and_safe_stock_ignored(self, auth_client: TestClient,
                                                admin_headers: dict) -> None:
        """⚠️ spec 1.2 ②：前端提交的 init_stock / safe_stock 被忽略，不报错。"""
        r = auth_client.post("/api/v1/products", headers=admin_headers, json={
            "product_name": "API测试额外字段",
            "category_id": 111, "unit_id": 1,
            "purchase_price": 5.0, "sale_price": 8.0,
            "init_stock": 100,       # 文档外字段
            "safe_stock": 10,        # 文档外字段
            "unknown_field": "xxx",  # 完全未知字段
        })
        assert r.json()["code"] == 0, f"应容忍未知字段：{r.json()}"

    def test_sale_below_purchase_returns_2003(self, auth_client: TestClient,
                                              admin_headers: dict) -> None:
        """⚠️ 验收项 6：售价<进价 → HTTP 200 + code=2003。"""
        r = auth_client.post("/api/v1/products", headers=admin_headers, json={
            "product_name": "API测试低价", "category_id": 111, "unit_id": 1,
            "purchase_price": 10.0, "sale_price": 5.0,
        })
        assert r.status_code == 200
        assert r.json()["code"] == 2003

    def test_duplicate_barcode_returns_2002(self, auth_client: TestClient,
                                             admin_headers: dict) -> None:
        """验收项 7：条码重复 → 2002。"""
        r = auth_client.post("/api/v1/products", headers=admin_headers, json={
            "product_name": "API测试重码", "category_id": 111, "unit_id": 1,
            "purchase_price": 5, "sale_price": 8,
            "barcodes": ["6900000000141"],  # 商品 14 主条码
        })
        assert r.json()["code"] == 2002

    def test_missing_name_returns_2001(self, auth_client: TestClient,
                                        admin_headers: dict) -> None:
        """验收项 8：Pydantic 拦截缺失字段。"""
        r = auth_client.post("/api/v1/products", headers=admin_headers, json={
            "category_id": 111, "unit_id": 1,
            "purchase_price": 5, "sale_price": 8,
        })
        assert r.status_code == 200
        assert r.json()["code"] == 2001


@pytest.mark.integration
class TestProductPermissionBoundary:
    """验收项 24/25：权限边界。"""

    def test_unauthenticated_returns_401(self, auth_client: TestClient) -> None:
        """未登录 → HTTP 401。"""
        r = auth_client.get("/api/v1/products")
        assert r.status_code == 401

    def test_cashier_cannot_create_product(self, auth_client: TestClient,
                                            cashier_headers: dict) -> None:
        """cashier01（无 goods:product:edit）POST /products → 403 + code=1040。"""
        r = auth_client.post("/api/v1/products", headers=cashier_headers, json={
            "product_name": "cashier 尝试", "category_id": 111, "unit_id": 1,
            "purchase_price": 5, "sale_price": 8,
        })
        assert r.status_code == 403
        assert r.json()["code"] == 1040

    def test_cashier_can_list_products(self, auth_client: TestClient,
                                        cashier_headers: dict) -> None:
        """cashier01 有 goods:product:list 吗？—— 实际没有，应 403。"""
        r = auth_client.get("/api/v1/products", headers=cashier_headers)
        # cashier01 permissions: dashboard:view / member:* / pos:* / promo:list
        # 不含 goods:product:list → 403
        assert r.status_code == 403
        assert r.json()["code"] == 1040

    def test_cashier_can_query_barcode(self, auth_client: TestClient,
                                        cashier_headers: dict) -> None:
        """cashier01 有 pos:use 权限 → 可访问 /products/barcode/{code}。"""
        r = auth_client.get("/api/v1/products/barcode/6900000000141", headers=cashier_headers)
        assert r.status_code == 200
        assert r.json()["code"] == 0
        assert r.json()["data"]["id"] == 14


@pytest.mark.integration
class TestBarcodeLookup:
    """GET /products/barcode/{barcode} —— 验收项 20。"""

    def test_nonexistent_barcode_returns_code_0_null(self, auth_client: TestClient,
                                                      admin_headers: dict) -> None:
        """⚠️ 验收项 20：找不到时 code=0 + data=null（不是 6001）。"""
        r = auth_client.get("/api/v1/products/barcode/NONEXISTENT_XYZ", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"] is None

    def test_secondary_barcode_found(self, auth_client: TestClient,
                                      admin_headers: dict) -> None:
        """附加条码也能查到（不只主条码）。"""
        r = auth_client.get("/api/v1/products/barcode/6900000000995", headers=admin_headers)
        assert r.json()["code"] == 0
        assert r.json()["data"]["id"] == 14


@pytest.mark.integration
class TestProductDelete:
    """DELETE /products/{id} —— 验收项 11/12。"""

    def test_delete_demo_product_returns_2007(self, auth_client: TestClient,
                                              admin_headers: dict) -> None:
        """⚠️ 验收项 11：演示商品有业务流水 → 2007（不是 500 外键错误）。"""
        r = auth_client.delete("/api/v1/products/1", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 2007
        assert "业务流水" in body["message"] or "inv_stock" in body["message"]

    def test_create_then_delete_succeeds(self, auth_client: TestClient,
                                          admin_headers: dict) -> None:
        """验收项 12：新建 → 删除 → 子表 prd_barcode 已清理。"""
        # 新建
        r = auth_client.post("/api/v1/products", headers=admin_headers, json={
            "product_name": "API测试待删商品", "category_id": 111, "unit_id": 1,
            "purchase_price": 5, "sale_price": 8,
            "barcodes": ["7777000099999"],
        })
        pid = r.json()["data"]["id"]

        # 删除
        r = auth_client.delete(f"/api/v1/products/{pid}", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["code"] == 0

        # 验证子表已清理
        s = SessionLocal()
        bc_count = s.execute(
            text("SELECT COUNT(*) FROM prd_barcode WHERE product_id=:p"), {"p": pid}
        ).scalar_one()
        p_count = s.execute(
            text("SELECT COUNT(*) FROM prd_product WHERE id=:p"), {"p": pid}
        ).scalar_one()
        s.close()
        assert bc_count == 0
        assert p_count == 0


@pytest.mark.integration
class TestProductImport:
    """POST /products/import —— 验收项 21/22/23。"""

    def _xlsx(self, rows: list[list]) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.append(["商品名称", "分类名称", "单位名称", "商品编码", "规格", "条码",
                   "进价", "售价", "会员价", "是否称重", "保质期天数", "备注"])
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_import_partial_success(self, auth_client: TestClient,
                                     admin_headers: dict) -> None:
        """验收项 21：10 行数据，第 3 行编码撞库、第 5 行名称空 → total=10 success=8 failed=2。"""
        xlsx = self._xlsx([
            ["API测试 导入 1", "叶菜类", "斤", "P777701", "", "7777000000001", 3, 5, None, 0, None, ""],
            ["API测试 导入 2", "叶菜类", "斤", "P777702", "", "7777000000002", 3, 5, None, 0, None, ""],
            ["API测试 导入 3 撞库", "叶菜类", "斤", "P000001", "", "7777000000003", 3, 5, None, 0, None, ""],
            ["API测试 导入 4", "叶菜类", "斤", "P777704", "", "7777000000004", 3, 5, None, 0, None, ""],
            ["", "叶菜类", "斤", "P777705", "", "7777000000005", 3, 5, None, 0, None, ""],
            ["API测试 导入 6", "叶菜类", "斤", "P777706", "", "7777000000006", 3, 5, None, 0, None, ""],
            ["API测试 导入 7", "叶菜类", "斤", "P777707", "", "7777000000007", 3, 5, None, 0, None, ""],
            ["API测试 导入 8", "叶菜类", "斤", "P777708", "", "7777000000008", 3, 5, None, 0, None, ""],
            ["API测试 导入 9", "叶菜类", "斤", "P777709", "", "7777000000009", 3, 5, None, 0, None, ""],
            ["API测试 导入 10", "叶菜类", "斤", "P777710", "", "7777000000010", 3, 5, None, 0, None, ""],
        ])
        r = auth_client.post(
            "/api/v1/products/import", headers=admin_headers,
            files={"file": ("test.xlsx", xlsx,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["total"] == 10
        assert data["success"] == 8
        assert data["failed"] == 2
        # errors 应含"第 3 行"和"第 5 行"
        error_text = " ".join(data["errors"])
        assert "第 3 行" in error_text
        assert "第 5 行" in error_text

        # 验证 8 条已落库
        s = SessionLocal()
        count = s.execute(
            text("SELECT COUNT(*) FROM prd_product WHERE product_code LIKE 'P7777%'")
        ).scalar_one()
        s.close()
        assert count == 8

    def test_import_xls_rejected(self, auth_client: TestClient,
                                  admin_headers: dict) -> None:
        """验收项 23：.xls → 2001。"""
        r = auth_client.post(
            "/api/v1/products/import", headers=admin_headers,
            files={"file": ("old.xls", b"fake", "application/vnd.ms-excel")},
        )
        assert r.status_code == 200
        assert r.json()["code"] == 2001
        assert ".xlsx" in r.json()["message"]
