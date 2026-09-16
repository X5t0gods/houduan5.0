"""采购 API 端到端集成测试（阶段 6 CP4 交付物）。

覆盖 spec 验收项 2/30/31/32 + 14 接口链路：
- 完整链路：建单 → 审核 → 收货（状态变化 + 成本重算）
- 无单入库、采购退货
- Decimal 序列化（avg_cost 是 number 保留 4 位）
- 权限边界：cashier01 无 pur:* → 403；stocker01 有 pur:receipt；buyer01 有 pur:order:*
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
def cleanup_purchase_api_data() -> Generator[None, None, None]:
    """清理 API 测试产生的采购数据 + 恢复种子库存/应付。"""
    yield
    s = SessionLocal()
    try:
        s.execute(text("DELETE FROM pur_receipt_item"))
        s.execute(text("DELETE FROM pur_receipt"))
        s.execute(text("DELETE FROM pur_return_item"))
        s.execute(text("DELETE FROM pur_return"))
        s.execute(text("DELETE FROM pur_order_item"))
        s.execute(text("DELETE FROM pur_order"))
        s.execute(text("DELETE FROM pur_payment WHERE remark LIKE '%API%'"))
        s.execute(text(
            "DELETE FROM inv_stock_flow WHERE flow_type IN ('PURCHASE_IN','PURCHASE_RETURN_OUT')"
        ))
        s.execute(text(
            "DELETE FROM inv_batch WHERE batch_no NOT IN "
            "('P000001-20260910-01','P000003-20260901-01',"
            "'P000005-20260901-01','P000006-20260913-01')"
        ))
        s.execute(text("DELETE FROM inv_stock WHERE store_id=2"))
        s.execute(text(
            "UPDATE inv_stock SET quantity=42.500, avg_cost=4.2000 WHERE store_id=1 AND product_id=1"
        ))
        s.execute(text(
            "UPDATE inv_stock SET quantity=168.000, avg_cost=2.4000 WHERE store_id=1 AND product_id=14"
        ))
        s.execute(text("UPDATE pur_supplier SET balance_payable=0.00 WHERE id=1"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=12860.00 WHERE id=2"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=4320.50 WHERE id=3"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=8640.00 WHERE id=4"))
        s.execute(text("UPDATE pur_supplier SET balance_payable=0.00 WHERE id=5"))
        s.execute(text("DELETE FROM pur_supplier WHERE supplier_code > 'S000005'"))
        s.execute(text(
            "UPDATE sys_no_seq SET current_val=5 WHERE seq_key='S' AND seq_date='1970-01-01'"
        ))
        s.execute(text("DELETE FROM sys_no_seq WHERE seq_key IN ('CG','SH','CT','FK','BATCH')"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestPurchaseFlow:
    """采购完整链路（建单→审核→收货）。"""

    def test_full_flow(self, client: TestClient) -> None:
        """建单 → 审核 → 收货，状态 DRAFT→AUDITED→FINISHED，成本重算。"""
        headers = _headers(client, "buyer01")  # 有 pur:order:*
        # 建单
        r = client.post("/api/v1/purchases", headers=headers, json={
            "supplier_id": 1, "store_id": 1,
            "items": [{"product_id": 14, "quantity": 20, "unit_price": 2.50}],
        }).json()
        assert r["code"] == 0, r
        order_id = r["data"]["id"]
        assert r["data"]["status"] == "DRAFT"

        # 审核
        r = client.post(f"/api/v1/purchases/{order_id}/audit", headers=headers,
                        json={"approved": True}).json()
        assert r["code"] == 0

        # 收货（stocker01 有 pur:receipt）
        rh = _headers(client, "stocker01")
        r = client.post(f"/api/v1/purchases/{order_id}/receive", headers=rh, json={
            "items": [{"product_id": 14, "receive_qty": 20, "unit_price": 2.50}],
        }).json()
        assert r["code"] == 0, r
        assert r["data"]["order_status"] == "FINISHED"
        assert r["data"]["receipt_no"].startswith("SH")

        # 成本重算：(168×2.4 + 20×2.5)/188 = (403.2+50)/188 = 2.4106...
        s = SessionLocal()
        try:
            row = s.execute(text(
                "SELECT quantity, avg_cost FROM inv_stock WHERE store_id=1 AND product_id=14"
            )).fetchone()
            assert row[0] == 188
            assert str(row[1]).startswith("2.41")  # 重算后成本上升
        finally:
            s.close()

    def test_order_detail_items_joined(self, client: TestClient) -> None:
        """验收 29：详情 items 含 JOIN 出的 product_code/name/unit_name。"""
        headers = _headers(client, "buyer01")
        r = client.post("/api/v1/purchases", headers=headers, json={
            "supplier_id": 1, "store_id": 1,
            "items": [{"product_id": 14, "quantity": 5, "unit_price": 2.40}],
        }).json()
        order_id = r["data"]["id"]
        r = client.get(f"/api/v1/purchases/{order_id}", headers=headers).json()
        assert r["code"] == 0
        item = r["data"]["items"][0]
        assert item["product_code"] is not None
        assert item["product_name"] is not None
        assert item["unit_name"] is not None

    def test_direct_receive(self, client: TestClient) -> None:
        """验收 16：无单入库 → order FINISHED + is_direct=1。"""
        headers = _headers(client, "stocker01")  # pur:receipt
        r = client.post("/api/v1/purchases/direct-receive", headers=headers, json={
            "supplier_id": 1, "store_id": 1,
            "items": [{"product_id": 14, "quantity": 10, "unit_price": 2.60}],
        }).json()
        assert r["code"] == 0, r
        assert r["data"]["is_direct"] == 1
        assert r["data"]["order_status"] == "FINISHED"
        assert r["data"]["order_id"] is not None

    def test_return(self, client: TestClient) -> None:
        """验收 18：采购退货 → 库存减 + 应付减。"""
        headers = _headers(client, "stocker01")
        r = client.post("/api/v1/purchases/returns", headers=headers, json={
            "supplier_id": 2, "store_id": 1, "reason": "API测试退货",
            "items": [{"product_id": 14, "quantity": 3, "unit_price": 2.40}],
        }).json()
        assert r["code"] == 0, r
        assert r["data"]["return_no"].startswith("CT")
        # 供应商2 应付减少 3×2.4=7.2 → 12860-7.2=12852.8
        assert r["data"]["balance_payable"] == 12852.80


@pytest.mark.integration
class TestPurchasePermissions:
    """权限边界（验收 31/32）。"""

    def test_cashier_no_pur_perm_403(self, client: TestClient) -> None:
        """验收 31：cashier01 无 pur:* → 403 + 1040。"""
        headers = _headers(client, "cashier01")
        r = client.get("/api/v1/purchases", headers=headers)
        assert r.status_code == 403
        assert r.json()["code"] == 1040

    def test_buyer_can_create(self, client: TestClient) -> None:
        """验收 31：buyer01 有 pur:order:create → 建单通过。"""
        headers = _headers(client, "buyer01")
        r = client.post("/api/v1/purchases", headers=headers, json={
            "supplier_id": 1, "store_id": 1,
            "items": [{"product_id": 14, "quantity": 1, "unit_price": 2.40}],
        })
        assert r.status_code == 200
        assert r.json()["code"] == 0

    def test_unauthorized_401(self, client: TestClient) -> None:
        """验收 32：无 token → 401。"""
        r = client.get("/api/v1/purchases")
        assert r.status_code == 401


@pytest.mark.integration
class TestDecimalSerialization:
    """Decimal 序列化（验收 30）。"""

    def test_supplier_balance_is_number(self, client: TestClient) -> None:
        """供应商 balance_payable 是 number（不是字符串）。"""
        headers = _headers(client, "buyer01")
        r = client.get("/api/v1/suppliers?page=1&page_size=20", headers=headers).json()
        assert r["code"] == 0
        s2 = next(x for x in r["data"]["list"] if x["id"] == 2)
        assert isinstance(s2["balance_payable"], (int, float))
        assert s2["balance_payable"] == 12860.00

    def test_order_total_is_number(self, client: TestClient) -> None:
        """订单 total_amount 是 number。"""
        headers = _headers(client, "buyer01")
        r = client.post("/api/v1/purchases", headers=headers, json={
            "supplier_id": 1, "store_id": 1,
            "items": [{"product_id": 14, "quantity": 3, "unit_price": 2.40}],
        }).json()
        assert isinstance(r["data"]["total_amount"], (int, float))
        assert r["data"]["total_amount"] == 7.20
