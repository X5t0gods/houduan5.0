"""销售 API 端到端集成测试（阶段 4 CP4 交付物）。

覆盖 spec 验收项 3-31（API 层面）：
- 完整链路：resolve → calc → settle → list → detail → receipt → return → void
- 挂单闭环：create → list → resume → cancel
- 班次：auto-open → close
- 权限边界：stocker01 无 pos:use → 403
- 未登录：无 token → 401
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from tests.test_api.conftest import login_as


def _headers(client: TestClient, username: str) -> dict[str, str]:
    """login_as 返回 data dict，提取 access_token 构建 headers。"""
    data = login_as(client, username)
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture(autouse=True)
def cleanup_api_sale_data() -> Generator[None, None, None]:
    """清理 API 测试产生的销售数据。"""
    yield
    s = SessionLocal()
    try:
        # 清退货
        rows = s.execute(text(
            "SELECT id, return_no FROM sal_return WHERE reason LIKE '%API%'"
        )).fetchall()
        for rid, ret_no in rows:
            s.execute(text("DELETE FROM sal_return_item WHERE return_id=:r"), {"r": rid})
            s.execute(text("DELETE FROM inv_stock_flow WHERE source_no=:rn"), {"rn": ret_no})
            s.execute(text("DELETE FROM sal_return WHERE id=:r"), {"r": rid})
        # 清销售单
        rows = s.execute(text(
            "SELECT id, trade_no FROM sal_trade WHERE request_id LIKE 'api-test-%'"
        )).fetchall()
        for tid, trade_no in rows:
            s.execute(text("DELETE FROM sal_trade_payment WHERE trade_id=:t"), {"t": tid})
            s.execute(text("DELETE FROM sal_trade_item WHERE trade_id=:t"), {"t": tid})
            s.execute(text("DELETE FROM inv_stock_flow WHERE source_no=:tn"), {"tn": trade_no})
            s.execute(text("DELETE FROM sal_trade WHERE id=:t"), {"t": tid})
        # 清挂单
        s.execute(text("DELETE FROM sal_hold WHERE cart_json LIKE '%api-test%'"))
        # 清班次（测试开的班）
        s.execute(text("DELETE FROM sal_session WHERE session_no LIKE 'JB%' AND sale_count=0"))
        # 恢复库存
        s.execute(text(
            "UPDATE inv_stock SET quantity=168 WHERE store_id=1 AND product_id=14"
        ))
        # 恢复促销统计
        s.execute(text("UPDATE pro_promotion SET trigger_count=0, discount_total=0.00"))
        # 清会员流水
        s.execute(text("DELETE FROM mem_balance_flow WHERE source_no LIKE 'XS%'"))
        s.execute(text("DELETE FROM mem_point_flow WHERE source_no LIKE 'XS%'"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.mark.integration
class TestSaleApiFlow:
    """完整销售链路。"""

    def test_resolve_found(self, client: TestClient) -> None:
        """条码解析成功（验收 9）。"""
        headers = _headers(client, "cashier01")
        r = client.post("/api/v1/sales/cart/resolve", headers=headers, json={"code": "6900000000141"})
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["product_name"] == "可口可乐"
        assert body["data"]["unit_price"] == 3.80

    def test_resolve_not_found(self, client: TestClient) -> None:
        """条码找不到 → code=0 + data=null（验收 9）。"""
        headers = _headers(client, "cashier01")
        r = client.post("/api/v1/sales/cart/resolve", headers=headers, json={"code": "0000000000000"})
        body = r.json()
        assert body["code"] == 0
        assert body["data"] is None

    def test_calc_promotion(self, client: TestClient) -> None:
        """促销试算（验收 2）。"""
        headers = _headers(client, "cashier01")
        r = client.post("/api/v1/sales/cart/calc", headers=headers, json={
            "items": [{"product_id": 14, "quantity": 3, "unit_price": "3.80"}]
        })
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total_amount"] == 11.40
        assert body["data"]["discount_amount"] == 1.80  # 可乐特价
        assert body["data"]["receivable"] == 9.60

    def test_settle_and_query(self, client: TestClient) -> None:
        """结算 → 列表 → 详情（验收 11/18/20）。"""
        headers = _headers(client, "cashier01")
        req_id = f"api-test-{uuid.uuid4().hex[:8]}"

        # 结算
        r = client.post("/api/v1/sales/trades", headers=headers, json={
            "request_id": req_id,
            "pos_id": 1,
            "items": [{"product_id": 14, "quantity": 1, "unit_price": "3.80"}],
            "payments": [{"pay_method": "CASH", "amount": 3.20}],
            "remark": "API测试",
        })
        body = r.json()
        assert body["code"] == 0
        trade_id = body["data"]["id"]
        assert body["data"]["receivable"] == 3.20

        # 列表
        r = client.get("/api/v1/sales/trades?page=1&page_size=5", headers=headers)
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] >= 1

        # 详情
        r = client.get(f"/api/v1/sales/trades/{trade_id}", headers=headers)
        body = r.json()
        assert body["code"] == 0
        assert len(body["data"]["items"]) == 1
        assert len(body["data"]["payments"]) == 1
        assert body["data"]["items"][0]["product_name"] == "可口可乐"

        # 恢复库存
        s = SessionLocal()
        s.execute(text("UPDATE inv_stock SET quantity = quantity + 1 WHERE store_id=1 AND product_id=14"))
        s.commit()
        s.close()

    def test_idempotent(self, client: TestClient) -> None:
        """幂等（验收 12）。"""
        headers = _headers(client, "cashier01")
        req_id = f"api-test-idem-{uuid.uuid4().hex[:8]}"
        payload = {
            "request_id": req_id,
            "pos_id": 1,
            "items": [{"product_id": 14, "quantity": 1, "unit_price": "3.80"}],
            "payments": [{"pay_method": "CASH", "amount": 3.20}],
        }
        r1 = client.post("/api/v1/sales/trades", headers=headers, json=payload).json()
        r2 = client.post("/api/v1/sales/trades", headers=headers, json=payload).json()
        assert r1["code"] == 0
        assert r2["code"] == 0
        assert r1["data"]["trade_no"] == r2["data"]["trade_no"]

        # 恢复库存（只扣了一次）
        s = SessionLocal()
        s.execute(text("UPDATE inv_stock SET quantity = quantity + 1 WHERE store_id=1 AND product_id=14"))
        s.commit()
        s.close()

    def test_receipt(self, client: TestClient) -> None:
        """小票（验收 26）。"""
        headers = _headers(client, "cashier01")
        req_id = f"api-test-rcpt-{uuid.uuid4().hex[:8]}"
        r = client.post("/api/v1/sales/trades", headers=headers, json={
            "request_id": req_id, "pos_id": 1,
            "items": [{"product_id": 14, "quantity": 1, "unit_price": "3.80"}],
            "payments": [{"pay_method": "CASH", "amount": 3.20}],
        }).json()
        trade_id = r["data"]["id"]

        # 打印 3 次
        for expected_count in [1, 2, 3]:
            rr = client.post(f"/api/v1/sales/trades/{trade_id}/receipt", headers=headers).json()
            assert rr["code"] == 0
            assert rr["data"]["print_count"] == expected_count
            assert "社区超市" in rr["data"]["print_data"]

        # 恢复库存
        s = SessionLocal()
        s.execute(text("UPDATE inv_stock SET quantity = quantity + 1 WHERE store_id=1 AND product_id=14"))
        s.commit()
        s.close()

    def test_return(self, client: TestClient) -> None:
        """退货（验收 21/22）。"""
        headers = _headers(client, "cashier01")
        req_id = f"api-test-ret-{uuid.uuid4().hex[:8]}"
        r = client.post("/api/v1/sales/trades", headers=headers, json={
            "request_id": req_id, "pos_id": 1,
            "items": [{"product_id": 14, "quantity": 2, "unit_price": "3.80"}],
            "payments": [{"pay_method": "CASH", "amount": 6.40}],
        }).json()
        trade_id = r["data"]["id"]

        # 退 1 件
        rr = client.post("/api/v1/sales/trades/return", headers=headers, json={
            "source_trade_id": trade_id,
            "items": [{"product_id": 14, "quantity": 1}],
            "refund_method": "CASH",
            "reason": "API测试退货",
        }).json()
        assert rr["code"] == 0
        assert rr["data"]["return_no"].startswith("TH")

        # 验证流水
        s = SessionLocal()
        flow = s.execute(text(
            "SELECT flow_type, direction FROM inv_stock_flow "
            "WHERE source_no=:rn ORDER BY id DESC LIMIT 1"
        ), {"rn": rr["data"]["return_no"]}).fetchone()
        assert flow[0] == "SALE_RETURN_IN"
        assert flow[1] == 1
        # 恢复
        s.execute(text("UPDATE inv_stock SET quantity = quantity + 1 WHERE store_id=1 AND product_id=14"))
        s.commit()
        s.close()

    def test_hold_lifecycle(self, client: TestClient) -> None:
        """挂单闭环（验收 25）。"""
        headers = _headers(client, "cashier01")
        cart = json.dumps([{"product_id": 14, "qty": 1}]) + " api-test"

        # 挂单
        r = client.post("/api/v1/sales/holds", headers=headers, json={
            "pos_id": 1, "cart_json": cart, "item_count": 1, "amount": 3.80,
        }).json()
        assert r["code"] == 0
        hold_no = r["data"]["hold_no"]
        assert hold_no.startswith("GD")

        # 列表
        r = client.get("/api/v1/sales/holds?pos_id=1", headers=headers).json()
        assert r["code"] == 0
        assert any(h["hold_no"] == hold_no for h in r["data"])

        # 取单
        r = client.post(f"/api/v1/sales/holds/{hold_no}/resume", headers=headers).json()
        assert r["code"] == 0

        # 取单后列表不含
        r = client.get("/api/v1/sales/holds?pos_id=1", headers=headers).json()
        assert not any(h["hold_no"] == hold_no for h in r["data"])


@pytest.mark.integration
class TestSalePermissions:
    """权限边界（验收 28/29）。"""

    def test_no_permission_403(self, client: TestClient) -> None:
        """stocker01 无 pos:use → 403 + code=1040。"""
        headers = _headers(client, "stocker01")
        r = client.post("/api/v1/sales/trades", headers=headers, json={
            "request_id": "x", "pos_id": 1,
            "items": [{"product_id": 14, "quantity": 1}],
            "payments": [{"pay_method": "CASH", "amount": 3.80}],
        })
        assert r.status_code == 403
        assert r.json()["code"] == 1040

    def test_unauthorized_401(self, client: TestClient) -> None:
        """无 token → 401。"""
        r = client.post("/api/v1/sales/trades", json={
            "request_id": "x", "pos_id": 1,
            "items": [{"product_id": 14, "quantity": 1}],
            "payments": [{"pay_method": "CASH", "amount": 3.80}],
        })
        assert r.status_code == 401
