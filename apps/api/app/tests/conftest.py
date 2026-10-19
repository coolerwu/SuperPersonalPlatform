import os
import tempfile

_TEST_ROOT = tempfile.mkdtemp(prefix="acc-test-")
os.environ["APP_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_ROOT}/test.db"
os.environ["APP_DATA_DIR"] = _TEST_ROOT
os.environ["APP_AUTH_TOKEN"] = "test-token"
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
os.environ["LLM_MODEL"] = "deepseek-v4-flash"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app(start_worker=False)) as test_client:
        response = test_client.post("/api/auth/login", json={"token": "test-token"})
        assert response.status_code == 200
        yield test_client
