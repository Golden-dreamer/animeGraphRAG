import asyncio
import logging

from fastapi import FastAPI, HTTPException

import db
import fetcher
from config import load_config
from scheduler_logic import run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Anime Parser Control")
cfg = load_config()


@app.on_event("startup")
async def startup():
    db.init_db()
    fetcher.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_files = list(fetcher.CACHE_DIR.glob("*.html"))
    log.info("Кэш: %s, файлов: %d", fetcher.CACHE_DIR, len(cache_files))
    log.info("Лимиты: %.2fs интервал, %d req/%ds, retries=%d",
             fetcher.MIN_INTERVAL_SEC, fetcher.RATE_WINDOW_MAX,
             fetcher.RATE_WINDOW_SEC, fetcher.MAX_RETRIES)
    asyncio.create_task(_scheduler_loop())


async def _scheduler_loop():
    while True:
        try:
            log.info("=== Начало цикла scheduler ===")
            await asyncio.to_thread(run_cycle, cfg)
        except Exception as e:
            log.exception("Ошибка в цикле scheduler: %s", e)
        await asyncio.sleep(cfg.cycle_interval_sec)


@app.post("/refresh/{mal_id}")
def refresh(mal_id: int):
    """Поставить тайтл в 'быструю полосу' — он будет обработан в начале следующего цикла,
    вне очереди, независимо от next_check_at."""
    db.force_refresh(mal_id)
    return {"status": "queued_with_priority", "mal_id": mal_id}


@app.get("/status")
def status():
    return db.get_stats()


@app.get("/failed")
def failed(limit: int = 100):
    """Тайтлы, обработка которых провалилась cfg.max_attempts раз подряд.
    Чтобы дать им ещё один шанс: POST /refresh/{mal_id} (сбрасывает счётчик попыток)
    или POST /failed/retry (сбрасывает все failed-тайтлы сразу)."""
    return {"failed_titles": db.get_failed(limit)}


@app.post("/failed/retry")
def retry_all_failed():
    """Сбросить все failed-тайтлы — дать им ещё один шанс.
    Каждый тайтл получает priority=1, attempts=0, status='pending',
    и будет обработан в начале следующего цикла scheduler.
    """
    failed_list = db.get_failed(limit=10000)
    count = 0
    for item in failed_list:
        db.force_refresh(item["mal_id"])
        count += 1
    log.info("Сброшено %d failed-тайтлов в очередь на переобработку", count)
    return {"reset_count": count, "status": "queued_for_retry"}


@app.get("/cache/stats")
def cache_stats():
    """Статистика файлового кэша API-ответов."""
    return fetcher.get_cache_stats()


@app.post("/cache/clear")
def cache_clear():
    """Очистить весь файловый кэш. Следующие запросы к API будут сделаны заново."""
    removed = fetcher.clear_cache()
    return {"removed_files": removed, "path": str(fetcher.CACHE_DIR)}


@app.get("/config")
def get_config():
    """Текущая конфигурация парсера (все лимиты, интервалы, размеры)."""
    return {
        "cycle_interval_sec": cfg.cycle_interval_sec,
        "batch_size": cfg.batch_size,
        "cache_max_mb": cfg.cache_max_mb,
        "max_attempts": cfg.max_attempts,
        "retry_backoff_minutes": cfg.retry_backoff_minutes,
        "refresh_current_days": cfg.refresh_current_days,
        "refresh_previous_days": cfg.refresh_previous_days,
        "refresh_recent_years": cfg.refresh_recent_years,
        "refresh_recent_days": cfg.refresh_recent_days,
        "api": {
            "base_url": fetcher.BASE_URL,
            "min_interval_sec": fetcher.MIN_INTERVAL_SEC,
            "rate_window_sec": fetcher.RATE_WINDOW_SEC,
            "rate_window_max": fetcher.RATE_WINDOW_MAX,
            "max_retries": fetcher.MAX_RETRIES,
            "retry_base_delay": fetcher.RETRY_BASE_DELAY,
            "retry_max_delay": fetcher.RETRY_MAX_DELAY,
            "http_timeout": fetcher.HTTP_TIMEOUT,
        },
        "cache_dir": str(fetcher.CACHE_DIR),
    }


@app.get("/health")
def health():
    return {"ok": True}