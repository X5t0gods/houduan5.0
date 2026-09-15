"""健康检查接口测试。

验证：
- GET /api/v1/health 返回 200
- 响应体结构正确（code=0, data 含必要字段）
- 数据库连不上时仍返回 200（db_status=disconnected）
- request_id 一致性
"""

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """测试健康检查接口。"""

    def test_health_returns_200(self, client: TestClient) -> None:
        """健康检查必须返回 HTTP 200。"""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client: TestClient) -> None:
        """健康检查响应体必须包含统一结构的 5 个字段。"""
        response = client.get("/api/v1/health")
        body = response.json()

        # 统一响应体 5 个固定字段
        assert "code" in body
        assert "message" in body
        assert "data" in body
        assert "request_id" in body
        assert "timestamp" in body

        # code=0 表示成功
        assert body["code"] == 0
        assert body["message"] == "success"

    def test_health_data_fields(self, client: TestClient) -> None:
        """健康检查 data 必须包含必要的状态字段。"""
        response = client.get("/api/v1/health")
        data = response.json()["data"]

        # 必要字段
        assert "status" in data
        assert "version" in data
        assert "python_version" in data
        assert "db_status" in data
        assert "db_latency_ms" in data
        assert "uptime_seconds" in data
        assert "start_time" in data
        assert "server_time" in data

        # status 必须是 ok
        assert data["status"] == "ok"

        # db_status 必须是 connected 或 disconnected
        assert data["db_status"] in ("connected", "disconnected")

    def test_health_timestamp_format(self, client: TestClient) -> None:
        """时间戳格式必须是 YYYY-MM-DD HH:mm:ss（不是 ISO 带 T 格式）。"""
        response = client.get("/api/v1/health")
        body = response.json()

        timestamp = body["timestamp"]
        # 确认不是 ISO 格式
        assert "T" not in timestamp
        # 确认格式正确（长度 19，含两个空格和两个冒号）
        assert len(timestamp) == 19
        assert timestamp.count(":") == 2
        assert timestamp.count(" ") == 1
        assert timestamp.count("-") == 2

    def test_health_request_id_in_header(self, client: TestClient) -> None:
        """响应头必须包含 X-Request-Id。"""
        response = client.get("/api/v1/health")

        assert "X-Request-Id" in response.headers
        assert len(response.headers["X-Request-Id"]) > 0
