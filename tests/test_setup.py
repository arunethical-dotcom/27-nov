from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from app.main import DISCLAIMER, create_app


def test_startup(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    with TestClient(create_app(url)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert DISCLAIMER in page.text
        assert client.get("/missing").json()["disclaimer"] == DISCLAIMER
        assert set(inspect(create_engine(url)).get_table_names()) == {
            "product", "sds_version", "chunk", "fact", "session", "session_product",
            "pair_result", "change_report",
        }
