import logging

import db
import discover
import fetcher
from config import Config
from processing import process_one

log = logging.getLogger("scheduler")

# Размер порции для одного запроса к БД. Scheduler обрабатывает ВСЕ due-тайтлы
# за цикл, но достаёт их порциями чтобы не грузить миллион строк за раз.
# Реальный лимит скорости — это Jikan API rate limiter в fetcher.py.
_DB_BATCH = 100


def run_cycle(cfg: Config):
    fetcher.cleanup_cache_if_over_limit(cfg.cache_max_mb)
    discover.discover_recent(cfg)

    total_processed = 0
    total_failed = 0
    while True:
        mal_ids = db.select_due_anime(limit=_DB_BATCH)
        if not mal_ids:
            break
        log.info("Цикл: к обработке %d тайтлов (всего за цикл: %d)",
                 len(mal_ids), total_processed)
        for mal_id in mal_ids:
            try:
                process_one(mal_id, cfg)
                total_processed += 1
            except Exception as e:
                # process_one не должен бросать, но на всякий случай
                log.error("mal_id=%s: непредвиденная ошибка: %s", mal_id, e)
                total_failed += 1

    log.info("Цикл завершён: обработано %d, ошибок %d", total_processed, total_failed)