"""Тесты db: CRUD chats, messages, log_query с temp SQLite."""
import os
import tempfile

import pytest

import db


@pytest.fixture(autouse=True)
def temp_db():
    """Создаём временную SQLite БД для каждого теста."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        original = os.environ.get("GRAPHRAG_DB_PATH")
        os.environ["GRAPHRAG_DB_PATH"] = db_path
        db._DB_PATH = db_path
        db.init_db()
        yield
        if original is not None:
            os.environ["GRAPHRAG_DB_PATH"] = original
        else:
            os.environ.pop("GRAPHRAG_DB_PATH", None)
        db._DB_PATH = os.environ.get("GRAPHRAG_DB_PATH", "/data/graphrag.db")


class TestInitDb:
    def test_creates_tables(self):
        conn = db._connect()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "chats" in tables
        assert "messages" in tables
        assert "query_logs" in tables

    def test_query_logs_has_enriched_columns(self):
        conn = db._connect()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(query_logs)").fetchall()}
        conn.close()
        assert "model" in cols
        assert "llm_base_url" in cols
        assert "answer" in cols
        assert "duration_sec" in cols
        assert "cypher_raw" in cols


class TestChatCRUD:
    def test_create_and_list(self):
        db.create_chat("chat1", "Test Chat")
        chats = db.list_chats()
        assert len(chats) == 1
        assert chats[0]["id"] == "chat1"
        assert chats[0]["title"] == "Test Chat"

    def test_delete_chat(self):
        db.create_chat("chat1", "Test")
        db.delete_chat("chat1")
        assert db.list_chats() == []

    def test_rename_chat(self):
        db.create_chat("chat1", "Old Name")
        db.rename_chat("chat1", "New Name")
        chats = db.list_chats()
        assert chats[0]["title"] == "New Name"

    def test_multiple_chats_ordered_by_created_at_desc(self):
        db.create_chat("chat1", "First")
        db.create_chat("chat2", "Second")
        chats = db.list_chats()
        # chat2 создан позже — должен быть первым
        assert chats[0]["id"] == "chat2"
        assert chats[1]["id"] == "chat1"


class TestMessages:
    def test_add_and_get_messages(self):
        db.create_chat("chat1", "Test")
        db.add_message("chat1", "user", "Hello")
        db.add_message("chat1", "assistant", "Hi there!")
        msgs = db.get_messages("chat1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["role"] == "assistant"

    def test_get_messages_empty(self):
        db.create_chat("chat1", "Test")
        assert db.get_messages("chat1") == []

    def test_messages_ordered_by_id(self):
        db.create_chat("chat1", "Test")
        db.add_message("chat1", "user", "First")
        db.add_message("chat1", "user", "Second")
        db.add_message("chat1", "user", "Third")
        msgs = db.get_messages("chat1")
        assert msgs[0]["content"] == "First"
        assert msgs[2]["content"] == "Third"


class TestLogQuery:
    def test_log_and_get(self):
        db.create_chat("chat1", "Test")
        db.log_query(
            "chat1", "Какие жанры?", "MATCH (a) RETURN a", "ok",
            rows_returned=5, attempts=1,
            model="gemma4:12b", llm_base_url="http://localhost:11434/v1",
            answer="Action, Comedy", duration_sec=1.5,
            cypher_raw="MATCH (a) RETURN a",
        )
        logs = db.get_logs()
        assert len(logs) == 1
        log = logs[0]
        assert log["question"] == "Какие жанры?"
        assert log["status"] == "ok"
        assert log["rows_returned"] == 5
        assert log["model"] == "gemma4:12b"
        assert log["llm_base_url"] == "http://localhost:11434/v1"
        assert log["answer"] == "Action, Comedy"
        assert log["duration_sec"] == 1.5
        assert log["cypher_raw"] == "MATCH (a) RETURN a"

    def test_log_without_optional_fields(self):
        db.log_query("chat1", "test", "MATCH (n)", "error")
        logs = db.get_logs()
        assert logs[0]["model"] is None
        assert logs[0]["answer"] is None

    def test_logs_ordered_by_id_desc(self):
        db.log_query("c1", "q1", "cypher1", "ok")
        db.log_query("c1", "q2", "cypher2", "error")
        logs = db.get_logs()
        assert logs[0]["question"] == "q2"
        assert logs[1]["question"] == "q1"

    def test_limit(self):
        for i in range(10):
            db.log_query("c1", f"q{i}", "c", "ok")
        assert len(db.get_logs(limit=5)) == 5
        assert len(db.get_logs(limit=100)) == 10