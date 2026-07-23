"""Состояние очереди парсинга в Neo4j.

Без SQLite, без retry-логики.
Логика:
  - "Не обработан" = узел :Anime с mal_status IS NULL (stub)
  - Актуальные = mal_status IN DUE_STATUSES (см. schema.py)
  - Scheduler каждый цикл обновляет все актуальные + stub'ы
  - /refresh вызывает process_one напрямую
  - Прогресс bootstrap — в текстовом файле (bootstrap_progress.txt)
"""
from __future__ import annotations

from loader import get_driver
from base_schema import DUE_STATUSES, AnimeStatus


def upsert_anime_stub(mal_id: int, year: int, season: str) -> int:
    """Регистрирует тайтл в графе, если его ещё нет.
    Возвращает 1 если создан, 0 если уже существовал.

    Если узел уже существует, но без year/season — дозаполняет их,
    чтобы bootstrap мог найти stub по сезону.
    """
    with get_driver().session() as session:
        existed = session.run(
            "MATCH (a:Anime {mal_id: $mal_id}) RETURN count(a) AS c",
            mal_id=mal_id,
        ).single()["c"]
        if existed:
            session.run("""
                MATCH (a:Anime {mal_id: $mal_id})
                SET a.year = coalesce(a.year, $year),
                    a.season = coalesce(a.season, $season)
            """, mal_id=mal_id, year=year, season=season)
            return 0
        session.run("""
            MERGE (a:Anime {mal_id: $mal_id})
            ON CREATE SET a.year = $year, a.season = $season
        """, mal_id=mal_id, year=year, season=season)
        return 1


def select_due_anime(limit: int | None = None) -> list[int]:
    """Актуальные тайтлы для scheduler.

    Критерии (объединение):
      1. mal_status IN DUE_STATUSES — airing/upcoming тайтлы, у которых
         меняются эпизоды/оценки/статус (обновляем каждый цикл)
      2. mal_status IS NULL — необработанные stub'ы (узлы, созданные через
         discover или _link_related, но ещё не отпарсенные process_one).
         После обработки mal_status заполнится и тайтл выпадет из очереди
         (или останется, если airing/upcoming).

    Порядок: сначала stub'ы (нужно получить данные), потом airing, потом upcoming.
    Если limit=None — возвращает все без лимита."""
    airing = AnimeStatus.AIRING.value
    upcoming = AnimeStatus.NOT_YET_AIRED.value
    with get_driver().session() as session:
        query = """
            MATCH (a:Anime)
            WHERE a.mal_status IN $due_statuses
               OR a.mal_status IS NULL
            RETURN a.mal_id AS mal_id,
                   CASE
                     WHEN a.mal_status IS NULL THEN 0
                     WHEN a.mal_status = $airing THEN 1
                     WHEN a.mal_status = $upcoming THEN 2
                     ELSE 3
                   END AS priority
            ORDER BY priority, a.mal_id
        """
        params = {"due_statuses": list(DUE_STATUSES), "airing": airing, "upcoming": upcoming}
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
            "MATCH (a:Anime) WHERE a.mal_status = $s RETURN count(a) AS c",
            s=AnimeStatus.AIRING.value,
        ).single()["c"]
        upcoming = session.run(
            "MATCH (a:Anime) WHERE a.mal_status = $s RETURN count(a) AS c",
            s=AnimeStatus.NOT_YET_AIRED.value,
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