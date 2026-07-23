"""Базовый класс для парсеров MyAnimeList.

Инкапсулирует общую структуру FastAPI-приложения парсера:
  - /trigger-cycle  — запуск одного цикла (с опциональным body)
  - /pause          — остановка после текущего элемента
  - /resume         — снятие паузы
  - /cycle-running  — статус цикла
  - /health         — health check

Конкретный парсер наследуется и реализует:
  - process_items(self, items) — обработать список, вернуть результаты
  - get_status(self)           — вернуть статистику для /status
  - parser_name (property)     — имя для логов

Парсер пассивен — запуск через координатор (POST /trigger-cycle).
Pause проверяется между элементами — доработать текущий и остановиться.

Для аварийного восстановления: парсер пишет данные и метаданные
(backoff) в БД поэлементно. Если парсер упал — уже обработанные
элементы не вернутся в очередь (next_check_at обновлён).
"""
from __future__ import annotations

import abc
import asyncio
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from base_fetcher import trigger_pause, clear_pause

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("base_parser")


class TriggerRequest(BaseModel):
    """Body для /trigger-cycle. Поля опциональны — парсер берёт то что ему нужно."""
    mal_ids: list[int] | None = None
    usernames: list[str] | None = None


class BaseParser(abc.ABC):
    """Абстрактный базовый класс для парсеров.

    Создаёт FastAPI app со стандартными эндпоинтами. Конкретный парсер
    реализует process_items() и get_status().
    """

    def __init__(self):
        self.app = FastAPI(title=self.parser_name)
        self._cycle_running = False
        self._paused = False
        self._setup_routes()

    # --- абстрактные методы ---

    @property
    @abc.abstractmethod
    def parser_name(self) -> str:
        """Имя парсера для логов и FastAPI title."""
        ...

    @abc.abstractmethod
    def process_items(self, items: list[Any]) -> list[dict]:
        """Обработать список элементов. Вернуть список результатов.

        Вызывается в фоновом потоке. Между элементами проверяется pause.
        Каждый элемент должен быть обработан атомарно — данные и метаданные
        пишутся в БД сразу после обработки, до перехода к следующему.
        """
        ...

    def get_status(self) -> dict:
        """Статистика для /status. Переопределить при необходимости."""
        return {"parser": self.parser_name, "running": self._cycle_running}

    # --- pause / resume ---

    def set_paused(self, value: bool):
        self._paused = value
        if value:
            trigger_pause()
        else:
            clear_pause()

    def is_paused(self) -> bool:
        return self._paused

    # --- маршруты ---

    def _setup_routes(self):
        app = self.app

        @app.on_event("startup")
        async def startup():
            log.info("%s: пассивен — запуск через координатор (POST /trigger-cycle)", self.parser_name)

        @app.post("/trigger-cycle")
        async def trigger_cycle(req: TriggerRequest | None = None):
            if self._cycle_running:
                raise HTTPException(status_code=409, detail="Цикл уже выполняется")
            items = self._extract_items(req)
            if not items:
                log.warning("%s: trigger-cycle с пустым списком — игнорируем", self.parser_name)
                return {"status": "no work", "items_count": 0}
            self._cycle_running = True
            asyncio.create_task(self._run_cycle(items))
            return {"status": "triggered", "items_count": len(items)}

        @app.post("/pause")
        async def pause():
            self.set_paused(True)
            return {"status": "paused"}

        @app.post("/resume")
        async def resume():
            self.set_paused(False)
            return {"status": "resumed"}

        @app.get("/cycle-running")
        def cycle_running():
            return {"running": self._cycle_running}

        @app.get("/status")
        def status():
            return self.get_status()

        @app.get("/health")
        def health():
            return {"ok": True}

    def _extract_items(self, req: TriggerRequest | None) -> list[Any]:
        """Извлечь список элементов из запроса.
        Переопределить если парсер использует другие поля."""
        if req is None:
            return []
        if req.mal_ids is not None:
            return req.mal_ids
        if req.usernames is not None:
            return req.usernames
        return []

    async def _run_cycle(self, items: list[Any]):
        try:
            log.info("=== %s: начало цикла (%d элементов) ===", self.parser_name, len(items))
            await asyncio.to_thread(self.process_items, items)
            log.info("=== %s: цикл завершён ===", self.parser_name)
        except Exception as e:
            log.exception("%s: ошибка в цикле: %s", self.parser_name, e)
        finally:
            self._cycle_running = False
            # Сбрасываем kill — на случай если pause был выставлен
            # но cycle завершился (например, PauseRequested)
            clear_pause()
            self._paused = False
            # Уведомляем координатор — цикл завершён
            await self._notify_coordinator_done()

    async def _notify_coordinator_done(self):
        """POST /cycle-done координатору — чтобы не polling."""
        url = os.environ.get("COORDINATOR_URL", "http://coordinator:8000")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{url}/cycle-done",
                                  json={"parser": self.parser_name})
        except Exception:
            pass  # координатор может быть недоступен — не критично