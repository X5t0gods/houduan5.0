"""测试配置 - pytest fixtures。

提供 TestClient 等公共 fixture，供所有测试文件使用。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """创建测试客户端。

    Returns:
        FastAPI TestClient 实例，用于发送测试请求。
    """
    return TestClient(app)


@pytest.fixture
def app_instance():
    """获取 FastAPI 应用实例（用于直接测试应用配置）。

    Returns:
        FastAPI app 实例。
    """
    return app
