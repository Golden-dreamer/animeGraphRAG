"""Состояние очереди user-anime-parser в Neo4j.

Adaptive backoff для аниме:
  интервал старт 10 дней, шаг +10 при отсутствии изменений.
  При изменении members → интервал остаётся прежним.
  При отсутствии изменений → интервал += 10.

Свойства на узлах: check_interval_days, next_check_at.
"""
from __future__ import annotations

import logging

from base_schema import ANIME_INITIAL_INTERVAL_DAYS, ANIME_INTERVAL_STEP_DAYS

log = logging.getLogger("user_anime_state")


def get_driver():
    from loader import get_driver as _gd
    return _gd()


def select_anime_never_checked(limit: int = 50) -> list[dict]:
    """Аниме, которые никогда не проверяли (summary_stats_at IS NULL)."""
    with get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL
              AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NULL
            RETURN a.mal_id AS mal_id, a.members AS members
            ORDER BY a.members DESC
            LIMIT $limit
        """, limit=limit)

        return [{"mal_id": r["mal_id"], "slug": "_"} for r in result]


def select_anime_for_backoff(limit: int = 50) -> list[dict]:
    """Аниме, у которых next_check_at прошла или изменился members."""
    with get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL
              AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NOT NULL
            WITH a,
                 CASE WHEN a.members <> a.stats_total THEN 1 ELSE 0 END AS changed
            WHERE changed = 1
               OR a.next_check_at IS NULL
               OR a.next_check_at <= datetime()
            RETURN a.mal_id AS mal_id, a.members AS members, changed AS changed
            ORDER BY changed DESC, a.next_check_at ASC
            LIMIT $limit
        """, limit=limit)

        return [{"mal_id": r["mal_id"], "slug": "_"} for r in result]


def record_anime_check(mal_id: int, members_changed: bool):
    """Обновить next_check_at и check_interval_days после проверки."""
    with get_driver().session() as session:
        session.run("""
            MATCH (a:Anime {mal_id: $mal_id})
            WITH a, coalesce(a.check_interval_days, $initial) AS interval
            SET a.check_interval_days = CASE WHEN $changed THEN interval ELSE interval + $step END,
                a.next_check_at = datetime() + duration({days: CASE WHEN $changed THEN interval ELSE interval + $step END})
        """, mal_id=mal_id, changed=members_changed,
           initial=ANIME_INITIAL_INTERVAL_DAYS, step=ANIME_INTERVAL_STEP_DAYS)


def get_user_stats() -> dict:
    with get_driver().session() as session:
        total_users = session.run("MATCH (u:User) RETURN count(u) AS c").single()["c"]
        active_users = session.run(
            'MATCH (u:User {status: "active"}) RETURN count(u) AS c'
        ).single()["c"]
        archived_users = session.run(
            'MATCH (u:User {status: "archived"}) RETURN count(u) AS c'
        ).single()["c"]
        total_ratings = session.run(
            "MATCH ()-[r:RATED]->() RETURN count(r) AS c"
        ).single()["c"]
        anime_stats_checked = session.run(
            "MATCH (a:Anime) WHERE a.summary_stats_at IS NOT NULL RETURN count(a) AS c"
        ).single()["c"]
        anime_stats_pending = session.run(
            "MATCH (a:Anime) WHERE a.mal_status IS NOT NULL "
            "AND a.summary_stats_at IS NULL RETURN count(a) AS c"
        ).single()["c"]

        return {
            "total_users": total_users,
            "active_users": active_users,
            "archived_users": archived_users,
            "total_ratings": total_ratings,
            "anime_stats_checked": anime_stats_checked,
            "anime_stats_pending": anime_stats_pending,
        }