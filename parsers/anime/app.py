"""Airing-parser — обновление airing/upcoming аниме с MyAnimeList.

Наследует BaseParser. Pause останавливает цикл после текущего элемента.
Запуск через координатор (POST /trigger-cycle) — принимает mal_ids,
как и остальные парсеры.
"""
import asyncio
import logging

import fetcher
import graph_state
from base_parser import BaseParser
from config import load_config
from processing import process_one
from scheduler_logic import run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")


class AiringParser(BaseParser):
    """Парсер airing/upcoming аниме."""

    def __init__(self):
        self.cfg = load_config()
        super().__init__()

    @property
    def parser_name(self) -> str:
        return "airing-parser"

    def process_items(self, items: list[int]) -> list[dict]:
        """Цикл обработки аниме. items — mal_ids от координатора."""
        run_cycle(items, self.cfg, is_paused=self.is_paused)
        return []

    def get_status(self) -> dict:
        return graph_state.get_stats()

    def _setup_routes(self):
        super()._setup_routes()
        app = self.app

        @app.on_event("startup")
        async def startup():
            log.info("Лимиты: %.2fs интервал, %d req/%ds, retries=%d",
                     fetcher.MIN_INTERVAL_SEC, fetcher.RATE_WINDOW_MAX,
                     fetcher.RATE_WINDOW_SEC, fetcher.MAX_RETRIES)

        @app.post("/refresh/{mal_id}")
        async def refresh(mal_id: int):
            await asyncio.to_thread(process_one, mal_id, self.cfg)
            return {"status": "refreshed", "mal_id": mal_id}

        @app.get("/stubs")
        def stubs(limit: int = 100):
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
            return {
                "cycle_interval_sec": self.cfg.cycle_interval_sec,
                "batch_size": self.cfg.batch_size,
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


parser = AiringParser()
app = parser.app