import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/app/data/state.db")

FAR_FUTURE = "9999-01-01T00:00:00+00:00"  # "никогда не обновлять автоматически"
EPOCH = "1970-01-01T00:00:00+00:00"       # "нужно обработать прямо сейчас"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")  # чтобы FastAPI и scheduler могли писать одновременно
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anime_progress (
                mal_id          INTEGER PRIMARY KEY,
                year            INTEGER,
                season          TEXT,
                mal_status      TEXT,
                priority        INTEGER NOT NULL DEFAULT 0,
                last_parsed_at  TEXT,
                next_check_at   TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
                attempts        INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'pending',
                last_error      TEXT
            )
        """)
        # миграция для БД, созданных до появления attempts/status/last_error
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(anime_progress)").fetchall()}
        if "attempts" not in existing_cols:
            conn.execute("ALTER TABLE anime_progress ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "status" not in existing_cols:
            conn.execute("ALTER TABLE anime_progress ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "last_error" not in existing_cols:
            conn.execute("ALTER TABLE anime_progress ADD COLUMN last_error TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seasons_bootstrapped (
                year          INTEGER,
                season        TEXT,
                completed_at  TEXT,
                PRIMARY KEY (year, season)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_due ON anime_progress(priority DESC, next_check_at ASC)")


def upsert_anime_stub(mal_id: int, year: int, season: str):
    """Регистрирует тайтл в очереди, если его там ещё нет. Не трогает уже существующие записи."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO anime_progress (mal_id, year, season, next_check_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mal_id) DO NOTHING
        """, (mal_id, year, season, EPOCH))


def select_due_anime(limit: int):
    """Для scheduler.py: приоритетные и просроченные тайтлы, вне привязки к сезону."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT mal_id FROM anime_progress
            WHERE next_check_at <= ? AND status != 'failed'
            ORDER BY priority DESC, next_check_at ASC
            LIMIT ?
        """, (now_iso(), limit)).fetchall()
        return [r["mal_id"] for r in rows]


def select_due_for_season(year: int, season: str, limit: int):
    """Для bootstrap.py: все ещё не обработанные тайтлы конкретного сезона."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT mal_id FROM anime_progress
            WHERE year = ? AND season = ? AND next_check_at <= ? AND status != 'failed'
            ORDER BY mal_id
            LIMIT ?
        """, (year, season, now_iso(), limit)).fetchall()
        return [r["mal_id"] for r in rows]


def mark_parsed(mal_id: int, mal_status: str, next_check_at: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE anime_progress
            SET mal_status = ?, last_parsed_at = ?, next_check_at = ?,
                priority = 0, attempts = 0, status = 'ok', last_error = NULL
            WHERE mal_id = ?
        """, (mal_status, now_iso(), next_check_at, mal_id))


def mark_failed(mal_id: int, error_message: str, max_attempts: int, retry_backoff_minutes: int):
    """При ошибке фетча/парсинга: несколько коротких повторов, затем статус 'failed'.
    'failed' не участвует в автоматических due-выборках (чтобы не блокировать
    завершение сезона в bootstrap), но виден через GET /failed и db.get_failed().
    """
    from datetime import timedelta
    with get_conn() as conn:
        row = conn.execute("SELECT attempts FROM anime_progress WHERE mal_id = ?", (mal_id,)).fetchone()
        attempts = (row["attempts"] if row else 0) + 1

        if attempts >= max_attempts:
            next_check_at = FAR_FUTURE
            status = "failed"
        else:
            next_check_at = (datetime.now(timezone.utc) + timedelta(minutes=retry_backoff_minutes)).isoformat()
            status = "pending"

        conn.execute("""
            UPDATE anime_progress
            SET next_check_at = ?, priority = 0, attempts = ?, status = ?, last_error = ?
            WHERE mal_id = ?
        """, (next_check_at, attempts, status, error_message[:500], mal_id))


def force_refresh(mal_id: int):
    """'Быстрая полоса': сбрасывает счётчик ошибок, тайтл обработается в начале следующего цикла."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO anime_progress (mal_id, priority, next_check_at, attempts, status, last_error)
            VALUES (?, 1, ?, 0, 'pending', NULL)
            ON CONFLICT(mal_id) DO UPDATE
            SET priority = 1, next_check_at = ?, attempts = 0, status = 'pending', last_error = NULL
        """, (mal_id, EPOCH, EPOCH))


def get_failed(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT mal_id, year, season, attempts, last_error, next_check_at
            FROM anime_progress WHERE status = 'failed'
            ORDER BY mal_id LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def season_already_bootstrapped(year: int, season: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM seasons_bootstrapped WHERE year=? AND season=?", (year, season)
        ).fetchone()
        return row is not None


def mark_season_bootstrapped(year: int, season: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO seasons_bootstrapped (year, season, completed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(year, season) DO NOTHING
        """, (year, season, now_iso()))


def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM anime_progress").fetchone()["c"]
        parsed = conn.execute("SELECT COUNT(*) c FROM anime_progress WHERE last_parsed_at IS NOT NULL").fetchone()["c"]
        due_now = conn.execute("SELECT COUNT(*) c FROM anime_progress WHERE next_check_at <= ?", (now_iso(),)).fetchone()["c"]
        pending_priority = conn.execute("SELECT COUNT(*) c FROM anime_progress WHERE priority > 0").fetchone()["c"]
        seasons_done = conn.execute("SELECT COUNT(*) c FROM seasons_bootstrapped").fetchone()["c"]
        failed = conn.execute("SELECT COUNT(*) c FROM anime_progress WHERE status = 'failed'").fetchone()["c"]
        return {
            "total_titles_known": total,
            "titles_parsed_at_least_once": parsed,
            "due_right_now": due_now,
            "forced_priority_pending": pending_priority,
            "seasons_bootstrapped": seasons_done,
            "permanently_failed": failed,
        }
