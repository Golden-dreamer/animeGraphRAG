import logging

import db
import discover
import fetcher
from config import Config
from processing import process_one

log = logging.getLogger("scheduler")


def run_cycle(cfg: Config):
    fetcher.cleanup_cache_if_over_limit(cfg.cache_max_mb)
    discover.discover_recent(cfg)

    mal_ids = db.select_due_anime(limit=cfg.batch_size)
    log.info("Цикл: к обработке %d тайтлов", len(mal_ids))
    for mal_id in mal_ids:
        process_one(mal_id, cfg)
