"""Тесты main: FastAPI endpoints через TestClient с моками."""
import os
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    """TestClient с temp SQLite (реальная init_db, но во временной директории)."""
    import tempfile
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_main.db")
    os.environ["GRAPHRAG_DB_PATH"] = db_path
    main.db._DB_PATH = db_path
    main.db.init_db()
    with TestClient(main.app) as c:
        yield c
    os.environ.pop("GRAPHRAG_DB_PATH", None)
    main.db._DB_PATH = os.environ.get("GRAPHRAG_DB_PATH", "/data/graphrag.db")
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestHealth:
    def test_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "model" in data
        assert "llm_base_url" in data


class TestMetrics:
    def test_returns_prometheus_format(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "# TYPE" in text
        assert "graphrag_requests_total" in text
        assert "counter" in text


class TestChatEndpoints:
    def test_create_and_list_chat(self, client):
        resp = client.post("/api/chats", json={"title": "Test Chat"})
        assert resp.status_code == 200
        data = resp.json()
        chat_id = data["id"]
        assert data["title"] == "Test Chat"

        resp = client.get("/api/chats")
        assert resp.status_code == 200
        chats = resp.json()["chats"]
        assert any(c["id"] == chat_id for c in chats)

    def test_delete_chat(self, client):
        resp = client.post("/api/chats", json={"title": "Temp"})
        chat_id = resp.json()["id"]
        resp = client.delete(f"/api/chats/{chat_id}")
        assert resp.status_code == 200

    def test_rename_chat(self, client):
        resp = client.post("/api/chats", json={"title": "Old"})
        chat_id = resp.json()["id"]
        resp = client.put(f"/api/chats/{chat_id}", json={"title": "New"})
        assert resp.status_code == 200

    def test_get_messages_empty(self, client):
        resp = client.post("/api/chats", json={"title": "Test"})
        chat_id = resp.json()["id"]
        resp = client.get(f"/api/chats/{chat_id}/messages")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []


class TestAskEndpoint:
    def test_ask_with_mocked_graphrag(self, client):
        mock_result = {
            "answer": "Топ-3: Anime1, Anime2, Anime3",
            "cypher": "MATCH (a) RETURN a LIMIT 3",
            "status": "ok",
            "rows": 3,
            "attempts": 1,
            "error": None,
            "model": "gemma4:12b",
            "llm_base_url": "http://localhost:11434/v1",
            "duration_sec": 1.5,
            "cypher_raw": "MATCH (a) RETURN a LIMIT 3",
        }
        with patch.object(main.graphrag, "ask", return_value=mock_result):
            resp = client.post("/api/chats", json={"title": "Test"})
            chat_id = resp.json()["id"]
            resp = client.post(
                f"/api/chats/{chat_id}/ask",
                json={"message": "Топ-3 аниме"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["model"] == "gemma4:12b"
        assert data["duration_sec"] == 1.5

    def test_ask_increments_metrics(self, client):
        mock_result = {
            "answer": "ok", "cypher": "MATCH (a) RETURN a",
            "status": "ok", "rows": 1, "attempts": 1, "error": None,
            "model": "test", "llm_base_url": "test", "duration_sec": 0.1,
            "cypher_raw": "raw",
        }
        # Запоминаем счётчики до запроса
        before = client.get("/metrics").text
        before_total = [l for l in before.splitlines() if "graphrag_requests_total" in l and "# TYPE" not in l][0]
        before_val = int(before_total.split()[1])

        with patch.object(main.graphrag, "ask", return_value=mock_result):
            resp = client.post("/api/chats", json={"title": "Test"})
            chat_id = resp.json()["id"]
            client.post(f"/api/chats/{chat_id}/ask", json={"message": "test"})

        metrics = client.get("/metrics").text
        after_val = int([l for l in metrics.splitlines() if "graphrag_requests_total" in l and "# TYPE" not in l][0].split()[1])
        assert after_val == before_val + 1


class TestLogsEndpoint:
    def test_logs_api(self, client):
        with patch.object(main.db, "get_logs", return_value=[]):
            resp = client.get("/api/logs")
        assert resp.status_code == 200
        assert "logs" in resp.json()