"""User-user-parser — юзер-центрированный парсер MyAnimeList.

Парсит animelist пользователей (/animelist/{user}/load.json) —
обновляет RATED edge'и, статусы, оценки. Запуск через координатор.

Наследует BaseParser. Принимает usernames через /trigger-cycle body.
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
log = logging.getLogger("user_user_app")


class UserUserParser(BaseParser):
    """Юзер-центрированный парсер: animelist → RATED edge'и."""

    def __init__(self):
        self.cfg = load_config()
        super().__init__()

    @property
    def parser_name(self) -> str:
        return "user-user-parser"

    def process_items(self, items: list[str]) -> list[dict]:
        return scheduler.run_cycle(items, self.cfg, is_paused=self.is_paused)

    def get_status(self) -> dict:
        return state.get_user_stats()

    def _extract_items(self, req: TriggerRequest | None) -> list:
        if req is None:
            return []
        return req.usernames or []

    def _setup_routes(self):
        super()._setup_routes()
        app = self.app

        @app.on_event("startup")
        async def startup():
            log.info("Лимиты: %.2fs интервал, %d req/%ds, retries=%d",
                     self.cfg.min_interval_sec, self.cfg.rate_window_max,
                     self.cfg.rate_window_sec, self.cfg.max_retries)
            loader.ensure_constraints()

        @app.post("/refresh-user/{username}")
        async def refresh_user(username: str):
            count = await asyncio.to_thread(scheduler.process_one, username, self.cfg)
            return {"status": "ok", "username": username, "ratings_updated": count}


parser = UserUserParser()
app = parser.app