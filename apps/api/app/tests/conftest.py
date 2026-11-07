import os
import tempfile

_TEST_ROOT = tempfile.mkdtemp(prefix="acc-test-")
os.environ["APP_HOME"] = _TEST_ROOT
os.environ["APP_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_ROOT}/test.db"
os.environ["APP_DATA_DIR"] = _TEST_ROOT
os.environ["APP_WORKSPACES_DIR"] = f"{_TEST_ROOT}/workspaces"
os.environ["APP_SKILLS_DIR"] = f"{_TEST_ROOT}/skills"
os.environ["APP_AUTH_TOKEN"] = "test-token"
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
os.environ["LLM_MODEL"] = "deepseek-v4-flash"
# Keep automatic memory consolidation off unless a test turns it on; otherwise
# unrelated tests that create runs would queue organizer runs in the shared DB.
os.environ["MEMORY_CONSOLIDATE_EVERY_TURNS"] = "0"
for _proxy_name in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
):
    os.environ.pop(_proxy_name, None)

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app(start_worker=False)) as test_client:
        response = test_client.post("/api/auth/login", json={"token": "test-token"})
        assert response.status_code == 200
        yield test_client
