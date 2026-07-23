"""Цикл user-anime-parser: проверка stats-страниц аниме.

Получает список mal_ids от координатора. Для каждого:
  1. Фетчит stats-страницу → парсит Summary Stats, Score Stats, пользователей
  2. Записывает данные в Neo4j (upsert)
  3. Записывает adaptive backoff (record_anime_check)

Pause проверяется между элементами — доработать текущий и остановиться.
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
from scraper import parse_stats_page, has_next_page, parse_summary_stats, parse_score_stats

log = logging.getLogger("user_anime_scheduler")

_PROGRESS_EVERY = 10

# In-memory resume cache: ОДИН mal_id -\u003e page_num.
# При pause запоминаем где остановились. При resume — продолжаем с этой страницы.
# Если координатор прислал другой mal_id — assert (значит логика сломалась).
_resume_cache: dict[int, int] = {}


def run_cycle(mal_ids: list[int], cfg: Config, is_paused: Callable[[], bool] | None = None) -> list[dict]:
    """Обработать список mal_ids. Вернуть результаты."""
    if not mal_ids:
        log.warning("user-anime: нет аниме для проверки")
        return []

    # Проверка resume cache: если есть закэшированный mal_id,
    # он ДОЛЖЕН совпадать с первым в батче от координатора.
    if _resume_cache:
        cached_id = next(iter(_resume_cache))
        assert cached_id == mal_ids[0], (
            f"resume cache mismatch: cached={cached_id}, coordinator sent={mal_ids[0]}. "
            f"Координатор должен выдать тот же недособранный mal_id."
        )
        log.info("user-anime: resume mal_id=%s со страницы %d", cached_id, _resume_cache[cached_id])

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
            resume_page = _resume_cache.pop(mal_id, 1)
            users_found = process_one(mal_id, cfg, is_paused=is_paused, resume_page=resume_page)
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

    # Если cache не пустой после цикла — значит был pause и mal_id не доработал
    # (или доработал но cache не очистился — это нормально, process_one чистит сам)
    if _resume_cache:
        cached_id = next(iter(_resume_cache))
        log.info("user-anime: resume cache сохранён: mal_id=%s -\u003e page %d",
                 cached_id, _resume_cache[cached_id])

    log.info("user-anime завершён: обработано %d/%d", processed, total)
    return results


def process_one(mal_id: int, cfg: Config, is_paused: Callable[[], bool] | None = None,
                resume_page: int = 1) -> int:
    """Проверить Summary Stats → собрать пользователей из stats-страниц.

    resume_page — продолжить с этой страницы (при resume после pause).
    """
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

    if resume_page > 1:
        log.info("mal_id=%s: resume со страницы %d", mal_id, resume_page)

    # Если resume со страницы N — первые N-1 страниц уже собраны в прошлый раз
    page_num = resume_page
    offset = (page_num - 1) * 75

    # Если resume не со 1-й — сразу фетчим нужную страницу
    if resume_page > 1:
        html = fetcher.fetch_stats_page(mal_id, "_", offset, cfg)

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
        # Сохраняем страницу для resume
        _resume_cache.clear()
        _resume_cache[mal_id] = page_num
        log.info("mal_id=%s: собрано %d пользователей (paused на стр %d, resume cache сохранён)",
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