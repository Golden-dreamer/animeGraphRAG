"""User-anime-parser — аниме-центрированный парсер пользователей MyAnimeList.

Парсит stats-страницы аниме (/anime/{id}/stats) — собирает недавно
активных пользователей и их оценки. Запуск через координатор.

Наследует BaseParser. Принимает mal_ids через /trigger-cycle body.
"""
from __future__ import annotations

import asyncio
import logging

from base_parser import BaseParser, TriggerRequest

import loader
import scheduler
import state
from config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("user_anime_app")


class UserAnimeParser(BaseParser):
    """Аниме-центрированный парсер: stats-страницы → пользователи."""

    def __init__(self):
        self.cfg = load_config()
        super().__init__()

    @property
    def parser_name(self) -> str:
        return "user-anime-parser"

    def process_items(self, items: list[int]) -> list[dict]:
        return scheduler.run_cycle(items, self.cfg, is_paused=self.is_paused)

    def get_status(self) -> dict:
        return state.get_user_stats()

    def _extract_items(self, req: TriggerRequest | None) -> list:
        if req is None:
            return []
        return req.mal_ids or []

    def _setup_routes(self):
        super()._setup_routes()
        app = self.app

        @app.on_event("startup")
        async def startup():
            log.info("Лимиты: %.2fs интервал, %d req/%ds, retries=%d",
                     self.cfg.min_interval_sec, self.cfg.rate_window_max,
                     self.cfg.rate_window_sec, self.cfg.max_retries)
            loader.ensure_constraints()

        @app.post("/scan-anime/{mal_id}")
        async def scan_anime(mal_id: int):
            count = await asyncio.to_thread(scheduler.process_one, mal_id, self.cfg)
            return {"status": "ok", "mal_id": mal_id, "users_found": count}


parser = UserAnimeParser()
app = parser.app