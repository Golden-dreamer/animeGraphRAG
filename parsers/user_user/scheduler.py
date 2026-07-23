"""Цикл user-user-parser: refresh animelist пользователей.

Получает список usernames от координатора. Для каждого:
  1. Фетчит animelist (JSON) → парсит
  2. Записывает RATED edge'и в Neo4j (upsert + cleanup stale)
  3. Записывает adaptive backoff (record_user_check)

Если профиль удалён (404) — архивирует юзера.
Pause проверяется между элементами.
is_paused callback передаётся из BaseParser (self.is_paused).
"""
from __future__ import annotations

import logging
from typing import Callable

import fetcher
import loader
import state
from base_fetcher import PauseRequested
from config import Config
from scraper import parse_animelist

log = logging.getLogger("user_user_scheduler")

_PROGRESS_EVERY = 10


def run_cycle(usernames: list[str], cfg: Config, is_paused: Callable[[], bool] | None = None) -> list[dict]:
    """Обработать список usernames. Вернуть результаты."""
    if not usernames:
        log.warning("user-user: нет пользователей для refresh")
        return []

    total = len(usernames)
    stats = state.get_user_stats()
    log.info("user-user: refresh %d пользователей (всего: %d active, %d archived, %d оценок)",
             total, stats.get("active_users", 0), stats.get("archived_users", 0),
             stats.get("total_ratings", 0))

    results = []
    processed = 0
    for i, username in enumerate(usernames, 1):
        if is_paused and is_paused():
            log.info("user-user: пауза на %d/%d", i - 1, total)
            break
        try:
            count = process_one(username, cfg)
            results.append({"username": username, "ratings_updated": count})
            processed += 1
        except PauseRequested:
            log.info("user-user: kill switch на %d/%d (username=%s)", i, total, username)
            break
        except Exception as e:
            log.error("user=%s: ошибка: %s: %s", username, type(e).__name__, e)
        if i % _PROGRESS_EVERY == 0 or i == total:
            stats = state.get_user_stats()
            log.info("user-user: %d/%d (всего: %d active, %d archived, %d оценок)",
                     i, total,
                     stats.get("active_users", 0),
                     stats.get("archived_users", 0),
                     stats.get("total_ratings", 0))

    log.info("user-user завершён: обработано %d/%d", processed, total)
    return results


def process_one(username: str, cfg: Config) -> int:
    """Фетч animelist → обновить RATED. Adaptive backoff: record_user_check."""
    raw = fetcher.fetch_animelist(username, cfg)

    if raw is None:
        log.info("user=%s: профиль не найден (404) — архивируем", username)
        loader.archive_user(username)
        return 0

    entries = parse_animelist(raw)
    changed = _check_user_changed(username, len(entries))

    if entries:
        loader.upsert_animelist(username, entries)

    loader.update_user_last_seen(username)
    state.record_user_check(username, changed)

    log.info("user=%s: обновлено %d оценок (changed=%s)", username, len(entries), changed)
    return len(entries)


def _check_user_changed(username: str, fresh_count: int) -> bool:
    """Сравнивает количество RATED edge'ей юзера с свежим animelist."""
    with state.get_driver().session() as session:
        result = session.run(
            "MATCH (u:User {username: $username})-[:RATED]->() RETURN count(*) AS c",
            username=username,
        ).single()
        if not result:
            return True
        return result["c"] != fresh_count