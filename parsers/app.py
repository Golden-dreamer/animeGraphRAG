import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import fetcher
import graph_state
from config import load_config
from processing import process_one
from scheduler_logic import run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Anime Parser Control")
cfg = load_config()


@app.on_event("startup")
async def startup():
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
        # Спим короткими интервалами, чтобы изменение cycle_interval_sec
        # через PUT /schedule применялось быстро, а не ждало старый sleep.
        interval = cfg.cycle_interval_sec
        next_run = datetime.now() + timedelta(seconds=interval)
        log.info("Ожидание следующего цикла: %d сек (в %s)",
                 interval, next_run.strftime("%Y-%m-%d %H:%M:%S"))
        elapsed = 0
        while elapsed < interval:
            await asyncio.sleep(5)
            elapsed += 5
            if cfg.cycle_interval_sec != interval:
                log.info("cycle_interval_sec изменён во время ожидания: %d → %d — перезапуск цикла",
                         interval, cfg.cycle_interval_sec)
                break


# --- Управление циклами scheduler ---

_manual_cycle_running = False


@app.post("/trigger-cycle")
async def trigger_cycle():
    """Запустить цикл scheduler прямо сейчас, не дожидаясь таймера."""
    global _manual_cycle_running
    if _manual_cycle_running:
        raise HTTPException(status_code=409, detail="Цикл уже выполняется")

    _manual_cycle_running = True
    asyncio.create_task(_run_manual_cycle())
    return {"status": "triggered", "message": "Цикл запущен в фоне"}


async def _run_manual_cycle():
    global _manual_cycle_running
    try:
        log.info("=== Ручной запуск цикла (trigger-cycle) ===")
        await asyncio.to_thread(run_cycle, cfg)
        log.info("=== Ручной цикл завершён ===")
    except Exception as e:
        log.exception("Ошибка в ручном цикле: %s", e)
    finally:
        _manual_cycle_running = False


class ScheduleUpdate(BaseModel):
    cycle_interval_sec: int


@app.put("/schedule")
def update_schedule(update: ScheduleUpdate):
    """Изменить интервал автоматического цикла scheduler."""
    if update.cycle_interval_sec < 60:
        raise HTTPException(status_code=400,
                            detail="cycle_interval_sec должен быть >= 60")
    old = cfg.cycle_interval_sec
    cfg.cycle_interval_sec = update.cycle_interval_sec
    next_run = datetime.now() + timedelta(seconds=update.cycle_interval_sec)
    log.info("cycle_interval_sec изменён: %d → %d — следующий цикл в %s",
             old, update.cycle_interval_sec,
             next_run.strftime("%Y-%m-%d %H:%M:%S"))
    return {
        "old_cycle_interval_sec": old,
        "new_cycle_interval_sec": update.cycle_interval_sec,
        "next_cycle_at": next_run.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "Действует до перезапуска контейнера. Для постоянного изменения отредактируйте config.yaml",
    }


# --- Эндпоинты ---

@app.post("/refresh/{mal_id}")
async def refresh(mal_id: int):
    """Принудительно обновить один тайтл прямо сейчас."""
    await asyncio.to_thread(process_one, mal_id, cfg)
    return {"status": "refreshed", "mal_id": mal_id}


@app.get("/status")
def status():
    return graph_state.get_stats()


@app.get("/stubs")
def stubs(limit: int = 100):
    """Неполные узлы — Anime с title IS NULL (не обработаны)."""
    from loader import get_driver
    with get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime) WHERE a.title IS NULL
            RETURN a.mal_id AS mal_id, a.year AS year, a.season AS season
            ORDER BY a.mal_id LIMIT $limit
        """, limit=limit)
        return {"stubs": [dict(r) for r in result]}


@app.get("/config")
def get_config():
    """Текущая конфигурация парсера (все лимиты, интервалы)."""
    return {
        "cycle_interval_sec": cfg.cycle_interval_sec,
        "batch_size": cfg.batch_size,
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
    }


@app.get("/health")
def health():
    return {"ok": True}