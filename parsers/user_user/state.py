"""Состояние очереди user-user-parser в Neo4j.

Adaptive backoff для юзеров:
  интервал старт 15 дней, шаг +15 при отсутствии изменений.
  При изменении animelist → интервал остаётся прежним.
  При отсутствии изменений → интервал += 15.

Свойства на узлах: check_interval_days, next_check_at.
"""
from __future__ import annotations

import logging

from base_schema import USER_INITIAL_INTERVAL_DAYS, USER_INTERVAL_STEP_DAYS

log = logging.getLogger("user_user_state")


def get_driver():
    from loader import get_driver as _gd
    return _gd()


def select_users_for_refresh(limit: int = 50) -> list[str]:
    """Выбирает active пользователей для refresh по adaptive backoff.

    next_check_at IS NULL (новый) ИЛИ next_check_at <= now.
    """
    with get_driver().session() as session:
        result = session.run("""
            MATCH (u:User {status: "active"})
            WHERE u.next_check_at IS NULL
               OR u.next_check_at <= datetime()
            RETURN u.username AS username, u.next_check_at AS next_check
            ORDER BY u.next_check_at ASC
            LIMIT $limit
        """, limit=limit)

        return [r["username"] for r in result]


def record_user_check(username: str, changed: bool):
    """Обновить next_check_at и check_interval_days после проверки."""
    with get_driver().session() as session:
        session.run("""
            MATCH (u:User {username: $username})
            WITH u, coalesce(u.check_interval_days, $initial) AS interval
            SET u.check_interval_days = CASE WHEN $changed THEN interval ELSE interval + $step END,
                u.next_check_at = datetime() + duration({days: CASE WHEN $changed THEN interval ELSE interval + $step END})
        """, username=username, changed=changed,
           initial=USER_INITIAL_INTERVAL_DAYS, step=USER_INTERVAL_STEP_DAYS)


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

        return {
            "total_users": total_users,
            "active_users": active_users,
            "archived_users": archived_users,
            "total_ratings": total_ratings,
        }