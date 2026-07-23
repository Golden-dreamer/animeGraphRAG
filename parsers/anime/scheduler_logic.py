"""Один цикл scheduler: discover актуальных сезонов + обработка тайтлов.

Цикл:
  1. discover_recent — регистрирует новые тайтлы текущего/следующего/прошлого сезона
  2. Обрабатывает каждый mal_id из списка (фетчит MAL → обновляет Neo4j)
  3. Возвращает управление

Список mal_ids приходит от координатора (как у user-парсеров).
is_paused callback проверяется между элементами.
"""
import logging

import discover
from base_fetcher import PauseRequested
from config import Config
from processing import process_one

log = logging.getLogger("scheduler")

_PROGRESS_EVERY = 50


def run_cycle(mal_ids: list[int], cfg: Config, is_paused=None):
    """Один цикл. mal_ids — список от координатора.
    is_paused — опциональный callable, возвращающий bool.
    Если вернул True — прерываем цикл после текущего элемента.
    """
    log.info("Discover: проверка текущего/следующего/прошлого сезона...")
    added = discover.discover_recent(cfg)
    log.info("Discover завершён: зарегистрировано %d новых тайтлов", added)

    total = len(mal_ids)
    log.info("Цикл: %d тайтлов к обновлению", total)

    if total == 0:
        log.info("Цикл завершён: нет тайтлов")
        return

    processed = 0
    failed = 0
    for i, mal_id in enumerate(mal_ids, 1):
        if is_paused and is_paused():
            log.info("Пауза на %d/%d — цикл прерван", i - 1, total)
            break
        try:
            process_one(mal_id, cfg)
            processed += 1
        except PauseRequested:
            log.info("Kill switch на %d/%d (mal_id=%s) — цикл прерван", i, total, mal_id)
            break
        except Exception as e:
            log.error("mal_id=%s: непредвиденная ошибка: %s", mal_id, e)
            failed += 1
        if i % _PROGRESS_EVERY == 0:
            log.info("Прогресс: %d/%d (%.0f%%)", i, total, i / total * 100)

    log.info("Цикл завершён: обработано %d/%d, ошибок %d",
             processed, total, failed)