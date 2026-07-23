"""FastAPI координатор парсеров — полностью асинхронный.

Правило: никогда два парсера не работают одновременно.

Координатор — умный: сам запрашивает Neo4j, формирует списки
mal_ids / usernames и передаёт парсерам через /trigger-cycle body.
Парсеры — глупые: просто парсят то, что дали.

Полностью асинхронный: все HTTP-вызовы через httpx.AsyncClient,
все sleep через asyncio.sleep. При /auto/stop или /pause —
asyncio.Task.cancel() мгновенно прерывает любой await.

Парсеры:
  airing-parser (порт 8567) — обновление airing аниме, раз в сутки
  user-anime  (порт 8568) — anime-centric (discovery юзеров из stats)
  user-user   (порт 8569) — user-centric (refresh animelist юзеров)

Эндпоинты:
  GET  /              — статус всех парсеров
  POST /start/anime   — запустить airing-parser
  POST /start/user-anime — запустить user-anime
  POST /start/user-user  — запустить user-user
  POST /pause         — остановить все (мгновенно, cancel auto task)
  POST /auto          — авто-режим (body: skip_airing_today=true)
  POST /auto/stop     — остановить авто-режим (мгновенно, cancel)
  GET  /auto/status   — статус авто-режима
  PUT  /auto/slice     — изменить слайс (сек)
  PUT  /auto/batch-size — изменить размер батча
  PUT  /anime-time     — изменить время запуска airing-parser (HH:MM)
  PUT  /auto/idle-wait — изменить idle wait (сек)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI
from neo4j import GraphDatabase
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coordinator")

ANIME_PARSER_URL = os.environ.get("ANIME_PARSER_URL", "http://airing-parser:8000")
USER_ANIME_URL = os.environ.get("USER_ANIME_URL", "http://user-anime:8000")
USER_USER_URL = os.environ.get("USER_USER_URL", "http://user-user:8000")
ANIME_PARSER_TIME = os.environ.get("ANIME_PARSER_TIME", "03:00")  # HH:MM
USER_SLICE_SEC = int(os.environ.get("COORDINATOR_USER_SLICE_SEC", "1800"))
IDLE_WAIT_SEC = int(os.environ.get("COORDINATOR_IDLE_WAIT_SEC", "300"))
BATCH_SIZE = int(os.environ.get("COORDINATOR_BATCH_SIZE", "5"))

# --- HTTP клиент (async) ---

_http: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=30)
    return _http


# --- Neo4j (sync, оборачиваем в to_thread) ---

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


# --- Select-запросы к Neo4j ---

def _select_anime_never_checked(limit: int) -> list[int]:
    with _get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL
              AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NULL
            RETURN a.mal_id AS mal_id
            ORDER BY a.members DESC
            LIMIT $limit
        """, limit=limit)
        return [r["mal_id"] for r in result]


def _select_anime_for_backoff(limit: int) -> list[int]:
    with _get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL
              AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NOT NULL
            WITH a,
                 CASE WHEN a.members <> a.stats_total THEN 1 ELSE 0 END AS changed
            WHERE changed = 1
               OR a.next_check_at IS NULL
               OR a.next_check_at <= datetime()
            RETURN a.mal_id AS mal_id
            ORDER BY changed DESC, a.next_check_at ASC
            LIMIT $limit
        """, limit=limit)
        return [r["mal_id"] for r in result]


def _select_users_for_refresh(limit: int) -> list[str]:
    with _get_driver().session() as session:
        result = session.run("""
            MATCH (u:User {status: "active"})
            WHERE u.next_check_at IS NULL
               OR u.next_check_at <= datetime()
            RETURN u.username AS username
            ORDER BY u.next_check_at ASC
            LIMIT $limit
        """, limit=limit)
        return [r["username"] for r in result]


def _select_due_anime() -> list[int]:
    with _get_driver().session() as session:
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IN $due_statuses
               OR a.mal_status IS NULL
            RETURN a.mal_id AS mal_id,
                   CASE
                     WHEN a.mal_status IS NULL THEN 0
                     WHEN a.mal_status = 'Currently Airing' THEN 1
                     WHEN a.mal_status = 'Not yet aired' THEN 2
                     ELSE 3
                   END AS priority
            ORDER BY priority, a.mal_id
        """, due_statuses=["Currently Airing", "Not yet aired"])
        return [r["mal_id"] for r in result]


def _next_anime_check_seconds() -> float | None:
    with _get_driver().session() as session:
        never = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NULL
            RETURN count(a) AS c
        """).single()["c"]
        if never > 0:
            return 0.0
        result = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NOT NULL
              AND a.next_check_at IS NOT NULL
            RETURN min(a.next_check_at) AS earliest
        """).single()
        if not result or result["earliest"] is None:
            return None
        delta = result["earliest"].native - datetime.now()
        return max(delta.total_seconds(), 1.0)


def _next_user_check_seconds() -> float | None:
    with _get_driver().session() as session:
        never = session.run("""
            MATCH (u:User {status: "active"})
            WHERE u.next_check_at IS NULL
            RETURN count(u) AS c
        """).single()["c"]
        if never > 0:
            return 0.0
        result = session.run("""
            MATCH (u:User {status: "active"})
            WHERE u.next_check_at IS NOT NULL
            RETURN min(u.next_check_at) AS earliest
        """).single()
        if not result or result["earliest"] is None:
            return None
        delta = result["earliest"].native - datetime.now()
        return max(delta.total_seconds(), 1.0)


def _count_anime_due() -> int:
    with _get_driver().session() as session:
        never = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NULL
            RETURN count(a) AS c
        """).single()["c"]
        backoff = session.run("""
            MATCH (a:Anime)
            WHERE a.mal_status IS NOT NULL AND a.mal_url IS NOT NULL
              AND a.summary_stats_at IS NOT NULL
              AND (a.next_check_at IS NULL OR a.next_check_at <= datetime())
            RETURN count(a) AS c
        """).single()["c"]
        return never + backoff


def _count_users_due() -> int:
    with _get_driver().session() as session:
        return session.run("""
            MATCH (u:User {status: "active"})
            WHERE u.next_check_at IS NULL OR u.next_check_at <= datetime()
            RETURN count(u) AS c
        """).single()["c"]


def _build_anime_stats_list_sync() -> list[int]:
    ids = _select_anime_never_checked(limit=BATCH_SIZE)
    if not ids:
        ids = _select_anime_for_backoff(limit=BATCH_SIZE)
    log.info("user-anime: найдено %d аниме для проверки", len(ids))
    return ids


# --- Async HTTP к парсерам ---

async def _apost(url: str, path: str, json_body: dict | None = None):
    http = await _get_http()
    try:
        return await http.post(f"{url}{path}", json=json_body)
    except Exception as e:
        log.warning("POST %s%s: %s", url, path, e)
        return None


async def _aget(url: str, path: str) -> dict:
    http = await _get_http()
    try:
        resp = await http.get(f"{url}{path}")
        return resp.json()
    except Exception as e:
        log.warning("GET %s%s: %s", url, path, e)
        return {}


async def _ais_running(url: str) -> bool:
    return (await _aget(url, "/cycle-running")).get("running", False)


async def _atrigger(url: str, name: str, body: dict | None = None):
    """Запустить цикл парсера. Сначала resume (сброс kill), потом trigger."""
    await _apost(url, "/resume")
    resp = await _apost(url, "/trigger-cycle", json_body=body)
    if resp is not None and resp.status_code == 409:
        log.info("%s: уже работает", name)
    else:
        count = 0
        if body:
            count = len(body.get("mal_ids") or body.get("usernames") or [])
        log.info("%s: цикл запущен (%d элементов)", name, count)


async def _apause(url: str, name: str):
    await _apost(url, "/pause")
    log.info("%s: pause отправлен", name)


async def _await_stopped(url: str, name: str, timeout_sec: int = 60):
    """Ждать пока парсер остановится. Poll каждые 2 сек."""
    waited = 0
    while await _ais_running(url):
        if waited >= timeout_sec:
            log.warning("%s: не остановился за %d сек — продолжаем", name, timeout_sec)
            return
        await asyncio.sleep(2)
        waited += 2
    log.info("%s: остановлен (%.0f сек)", name, waited)


async def _await_cycle_done(url: str, name: str, timeout_sec: int,
                            stop_event: asyncio.Event):
    """Ждать завершения цикла. БЕЗ POLLING.

    Парсер шлёт POST /cycle-done когда цикл завершён → _cycle_done_event.
    Мы ждём Event ИЛИ timeout (что раньше). Без GET /cycle-running каждые 5 сек.
    Прогресс-лог каждые 30 сек.
    """
    _cycle_done_event.clear()
    waited = 0
    PROGRESS_INTERVAL = 30

    while waited < timeout_sec:
        if stop_event.is_set():
            log.info("%s: STOP — KILL SWITCH", name)
            await _apause(url, name)
            await _await_stopped(url, name)
            raise _StopRequested()

        # Ждём cycle-done event (5 сек, потом проверяем stop/timeout)
        try:
            await asyncio.wait_for(_cycle_done_event.wait(), timeout=5)
            log.info("%s: цикл завершён (%d сек)", name, waited)
            _cycle_done_event.clear()
            return
        except asyncio.TimeoutError:
            pass

        waited += 5

        if waited % PROGRESS_INTERVAL == 0:
            remaining = timeout_sec - waited
            log.info("%s: работаем (%d сек прошло, осталось %d сек)",
                     name, waited, remaining)

    # Timeout истёк — kill switch
    log.info("%s: слайс %d сек истёк — KILL SWITCH", name, timeout_sec)
    await _apause(url, name)
    await _await_stopped(url, name)


class _StopRequested(Exception):
    """Сигнал к немедленной остановке — из _await_cycle_done."""


# --- Время airing ---

def _is_anime_time() -> bool:
    global _last_anime_run
    now = datetime.now()
    hour, minute = map(int, ANIME_PARSER_TIME.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if _last_anime_run and _last_anime_run.date() >= now.date():
        return False
    return now >= target


def _seconds_until_airing() -> float:
    now = datetime.now()
    hour, minute = map(int, ANIME_PARSER_TIME.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if _last_anime_run and _last_anime_run.date() >= now.date():
        target = target + timedelta(days=1)
    if now >= target and not (_last_anime_run and _last_anime_run.date() >= now.date()):
        return 0.0
    return max((target - now).total_seconds(), 1.0)


# --- Управление ---

app = FastAPI(title="Parser Coordinator")

_auto_running = False
_auto_task: asyncio.Task | None = None
_stop_event: asyncio.Event = asyncio.Event()
_last_anime_run: datetime | None = None

# Event: парсер сообщил о завершении цикла (POST /cycle-done)
_cycle_done_event: asyncio.Event = asyncio.Event()


@app.post("/cycle-done")
async def cycle_done(msg: dict):
    """Парсер сообщил о завершении цикла."""
    parser = msg.get("parser", "?")
    log.info("%s: cycle-done получен", parser)
    _cycle_done_event.set()
    return {"status": "ok"}


def _check_stop():
    """Проверить stop_event. Бросить _StopRequested если выставлен."""
    if _stop_event.is_set():
        raise _StopRequested()


async def _pause_all_others(except_url: str):
    """Остановить все парсеры кроме указанного. Ждать реальной остановки."""
    others = []
    if ANIME_PARSER_URL != except_url:
        others.append((ANIME_PARSER_URL, "airing-parser"))
    if USER_ANIME_URL != except_url:
        others.append((USER_ANIME_URL, "user-anime"))
    if USER_USER_URL != except_url:
        others.append((USER_USER_URL, "user-user"))
    for url, name in others:
        if await _ais_running(url):
            await _apause(url, name)
            await _await_stopped(url, name)


async def _smart_wait(name: str, next_check_fn, idle_fallback_sec: int,
                      stop_event: asyncio.Event):
    """Ждать до ближайшего события. Прерывается stop_event."""
    due_secs = await asyncio.to_thread(next_check_fn)
    airing_secs = _seconds_until_airing()
    if due_secs is None:
        due_secs = idle_fallback_sec
    secs = min(due_secs, airing_secs)
    log.info("%s: нет работы — спим %.0f сек (due=%.0f, airing=%.0f)",
             name, secs, due_secs, airing_secs)
    # Спим короткими кусками для response на stop
    waited = 0.0
    chunk = 5.0
    while waited < secs:
        if stop_event.is_set():
            raise _StopRequested()
        await asyncio.sleep(min(chunk, secs - waited))
        waited += chunk


async def _run_anime_with_priority(stop_event: asyncio.Event):
    """Остановить user-парсеры, запустить airing-parser, дождаться."""
    global _last_anime_run
    log.info("=== airing-parser: подготовка к запуску ===")
    await _pause_all_others(ANIME_PARSER_URL)
    mal_ids = await asyncio.to_thread(_select_due_anime)
    log.info("airing-parser: найдено %d аниме для обновления", len(mal_ids))
    await _atrigger(ANIME_PARSER_URL, "airing-parser", body={"mal_ids": mal_ids})
    # airing без жёсткого timeout — но stop_event прервать может
    try:
        await _await_cycle_done(ANIME_PARSER_URL, "airing-parser",
                                timeout_sec=86400, stop_event=stop_event)
    except _StopRequested:
        await _apause(ANIME_PARSER_URL, "airing-parser")
        await _await_stopped(ANIME_PARSER_URL, "airing-parser")
        raise
    _last_anime_run = datetime.now()
    log.info("airing-parser завершён, last_anime_run=%s", _last_anime_run)


async def _run_user_anime_slice(stop_event: asyncio.Event):
    """Запустить user-anime батчами до истечения слайса."""
    log.info("=== user-anime: старт слайса (%d сек) ===", USER_SLICE_SEC)
    await _pause_all_others(USER_ANIME_URL)
    total_due = await asyncio.to_thread(_count_anime_due)
    start = time.time()
    processed = 0
    batch_num = 0
    end_time = start + USER_SLICE_SEC
    log.info("user-anime: всего %d аниме требует проверки, слайс до %s",
             total_due, datetime.fromtimestamp(end_time).strftime("%H:%M:%S"))

    while True:
        _check_stop()
        if _is_anime_time():
            log.info("user-anime: время airing — остановка после %d/%d (батч #%d)",
                     processed, total_due, batch_num)
            return
        elapsed = time.time() - start
        if elapsed >= USER_SLICE_SEC:
            log.info("user-anime: слайс истёк в %s — обработано %d/%d (батч #%d)",
                     datetime.now().strftime("%H:%M:%S"), processed, total_due, batch_num)
            return

        mal_ids = await asyncio.to_thread(_build_anime_stats_list_sync)
        if not mal_ids:
            await _smart_wait("user-anime", _next_anime_check_seconds,
                              IDLE_WAIT_SEC, stop_event)
            return

        batch_num += 1
        log.info("user-anime: батч #%d (%d элементов, обработано %d/%d)",
                 batch_num, len(mal_ids), processed, total_due)
        await _atrigger(USER_ANIME_URL, "user-anime", body={"mal_ids": mal_ids})
        remaining = int(end_time - time.time())
        if remaining <= 0:
            remaining = 1
        await _await_cycle_done(USER_ANIME_URL, "user-anime",
                                timeout_sec=remaining, stop_event=stop_event)
        processed += len(mal_ids)


async def _run_user_user_slice(stop_event: asyncio.Event):
    """Запустить user-user батчами до истечения слайса."""
    log.info("=== user-user: старт слайса (%d сек) ===", USER_SLICE_SEC)
    await _pause_all_others(USER_USER_URL)
    total_due = await asyncio.to_thread(_count_users_due)
    start = time.time()
    processed = 0
    batch_num = 0
    end_time = start + USER_SLICE_SEC
    log.info("user-user: всего %d юзеров требует refresh, слайс до %s",
             total_due, datetime.fromtimestamp(end_time).strftime("%H:%M:%S"))

    while True:
        _check_stop()
        if _is_anime_time():
            log.info("user-user: время airing — остановка после %d/%d (батч #%d)",
                     processed, total_due, batch_num)
            return
        elapsed = time.time() - start
        if elapsed >= USER_SLICE_SEC:
            log.info("user-user: слайс истёк в %s — обработано %d/%d (батч #%d)",
                     datetime.now().strftime("%H:%M:%S"), processed, total_due, batch_num)
            return

        usernames = await asyncio.to_thread(
            lambda: _select_users_for_refresh(limit=BATCH_SIZE))
        if not usernames:
            await _smart_wait("user-user", _next_user_check_seconds,
                              IDLE_WAIT_SEC, stop_event)
            return

        batch_num += 1
        log.info("user-user: батч #%d (%d элементов, обработано %d/%d)",
                 batch_num, len(usernames), processed, total_due)
        await _atrigger(USER_USER_URL, "user-user", body={"usernames": usernames})
        remaining = int(end_time - time.time())
        if remaining <= 0:
            remaining = 1
        await _await_cycle_done(USER_USER_URL, "user-user",
                                timeout_sec=remaining, stop_event=stop_event)
        processed += len(usernames)


async def _auto_loop():
    """Главный цикл авто-режима. Полностью async.

    stop_event прерывает любой await мгновенно — _StopRequested
    пробрасывается наверх и цикл завершается.
    """
    log.info("Авто-цикл запущен (батчи по %d, слайс %d сек, airing в %s)",
             BATCH_SIZE, USER_SLICE_SEC, ANIME_PARSER_TIME)

    global _auto_running
    try:
        while _auto_running:
            # user-anime
            await _run_user_anime_slice(_stop_event)

            if not _auto_running:
                break

            # airing-parser если время пришло
            if _is_anime_time():
                log.info("Авто-цикл: время airing-parser")
                await _run_anime_with_priority(_stop_event)

            if not _auto_running:
                break

            # user-user
            await _run_user_user_slice(_stop_event)

            if not _auto_running:
                break

            # airing-parser если время пришло
            if _is_anime_time():
                log.info("Авто-цикл: время airing-parser")
                await _run_anime_with_priority(_stop_event)

    except _StopRequested:
        log.info("Авто-цикл: STOP получен — немедленная остановка")
    except asyncio.CancelledError:
        log.info("Авто-цикл: task cancelled")
    except Exception as e:
        log.exception("Авто-цикл: ошибка: %s", e)
    finally:
        _auto_running = False
        log.info("Авто-цикл остановлен")


# --- Эндпоинты ---

@app.get("/")
async def status():
    return {
        "anime_parser": {"url": ANIME_PARSER_URL, "running": await _ais_running(ANIME_PARSER_URL)},
        "user_anime": {"url": USER_ANIME_URL, "running": await _ais_running(USER_ANIME_URL)},
        "user_user": {"url": USER_USER_URL, "running": await _ais_running(USER_USER_URL)},
        "auto_mode": _auto_running,
        "user_slice_sec": USER_SLICE_SEC,
        "batch_size": BATCH_SIZE,
        "anime_parser_time": ANIME_PARSER_TIME,
        "idle_wait_sec": IDLE_WAIT_SEC,
        "last_anime_run": _last_anime_run.isoformat() if _last_anime_run else None,
    }


@app.post("/start/anime")
async def start_anime():
    """Остановить user-парсеры, запустить airing-parser."""
    log.info("Ручной запуск: airing-parser")
    await _pause_all_others(ANIME_PARSER_URL)
    mal_ids = await asyncio.to_thread(_select_due_anime)
    log.info("airing-parser: найдено %d аниме для обновления", len(mal_ids))
    await _atrigger(ANIME_PARSER_URL, "airing-parser", body={"mal_ids": mal_ids})
    return {"status": "anime started", "items": len(mal_ids)}


@app.post("/start/user-anime")
async def start_user_anime():
    """Остановить другие, запустить user-anime со списком из БД."""
    log.info("Ручной запуск: user-anime")
    await _pause_all_others(USER_ANIME_URL)
    mal_ids = await asyncio.to_thread(_build_anime_stats_list_sync)
    if not mal_ids:
        log.warning("user-anime: нет аниме для проверки")
        return {"status": "no work", "message": "нет аниме для проверки"}
    await _atrigger(USER_ANIME_URL, "user-anime", body={"mal_ids": mal_ids})
    return {"status": "user-anime started", "items": len(mal_ids)}


@app.post("/start/user-user")
async def start_user_user():
    """Остановить другие, запустить user-user со списком из БД."""
    log.info("Ручной запуск: user-user")
    await _pause_all_others(USER_USER_URL)
    usernames = await asyncio.to_thread(
        lambda: _select_users_for_refresh(limit=BATCH_SIZE))
    if not usernames:
        log.warning("user-user: нет пользователей для refresh")
        return {"status": "no work", "message": "нет пользователей для refresh"}
    log.info("user-user: найдено %d пользователей для refresh", len(usernames))
    await _atrigger(USER_USER_URL, "user-user", body={"usernames": usernames})
    return {"status": "user-user started", "items": len(usernames)}


@app.post("/pause")
async def pause_all():
    """Остановить ВСЕ парсеры мгновенно + остановить авто-режим."""
    global _auto_running
    # Сигнал stop для auto-loop
    _stop_event.set()
    # Cancel auto task если работает
    if _auto_task and not _auto_task.done():
        _auto_task.cancel()
        try:
            await _auto_task
        except (asyncio.CancelledError, _StopRequested, Exception):
            pass
    _auto_running = False
    _stop_event.clear()
    # Pause все парсеры
    await _apause(ANIME_PARSER_URL, "airing-parser")
    await _apause(USER_ANIME_URL, "user-anime")
    await _apause(USER_USER_URL, "user-user")
    log.info("Все парсеры остановлены, авто-режим выключен")
    return {"status": "all paused", "auto_mode": False}


class SliceUpdate(BaseModel):
    slice_sec: int


@app.put("/auto/slice")
def set_slice(update: SliceUpdate):
    global USER_SLICE_SEC
    USER_SLICE_SEC = update.slice_sec
    log.info("Слайс изменён: %d сек", USER_SLICE_SEC)
    return {"slice_sec": USER_SLICE_SEC}


class BatchSizeUpdate(BaseModel):
    batch_size: int


@app.put("/auto/batch-size")
def set_batch_size(update: BatchSizeUpdate):
    global BATCH_SIZE
    BATCH_SIZE = update.batch_size
    log.info("Размер батча изменён: %d", BATCH_SIZE)
    return {"batch_size": BATCH_SIZE}


class TimeUpdate(BaseModel):
    time: str  # HH:MM


@app.put("/anime-time")
def set_anime_time(update: TimeUpdate):
    global ANIME_PARSER_TIME
    ANIME_PARSER_TIME = update.time
    log.info("Время airing изменено: %s", ANIME_PARSER_TIME)
    return {"anime_parser_time": ANIME_PARSER_TIME}


class IdleWaitUpdate(BaseModel):
    idle_wait_sec: int


@app.put("/auto/idle-wait")
def set_idle_wait(update: IdleWaitUpdate):
    global IDLE_WAIT_SEC
    IDLE_WAIT_SEC = update.idle_wait_sec
    log.info("Idle wait изменён: %d сек", IDLE_WAIT_SEC)
    return {"idle_wait_sec": IDLE_WAIT_SEC}


@app.get("/auto/status")
async def auto_status():
    return {
        "auto_mode": _auto_running,
        "user_slice_sec": USER_SLICE_SEC,
        "batch_size": BATCH_SIZE,
        "anime_parser_time": ANIME_PARSER_TIME,
        "idle_wait_sec": IDLE_WAIT_SEC,
        "last_anime_run": _last_anime_run.isoformat() if _last_anime_run else None,
    }


class AutoStartRequest(BaseModel):
    skip_airing_today: bool = False


@app.post("/auto")
async def start_auto(req: AutoStartRequest | None = None):
    """Запустить авто-режим."""
    global _auto_running, _auto_task, _last_anime_run
    if _auto_running:
        return {"status": "already running"}
    _stop_event.clear()
    if req and req.skip_airing_today:
        _last_anime_run = datetime.now()
        log.info("Авто-режим: airing пропускается сегодня (skip_airing_today)")
    _auto_running = True
    _auto_task = asyncio.create_task(_auto_loop())
    log.info("Авто-режим запущен")
    return {"status": "auto started"}


@app.post("/auto/stop")
async def stop_auto():
    """Остановить авто-режим мгновенно — cancel task + pause все парсеры."""
    global _auto_running
    _stop_event.set()
    if _auto_task and not _auto_task.done():
        _auto_task.cancel()
        try:
            await _auto_task
        except (asyncio.CancelledError, _StopRequested, Exception):
            pass
    _auto_running = False
    _stop_event.clear()
    # Pause все парсеры
    await _apause(ANIME_PARSER_URL, "airing-parser")
    await _apause(USER_ANIME_URL, "user-anime")
    await _apause(USER_USER_URL, "user-user")
    log.info("Авто-режим остановлен, все парсеры на паузе")
    return {"status": "auto stopped", "auto_mode": False}


@app.on_event("shutdown")
async def shutdown():
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)