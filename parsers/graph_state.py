"""Состояние очереди парсинга в Neo4j.

Без SQLite, без retry-логики.
Логика:
  - "Не обработан" = узел :Anime с title IS NULL (stub)
  - Актуальные = mal_status IN ['Currently Airing', 'Not yet aired']
  - Scheduler каждый цикл обновляет все актуальные + stub'ы
  - /refresh вызывает process_one напрямую
  - Прогресс bootstrap — в текстовом файле (bootstrap_progress.txt)
"""
from __future__ import annotations

from loader import get_driver


def upsert_anime_stub(mal_id: int, year: int, season: str) -> int:
    """Регистрирует тайтл в графе, если его ещё нет.
    Возвращает 1 если создан, 0 если уже существовал."""
    with get_driver().session() as session:
        # Проверяем существование до MERGE — mal_id проиндексирован (constraint).
        existed = session.run(
            "MATCH (a:Anime {mal_id: $mal_id}) RETURN count(a) AS c",
            mal_id=mal_id,
        ).single()["c"]
        if existed:
            return 0
        session.run("""
            MERGE (a:Anime {mal_id: $mal_id})
            ON CREATE SET a.year = $year, a.season = $season
        """, mal_id=mal_id, year=year, season=season)
        return 1


def select_due_anime(limit: int | None = None) -> list[int]:
    """Актуальные тайтлы для scheduler.

    Критерии (объединение):
      1. mal_status IN ['Currently Airing', 'Not yet aired'] — airing тайтлы,
         у которых меняются эпизоды/оценки/статус (обновляем каждый цикл)
      2. title IS NULL — stub'ы, никогда не обработанные (у них mal_status
         тоже NULL, поэтому критерий 1 их не ловит). Сcheduler их обработает,
         получит mal_status с MAL, и дальше они попадут под критерий 1 или
         выпадут (если Finished Airing — больше не актуальны).

    Порядок: сначала stub'ы (нужно получить данные), потом airing, потом upcoming.
    Если limit=None — возвращает все без лимита."""
    with get_driver().session() as session:
        query = """
            MATCH (a:Anime)
            WHERE a.mal_status IN ['Currently Airing', 'Not yet aired']
               OR a.title IS NULL
            RETURN a.mal_id AS mal_id,
                   CASE
                     WHEN a.title IS NULL THEN 0
                     WHEN a.mal_status = 'Currently Airing' THEN 1
                     WHEN a.mal_status = 'Not yet aired' THEN 2
                     ELSE 3
                   END AS priority
            ORDER BY priority, a.mal_id
        """
        params = {}
        if limit is not None:
            query += " LIMIT $limit"
            params["limit"] = limit
        result = session.run(query, **params)
        return [r["mal_id"] for r in result]


def select_due_for_season(year: int, season: str, limit: int = 100) -> list[int]:
    """Для bootstrap: необработанные тайтлы конкретного сезона (title IS NULL)."""
    with get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.year = $year AND a.season = $season
              AND a.title IS NULL
            RETURN a.mal_id AS mal_id
            ORDER BY a.mal_id
            LIMIT $limit
        """, year=year, season=season, limit=limit)
        return [r["mal_id"] for r in result]


def mark_parsed(mal_id: int):
    """Но-op: title записывается в loader.upsert_anime, ничего не делаем."""
    # title IS NOT NULL после upsert_anime — этого достаточно.
    pass


def get_stats() -> dict:
    """Статистика для /status endpoint."""
    with get_driver().session() as session:
        total = session.run("MATCH (a:Anime) RETURN count(a) AS c").single()["c"]
        parsed = session.run(
            "MATCH (a:Anime) WHERE a.title IS NOT NULL RETURN count(a) AS c"
        ).single()["c"]
        stubs = session.run(
            "MATCH (a:Anime) WHERE a.title IS NULL RETURN count(a) AS c"
        ).single()["c"]
        airing = session.run(
            "MATCH (a:Anime) WHERE a.mal_status = 'Currently Airing' RETURN count(a) AS c"
        ).single()["c"]
        upcoming = session.run(
            "MATCH (a:Anime) WHERE a.mal_status = 'Not yet aired' RETURN count(a) AS c"
        ).single()["c"]
        return {
            "total_anime": total,
            "parsed": parsed,
            "unprocessed_stubs": stubs,
            "currently_airing": airing,
            "not_yet_aired": upcoming,
        }


def refresh_anime(mal_id: int):
    """Принудительное обновление — вызывает process_one напрямую.
    Здесь ничего не делаем, app.py вызывает process_one напрямую."""
    pass