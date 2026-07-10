import asyncio
import logging

from fastapi import FastAPI, HTTPException

import db
from config import load_config
from scheduler_logic import run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Anime Parser Control")
cfg = load_config()


@app.on_event("startup")
async def startup():
    db.init_db()
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


@app.get("/health")
def health():
    return {"ok": True}
