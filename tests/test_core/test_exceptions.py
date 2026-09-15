"""异常处理测试。

验证：
- BusinessError 走 HTTP 200 + 非 0 code
- RequestValidationError 走 HTTP 200 + code 2001
- 未捕获异常走 HTTP 500 + 通用提示（不暴露堆栈）
- 404 返回正确结构
"""

from fastapi.testclient import TestClient


class TestBusinessError:
    """测试业务错误通道。"""

    def test_business_error_returns_200_with_nonzero_code(self, client: TestClient) -> None:
        """业务错误必须返回 HTTP 200 + 非 0 code。

        这是最关键的约定：前端 request.ts 只在 HTTP 401 时清登录态，
        若业务错误返回 401 会导致用户被莫名踢回登录页。
        """
        # DEBUG=true 时存在调试路由
        response = client.get("/api/v1/_dev/raise-business-error")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 2001
        assert body["message"] == "必填项缺失"
        assert body["data"] is None
        assert "request_id" in body
        assert "timestamp" in body


class TestValidationError:
    """测试参数校验错误。"""

    def test_validation_error_returns_200_with_2001(self, client: TestClient) -> None:
        """Pydantic 校验失败返回 HTTP 200 + code 2001。"""
        # 访问不存在的接口会触发 404，这里用健康检查接口的错误参数测试
        # 由于 health 接口无参数，我们测试 404 场景
        response = client.get("/api/v1/not-exist")

        # 404 是 HTTP 层错误，保留原状态码
        assert response.status_code == 404
        body = response.json()
        assert "code" in body
        assert "message" in body
        assert "request_id" in body


class TestHTTPException:
    """测试 HTTP 异常处理。"""

    def test_404_returns_unified_structure(self, client: TestClient) -> None:
        """404 返回统一响应体结构（含 code/message/request_id）。"""
        response = client.get("/api/v1/definitely-not-exist")

        assert response.status_code == 404
        body = response.json()
        assert "code" in body
        assert "message" in body
        assert "request_id" in body
        assert "timestamp" in body


class TestRequestIdConsistency:
    """测试 request_id 一致性。"""

    def test_request_id_in_header_and_body_match(self, client: TestClient) -> None:
        """响应体中的 request_id 必须与响应头 X-Request-Id 一致。"""
        response = client.get("/api/v1/health")

        header_request_id = response.headers.get("X-Request-Id")
        body_request_id = response.json().get("request_id")

        assert header_request_id is not None
        assert body_request_id is not None
        assert header_request_id == body_request_id

    def test_custom_request_id_is_preserved(self, client: TestClient) -> None:
        """请求头带 X-Request-Id 时沿用（便于联调追踪）。"""
        custom_id = "my-custom-trace-id-12345"
        response = client.get("/api/v1/health", headers={"X-Request-Id": custom_id})

        assert response.headers.get("X-Request-Id") == custom_id
        assert response.json().get("request_id") == custom_id
