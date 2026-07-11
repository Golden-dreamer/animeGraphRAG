"""Один цикл scheduler: discover актуальных сезонов + обработка airing-тайтлов.

Таймер в app.py срабатывает раз в cycle_interval_sec и вызывает run_cycle().
Цикл:
  1. discover_recent — регистрирует новые тайтлы текущего/следующего/прошлого сезона
  2. Собирает ВСЕ актуальные тайтлы (Currently Airing + Not yet aired) одним запросом
  3. Обрабатывает каждый (фетчит MAL → обновляет Neo4j)
  4. Возвращает управление — app.py ждёт cycle_interval_sec до следующего цикла
"""
import logging
from datetime import datetime, timedelta

import graph_state
import discover
from config import Config
from processing import process_one

log = logging.getLogger("scheduler")

_PROGRESS_EVERY = 50


def run_cycle(cfg: Config):
    log.info("Discover: проверка текущего/следующего/прошлого сезона...")
    added = discover.discover_recent(cfg)
    log.info("Discover завершён: зарегистрировано %d новых тайтлов", added)

    # Собираем все актуальные тайтлы одним запросом — без while True,
    # иначе обработанные тайтлы возвращаются снова (mal_status не меняется).
    mal_ids = graph_state.select_due_anime()
    total = len(mal_ids)
    log.info("Цикл: %d актуальных тайтлов к обновлению", total)

    if total == 0:
        log.info("Цикл завершён: нет актуальных тайтлов")
        return

    processed = 0
    failed = 0
    for i, mal_id in enumerate(mal_ids, 1):
        try:
            process_one(mal_id, cfg)
            processed += 1
        except Exception as e:
            # process_one сам ловит ошибки, но на всякий случай — safety net
            log.error("mal_id=%s: непредвиденная ошибка: %s", mal_id, e)
            failed += 1
        if i % _PROGRESS_EVERY == 0:
            log.info("Прогресс: %d/%d (%.0f%%)", i, total, i / total * 100)

    log.info("Цикл завершён: обработано %d/%d, ошибок %d",
             processed, total, failed)
    log.info("Следующий цикл через %d сек (в %s)",
             cfg.cycle_interval_sec,
             (datetime.now() + timedelta(seconds=cfg.cycle_interval_sec)).strftime("%Y-%m-%d %H:%M:%S"))