"""Цикл user-anime-parser: проверка stats-страниц аниме.

Получает список mal_ids от координатора. Для каждого:
  1. Фетчит stats-страницу → парсит Summary Stats, Score Stats, пользователей
  2. Записывает данные в Neo4j (upsert)
  3. Записывает adaptive backoff (record_anime_check)

Pause проверяется между элементами — доработать текущий и остановиться.
is_paused callback передаётся из BaseParser (self.is_paused).

Resume cache убран: stats-страницы динамические, при pause аниме
обрабатывается заново в следующем цикле.
"""
from __future__ import annotations

import logging
from typing import Callable

import fetcher
import loader
import state
from base_fetcher import PauseRequested
from config import Config
from scraper import parse_stats_page, has_next_page, parse_summary_stats, parse_score_stats

log = logging.getLogger("user_anime_scheduler")

_PROGRESS_EVERY = 10


def run_cycle(mal_ids: list[int], cfg: Config, is_paused: Callable[[], bool] | None = None) -> list[dict]:
    """Обработать список mal_ids. Вернуть результаты."""
    if not mal_ids:
        log.warning("user-anime: нет аниме для проверки")
        return []

    total = len(mal_ids)
    stats = state.get_user_stats()
    log.info("user-anime: проверка %d аниме (всего: обработано %d, осталось %d)",
             total, stats.get("anime_stats_checked", 0), stats.get("anime_stats_pending", 0))

    results = []
    processed = 0
    for i, mal_id in enumerate(mal_ids, 1):
        if is_paused and is_paused():
            log.info("user-anime: пауза на %d/%d", i - 1, total)
            break
        try:
            users_found = process_one(mal_id, cfg, is_paused=is_paused)
            results.append({"mal_id": mal_id, "users_found": users_found})
            processed += 1
        except PauseRequested:
            log.info("user-anime: kill switch на %d/%d (mal_id=%s)", i, total, mal_id)
            break
        except Exception as e:
            log.error("mal_id=%s: ошибка: %s: %s", mal_id, type(e).__name__, e)
        if i % _PROGRESS_EVERY == 0 or i == total:
            stats = state.get_user_stats()
            log.info("user-anime: %d/%d (всего: %d обработано, %d осталось, %d юзеров, %d оценок)",
                     i, total,
                     stats.get("anime_stats_checked", 0),
                     stats.get("anime_stats_pending", 0),
                     stats.get("total_users", 0),
                     stats.get("total_ratings", 0))

    log.info("user-anime завершён: обработано %d/%d", processed, total)
    return results


def process_one(mal_id: int, cfg: Config, is_paused: Callable[[], bool] | None = None) -> int:
    """Проверить Summary Stats → собрать пользователей из stats-страниц."""
    # Первая страница нужна всегда — из неё берём summary/scores
    html = fetcher.fetch_stats_page(mal_id, "_", 0, cfg)
    if html is None:
        log.info("mal_id=%s: stats-страница не найдена (404)", mal_id)
        return 0

    summary = parse_summary_stats(html)
    scores = parse_score_stats(html)

    members_changed = _check_members_changed(mal_id, summary)

    total_users = 0
    all_usernames: set[str] = set()
    was_paused = False

    page_num = 1
    offset = 0

    try:
        while html is not None:
            if is_paused and is_paused():
                log.info("mal_id=%s: пауза на странице %d", mal_id, page_num)
                was_paused = True
                break

            users = parse_stats_page(html)
            if not users:
                break

            loader.upsert_stats_batch(mal_id, users)
            all_usernames.update(u['username'] for u in users)
            total_users += len(users)

            if not has_next_page(html):
                break

            page_num += 1
            offset = (page_num - 1) * 75
            html = fetcher.fetch_stats_page(mal_id, "_", offset, cfg)
    except PauseRequested:
        log.info("mal_id=%s: kill switch на странице %d", mal_id, page_num)
        was_paused = True

    if was_paused:
        log.info("mal_id=%s: собрано %d пользователей (paused на стр %d)",
                 mal_id, total_users, page_num)
        return total_users

    # Полная обработка — пишем всё
    if summary:
        loader.upsert_summary_stats(mal_id, summary)
        log.info("mal_id=%s: Summary Stats — total=%s", mal_id, summary.get('total'))
    if scores:
        loader.upsert_score_stats(mal_id, scores)

    state.record_anime_check(mal_id, members_changed)

    log.info("mal_id=%s: собрано %d пользователей с %d страниц (changed=%s)",
             mal_id, total_users, page_num, members_changed)
    return total_users


def _check_members_changed(mal_id: int, summary: dict | None) -> bool:
    if not summary or summary.get('total') is None:
        return True
    with state.get_driver().session() as session:
        result = session.run(
            "MATCH (a:Anime {mal_id: $mal_id}) RETURN a.members AS members",
            mal_id=mal_id,
        ).single()
        if not result:
            return True
        return result["members"] != summary.get('total')