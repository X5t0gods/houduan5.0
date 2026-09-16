"""库存 API 端到端集成测试（阶段 5 CP4 交付物）。

覆盖 spec 验收项 1/9/23/24/26/27/30/31（API 层）：
- 库存列表字段完整 + stock_amount 是 number
- 流水列表
- 调拨跨门店 + 同门店被拒
- 期初库存（POST /products 带 init_stock）
- 权限边界（cashier01 无 inv:check → 403；stocker01 有 → 通过）
- 未登录 → 401
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from tests.test_api.conftest import login_as


def _headers(client: TestClient, username: str) -> dict[str, str]:
    data = login_as(client, username)
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture(autouse=True)
def cleanup_inventory_api_data() -> Generator[None, None, None]:
    """清理 API 测试产生的调拨/报损/期初库存数据。"""
    yield
    s = SessionLocal()
    try:
        # 清调拨
        rows = s.execute(text(
            "SELECT id FROM inv_transfer WHERE remark LIKE '%API%'"
        )).fetchall()
        for (tid,) in rows:
            s.execute(text("DELETE FROM inv_transfer_item WHERE transfer_id=:t"), {"t": tid})
            s.execute(text("DELETE FROM inv_transfer WHERE id=:t"), {"t": tid})
        # 清报损
        rows = s.execute(text("SELECT id FROM inv_loss WHERE reason LIKE '%API%'")).fetchall()
        for (lid,) in rows:
            s.execute(text("DELETE FROM inv_loss_item WHERE loss_id=:l"), {"l": lid})
            s.execute(text("DELETE FROM inv_loss WHERE id=:l"), {"l": lid})
        # 清流水（调拨/报损）——⛔ 不能用 source_no LIKE 'QC%'，
        # 因为 demo 的 20 条 INIT 流水 source_no='QC20260915001'，会被误删！
        # 测试商品的 INIT/QC 流水由下面的 per-product 循环删除
        s.execute(text(
            "DELETE FROM inv_stock_flow WHERE flow_type IN "
            "('TRANSFER_IN','TRANSFER_OUT','LOSS_OUT')"
        ))
        # 清 API 测试商品 + 其库存
        rows = s.execute(text(
            "SELECT id FROM prd_product WHERE product_name LIKE 'API库存%'"
        )).fetchall()
        for (pid,) in rows:
            s.execute(text("DELETE FROM inv_stock_flow WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM inv_batch WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM inv_stock WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_barcode WHERE product_id=:p"), {"p": pid})
            s.execute(text("DELETE FROM prd_product WHERE id=:p"), {"p": pid})
        # 删调拨到门店2新建的库存
        s.execute(text("DELETE FROM inv_stock WHERE store_id=2"))
        # 恢复门店1库存
        s.execute(text("UPDATE inv_stock SET quantity=42.500 WHERE store_id=1 AND product_id=1"))
        s.execute(text("UPDATE inv_stock SET quantity=18.200 WHERE store_id=1 AND product_id=2"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestInventoryList:
    """库存列表与流水（验收 1/2/9）。"""

    def test_list_fields_complete(self, client: TestClient) -> None:
        """验收 1：库存列表字段完整，stock_amount 是 number。"""
        headers = _headers(client, "stocker01")
        r = client.get("/api/v1/inventory?page=1&page_size=5", headers=headers)
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 20
        first = body["data"]["list"][0]
        for field in ("unit_name", "store_name", "stock_amount", "stock_status", "remain_days"):
            assert field in first
        # stock_amount 是 number（不是字符串）
        assert isinstance(first["stock_amount"], (int, float))

    def test_flows_list(self, client: TestClient) -> None:
        """验收 9：流水列表 20 条（全 INIT），含 product_name/operator_name。"""
        headers = _headers(client, "stocker01")
        r = client.get("/api/v1/inventory/flows?page=1&page_size=100", headers=headers)
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 20
        first = body["data"]["list"][0]
        assert first["product_name"] is not None
        assert "operator_name" in first

    def test_warnings_three_arrays(self, client: TestClient) -> None:
        """验收 7：warnings 三数组，low_stock 含商品 2/4。"""
        headers = _headers(client, "stocker01")
        r = client.get("/api/v1/inventory/warnings", headers=headers)
        body = r.json()
        assert body["code"] == 0
        d = body["data"]
        assert "low_stock" in d and "over_stock" in d and "near_expiry" in d
        low_ids = [x["product_id"] for x in d["low_stock"]]
        assert 2 in low_ids and 4 in low_ids


@pytest.mark.integration
class TestTransferApi:
    """调拨（验收 23/24/26）。"""

    def test_transfer_cross_store(self, client: TestClient) -> None:
        """验收 23：1→2 调拨 → 门店1减、门店2增，status=IN。"""
        headers = _headers(client, "admin")  # admin 有 inv:transfer
        r = client.post("/api/v1/inventory/transfers", headers=headers, json={
            "from_store_id": 1, "to_store_id": 2, "remark": "API测试调拨",
            "items": [{"product_id": 1, "quantity": 5}],
        })
        body = r.json()
        assert body["code"] == 0, body
        assert body["data"]["status"] == "IN"
        assert body["data"]["transfer_no"].startswith("DB")

        # 门店2库存增加5
        s = SessionLocal()
        try:
            q2 = s.execute(text(
                "SELECT quantity FROM inv_stock WHERE store_id=2 AND product_id=1"
            )).scalar_one()
            assert q2 == 5
        finally:
            s.close()

    def test_transfer_same_store_rejected(self, client: TestClient) -> None:
        """验收 24：from == to → 2001。"""
        headers = _headers(client, "admin")
        r = client.post("/api/v1/inventory/transfers", headers=headers, json={
            "from_store_id": 1, "to_store_id": 1, "remark": "API测试同门店",
            "items": [{"product_id": 1, "quantity": 1}],
        })
        body = r.json()
        assert body["code"] == 2001

    def test_transfer_list_has_store_names(self, client: TestClient) -> None:
        """验收 26：调拨列表含 from_store/to_store 门店名 + item_count。"""
        headers = _headers(client, "admin")
        # 先建一个调拨
        client.post("/api/v1/inventory/transfers", headers=headers, json={
            "from_store_id": 1, "to_store_id": 2, "remark": "API测试列表",
            "items": [{"product_id": 1, "quantity": 2}],
        })
        r = client.get("/api/v1/inventory/transfers?page=1&page_size=10", headers=headers)
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] >= 1
        first = body["data"]["list"][0]
        assert first["from_store"] is not None  # 门店名而非 ID
        assert first["to_store"] is not None
        assert "item_count" in first


@pytest.mark.integration
class TestInitStockApi:
    """期初库存（验收 27/28/29）。"""

    def test_init_stock_via_product_api(self, client: TestClient) -> None:
        """验收 27：POST /products 带 init_stock → inv_stock + INIT 流水（QC 单号）。"""
        headers = _headers(client, "admin")
        r = client.post("/api/v1/products", headers=headers, json={
            "product_name": "API库存期初测试",
            "category_id": 111, "unit_id": 1,
            "purchase_price": 6.0, "sale_price": 10.0,
            "init_stock": 20, "safe_stock": 5,
        })
        body = r.json()
        assert body["code"] == 0, body
        pid = body["data"]["id"]

        s = SessionLocal()
        try:
            stock = s.execute(text(
                "SELECT quantity, safe_qty FROM inv_stock WHERE product_id=:p"
            ), {"p": pid}).fetchone()
            assert stock is not None
            assert stock[0] == 20
            assert stock[1] == 5
            flow = s.execute(text(
                "SELECT flow_type, source_no FROM inv_stock_flow "
                "WHERE product_id=:p ORDER BY id DESC LIMIT 1"
            ), {"p": pid}).fetchone()
            assert flow[0] == "INIT"
            assert flow[1].startswith("QC")
        finally:
            s.close()

    def test_perishable_creates_batch(self, client: TestClient) -> None:
        """验收 29：保质期商品 init_stock → inv_batch（batch_no 形如 Pxxxxxx-日期-01）。"""
        headers = _headers(client, "admin")
        r = client.post("/api/v1/products", headers=headers, json={
            "product_name": "API库存保质期测试",
            "category_id": 111, "unit_id": 1,
            "purchase_price": 6.0, "sale_price": 10.0,
            "is_perishable": 1, "shelf_life_days": 7,
            "init_stock": 10,
        })
        body = r.json()
        assert body["code"] == 0, body
        pid = body["data"]["id"]
        product_code = body["data"]["product_code"]

        s = SessionLocal()
        try:
            batch = s.execute(text(
                "SELECT batch_no, expiry_date, remain_qty FROM inv_batch WHERE product_id=:p"
            ), {"p": pid}).fetchone()
            assert batch is not None
            assert batch[0].startswith(product_code)  # Pxxxxxx-日期-01
            assert batch[2] == 10  # remain_qty = init_stock
        finally:
            s.close()


@pytest.mark.integration
class TestInventoryPermissions:
    """权限边界（验收 30/31）。"""

    def test_cashier_no_check_perm_403(self, client: TestClient) -> None:
        """验收 30：cashier01 无 inv:check → 403。"""
        headers = _headers(client, "cashier01")
        r = client.get("/api/v1/inventory/checks", headers=headers)
        assert r.status_code == 403
        assert r.json()["code"] == 1040

    def test_stocker_has_check_perm(self, client: TestClient) -> None:
        """验收 30：stocker01 有 inv:check → 通过。"""
        headers = _headers(client, "stocker01")
        r = client.get("/api/v1/inventory/checks", headers=headers)
        assert r.status_code == 200
        assert r.json()["code"] == 0

    def test_unauthorized_401(self, client: TestClient) -> None:
        """验收 31：无 token → 401。"""
        r = client.get("/api/v1/inventory")
        assert r.status_code == 401
