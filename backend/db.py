"""SQLite хранилище: чаты, сообщения, логи Cypher-запросов."""
import sqlite3
import os
from datetime import datetime

_DB_PATH = os.environ.get("GRAPHRAG_DB_PATH", "/data/graphrag.db")


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            question TEXT,
            cypher TEXT,
            status TEXT,
            rows_returned INTEGER,
            error_message TEXT,
            attempts INTEGER,
            created_at TEXT NOT NULL,
            model TEXT,
            llm_base_url TEXT,
            answer TEXT,
            duration_sec REAL,
            cypher_raw TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
        CREATE INDEX IF NOT EXISTS idx_logs_chat ON query_logs(chat_id);
    """)
    # Миграция: добавляем новые колонки, если таблица уже существует без них
    existing = {row[1] for row in conn.execute("PRAGMA table_info(query_logs)").fetchall()}
    for col, decl in [
        ("model", "TEXT"),
        ("llm_base_url", "TEXT"),
        ("answer", "TEXT"),
        ("duration_sec", "REAL"),
        ("cypher_raw", "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE query_logs ADD COLUMN {col} {decl}")
    conn.commit()
    conn.close()


# --- Чаты ---

def create_chat(chat_id: str, title: str):
    conn = _connect()
    conn.execute(
        "INSERT INTO chats (id, title, created_at) VALUES (?, ?, ?)",
        (chat_id, title, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def list_chats() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, title, created_at FROM chats ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_chat(chat_id: str):
    conn = _connect()
    conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()


def rename_chat(chat_id: str, title: str):
    conn = _connect()
    conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
    conn.commit()
    conn.close()


# --- Сообщения ---

def add_message(chat_id: str, role: str, content: str):
    conn = _connect()
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, role, content, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def get_messages(chat_id: str, limit: int = 50) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT ?",
        (chat_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Логи ---

def log_query(chat_id: str, question: str, cypher: str, status: str,
              rows_returned: int = 0, error_message: str|None = None, attempts: int = 1,
              model: str|None = None, llm_base_url: str|None = None, answer: str|None = None,
              duration_sec: float|None = None, cypher_raw: str|None = None):
    conn = _connect()
    conn.execute(
        """INSERT INTO query_logs
           (chat_id, question, cypher, status, rows_returned, error_message, attempts,
            created_at, model, llm_base_url, answer, duration_sec, cypher_raw)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (chat_id, question, cypher, status, rows_returned, error_message, attempts,
         datetime.utcnow().isoformat(), model, llm_base_url, answer, duration_sec, cypher_raw)
    )
    conn.commit()
    conn.close()


def get_logs(limit: int = 100) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """SELECT id, chat_id, question, cypher, status, rows_returned,
                  error_message, attempts, created_at,
                  model, llm_base_url, answer, duration_sec, cypher_raw
           FROM query_logs ORDER BY id DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]