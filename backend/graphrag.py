"""GraphRAG engine: question → LLM генерирует Cypher → Neo4j → LLM формулирует ответ.

Two-step pipeline:
  1. text→Cypher: LLM получает схему графа + вопрос, генерирует Cypher-запрос
  2. result→answer: LLM получает результат из Neo4j, формулирует ответ на русском

Самопроверка: если Cypher упал с синтаксической ошибкой — бэкенд возвращает
ошибку LLM, та пробует снова (до 3 попыток).

Streaming: ask_stream() — генератор, yielding SSE events для фронтенда.
Think-режим: при think=True используется Ollama native API (/api/chat),
который поддерживает separate reasoning/content streams.
"""
import json
import logging
import os
import re
import time
from typing import Generator

import requests
from neo4j import GraphDatabase

import db
import session_config

log = logging.getLogger("graphrag")

# --- Конфигурация Neo4j (env, не меняется через UI) ---

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

MAX_CYPHER_ATTEMPTS = 3

# --- Промпты (defaults, могут быть переопределены через session config) ---

GRAPH_SCHEMA = """\
Узлы:
  :Anime        — mal_id, title, title_original, title_english, title_synonyms, title_japanese,
                  poster_url, mal_url, type, episodes, mal_status, aired, premiered, broadcast,
                  source, duration, rating, score, scored_by, ranked, popularity, members,
                  favorites, synopsis, background, year, season
  :Genre        — name
  :Studio       — name
  :Producer     — name
  :Character    — mal_id, name, url
  :Person       — mal_id, name, url
  :ExternalLink — url, name
  :StreamingPlatform — name
  :Manga        — mal_id, title

Связи:
  (Anime)-[:HAS_GENRE]->(Genre)
  (Anime)-[:HAS_THEME]->(Genre)
  (Anime)-[:HAS_DEMOGRAPHIC]->(Genre)
  (Anime)-[:PRODUCED_BY]->(Studio)
  (Anime)-[:PRODUCER_OF]->(Producer)
  (Anime)-[:LICENSED_BY]->(Producer)
  (Anime)-[:RELATED_TO {relation, target_type}]->(Anime | Manga)
  (Anime)-[:HAS_CHARACTER {role}]->(Character)
  (Anime)-[:STAFF {roles}]->(Person)
  (Person)-[:VOICE_ACTED {language, anime_id}]->(Character)
  (Anime)-[:STREAMING_ON {url, available}]->(StreamingPlatform)
  (Anime)-[:AVAILABLE_AT]->(ExternalLink)
  (Anime)-[:HAS_RESOURCE]->(ExternalLink)

Индексы: Anime.mal_id, Person.mal_id, Character.mal_id, Manga.mal_id,
Genre.name, Studio.name, Producer.name — UNIQUE CONSTRAINT.
Anime.mal_status — INDEX.

Особенности:
  :Anime.title — алиас title_original, отображается в Neo4j Browser.
  title IS NULL = тайтл зарегистрирован, но не обработан (stub).
  Person и Character уникальны по mal_id, не по имени (могут быть однофамильцы).
  STAFF.roles — список строк (director, producer, script, key animation, ...).
  Anime.year — число, Anime.season — lowercase (winter/spring/summer/fall).
"""

DEFAULT_CYPHER_PROMPT = f"""\
Ты — эксперт по Cypher-запросам к Neo4j. Тебе задаёт вопрос пользователь на русском.
Твоя задача — составить Cypher-запрос к графу MyAnimeList и вернуть ТОЛЬКО запрос,
без объяснений, без markdown-блоков, без лишнего текста.

Схема графа:
{GRAPH_SCHEMA}

Правила:
1. Возвращай ТОЛЬКО валидный Cypher. Никаких ```cypher блоков, никаких комментариев.
2. ВАЖНО: направление связей! Используй <-[:REL]- для обхода от Person к Anime:
   - (Anime)-[:STAFF]->(Person)      правильно: MATCH (a:Anime)-[:STAFF]->(p:Person)
   - все проекты Person:              MATCH (a:Anime)-[:STAFF]->(p:Person {{mal_id: X}})
   - НЕ пиши (p:Person)-[:STAFF]->(a:Anime) — это не вернёт результатов!
   - (Anime)-[:HAS_CHARACTER]->(Character)
   - (Person)-[:VOICE_ACTED]->(Character)
   - (Anime)-[:PRODUCED_BY]->(Studio)
3. Названия аниме в БД на английском/ромадзи (Shingeki no Kyojin, Attack on Titan).
   НЕ ищи по русским названиям — переводи в английский эквивалент.
4. Для поиска по названию: toLower(a.title) CONTAINS toLower('...') OR \
   toLower(a.title_english) CONTAINS toLower('...').
5. "Режиссёр" = ANY(r IN rel.roles WHERE toLower(r) = 'director').
   'Director' и 'ADR Director' — разные. Используй exact match toLower(r) = 'director'.
6. "Сэйю"/"актёр озвучки" = Person через VOICE_ACTED к Character.
7. LIMIT — ТОЛЬКО когда пользователь явно просит конкретное число ("топ-5", \
"лучшие 3", "покажи 10"). Во всех остальных случаях НЕ добавляй LIMIT — \
бэкенд сам ограничит объём данных. Произвольный LIMIT обрезает результаты \
и приводит к неполным ответам.
8. Сортируй по score DESC или year, если релевантно.
9. Для "все проекты человека": сначала найди Person по имени/ID, потом\
   MATCH (a:Anime)-[:STAFF]->(p) RETURN a.title.
10. Если запрос не имеет смысла в этой схеме — верни "INVALID".
11. Если вопрос неоднозначен или не хватает данных для точного запроса \
   (например, неясно о каком именно тайтле речь, или пользователь не уточнил \
   что именно хочет узнать) — верни "CLARIFY: <уточняющий вопрос на русском>".
   Пример: вопрос "сколько серий" без указания тайтла → \
   "CLARIFY: О каком аниме идёт речь?"
12. Ответы на русском потом сформулирует другая часть системы — ты только Cypher.
"""

DEFAULT_ANSWER_PROMPT = """\
Ты — ассистент по аниме. Тебе задали вопрос, ты выполнил запрос к базе данных MyAnimeList.
Сейчас тебе вернулся результат. Сформулируй ответ на русском языке.

Правила:
1. Отвечай ТОЛЬКО на основе данных из результата запроса. Не придумывай факты.
2. Если результат пустой — скажи честно: "Не нашёл информации по этому запросу."
3. Если данных мало — перечисли что есть, не добавляй от себя.
4. Форматируй читаемо: списки, таблицы если уместно.
5. Кратко и по делу. Без воды.
6. На русском.
"""


# --------------------------------------------------------------------------- #
# Config helpers                                                               #
# --------------------------------------------------------------------------- #

def _cfg() -> session_config.SessionConfig:
    return session_config.get_config()


def _cypher_prompt() -> str:
    return _cfg().cypher_prompt or DEFAULT_CYPHER_PROMPT


def _answer_prompt() -> str:
    return _cfg().answer_prompt or DEFAULT_ANSWER_PROMPT


# --------------------------------------------------------------------------- #
# LLM calls: non-streaming + streaming                                        #
# --------------------------------------------------------------------------- #

def _is_ollama(base_url: str) -> bool:
    """True если URL похож на Ollama (для native /api/chat)."""
    return ":11434" in base_url or "ollama" in base_url.lower()


def _llm_call(system_prompt: str, user_content: str, max_tokens: int = 4096) -> str:
    """Не-streaming вызов LLM. Возвращает полный текст content."""
    cfg = _cfg()
    if cfg.think and _is_ollama(cfg.base_url):
        return _ollama_native_call(system_prompt, user_content, max_tokens)
    return _openai_call(system_prompt, user_content, max_tokens)


def _openai_call(system_prompt: str, user_content: str, max_tokens: int) -> str:
    """OpenAI-compatible /v1/chat/completions."""
    cfg = _cfg()
    resp = requests.post(
        f"{cfg.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"].get("content") or "").strip()


def _ollama_native_call(system_prompt: str, user_content: str, max_tokens: int) -> str:
    """Ollama native /api/chat с think-поддержкой."""
    cfg = _cfg()
    # /v1 → base without /v1
    base = cfg.base_url.replace("/v1", "")
    resp = requests.post(
        f"{base}/api/chat",
        json={
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "think": cfg.think,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return (resp.json().get("message", {}).get("content") or "").strip()


def _llm_stream(system_prompt: str, user_content: str, max_tokens: int
                ) -> Generator[dict, None, None]:
    """Streaming генератор. Yields dicts: {type: 'thinking'|'content'|'done', text: str}."""
    cfg = _cfg()
    if cfg.think and _is_ollama(cfg.base_url):
        yield from _ollama_stream(system_prompt, user_content, max_tokens)
    else:
        yield from _openai_stream(system_prompt, user_content, max_tokens)


def _openai_stream(system_prompt: str, user_content: str, max_tokens: int
                   ) -> Generator[dict, None, None]:
    """OpenAI SSE streaming."""
    cfg = _cfg()
    resp = requests.post(
        f"{cfg.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "stream": True,
        },
        timeout=120,
        stream=True,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            yield {"type": "done", "text": ""}
            return
        try:
            chunk = json.loads(data)
            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content", "")
            if content:
                yield {"type": "content", "text": content}
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
    yield {"type": "done", "text": ""}


def _ollama_stream(system_prompt: str, user_content: str, max_tokens: int
                   ) -> Generator[dict, None, None]:
    """Ollama native /api/chat streaming with think support."""
    cfg = _cfg()
    base = cfg.base_url.replace("/v1", "")
    resp = requests.post(
        f"{base}/api/chat",
        json={
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": True,
            "think": cfg.think,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        },
        timeout=120,
        stream=True,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            chunk = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if chunk.get("done"):
            yield {"type": "done", "text": ""}
            return
        msg = chunk.get("message", {})
        # В streaming-режиме Ollama не разделяет thinking/content,
        # но при think=True поле может содержать reasoning
        content = msg.get("content", "")
        if content:
            yield {"type": "content", "text": content}
    yield {"type": "done", "text": ""}


# --------------------------------------------------------------------------- #
# Cypher helpers                                                               #
# --------------------------------------------------------------------------- #

def _extract_cypher(text: str) -> str:
    """Извлекает Cypher из ответа LLM (убирает markdown-блоки если есть)."""
    m = re.search(r'```(?:cypher)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


_driver = None


def _get_driver():
    """Singleton Neo4j driver — создаётся один раз, переиспользуется."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def _run_cypher(cypher: str) -> tuple[list[dict] | None, str | None]:
    """Выполняет Cypher в Neo4j. Возвращает (rows, error)."""
    try:
        with _get_driver().session() as session:
            return [dict(r) for r in session.run(cypher)], None
    except Exception as e:
        return None, str(e)


# --------------------------------------------------------------------------- #
# Result helpers                                                               #
# --------------------------------------------------------------------------- #

def _make_result(answer, cypher, status, rows, attempts, error,
                 t0, raw=None) -> dict:
    """Создаёт единый dict результата с метаданными."""
    cfg = _cfg()
    return {
        "answer": answer,
        "cypher": cypher,
        "status": status,
        "rows": rows,
        "attempts": attempts,
        "error": error,
        "model": cfg.model,
        "llm_base_url": cfg.base_url,
        "duration_sec": round(time.time() - t0, 2),
        "cypher_raw": raw,
    }


def _is_invalid(cypher: str) -> bool:
    return not cypher.strip() or cypher.upper().strip() == "INVALID"


def _is_clarify(cypher: str) -> bool:
    return cypher.strip().upper().startswith("CLARIFY:")


def _clarify_question(cypher: str) -> str:
    return cypher.strip()[len("CLARIFY:"):].strip()


def _build_history_context(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["", "Предыдущий контекст разговора:"]
    for m in history[-6:]:
        role = "Пользователь" if m["role"] == "user" else "Ответ"
        lines.append(f"{role}: {m['content'][:200]}")
    return "\n".join(lines) + "\n"


def _build_retry_message(question: str, error: str) -> str:
    return (f"Вопрос: {question}\n\n"
            f"Предыдущая попытка Cypher упала с ошибкой:\n{error}\n\n"
            "Исправь запрос и попробуй снова.")


def _build_user_message(question: str, history_ctx: str,
                        error: str | None, attempt: int) -> str:
    if attempt == 1:
        return f"Вопрос: {question}{history_ctx}"
    return _build_retry_message(question, error)


def _truncate_rows(rows: list[dict]) -> str:
    limited = rows[:100]
    result_str = json.dumps(limited, ensure_ascii=False, default=str)
    if len(rows) > 100:
        result_str += f"\n... (показано 100 из {len(rows)} строк)"
    return result_str


def _log_and_return(chat_id, question, cypher, status, rows, error, attempts,
                    t0, raw, answer):
    if chat_id:
        cfg = _cfg()
        db.log_query(chat_id, question, cypher, status, rows, error, attempts,
                      model=cfg.model, llm_base_url=cfg.base_url,
                      answer=answer,
                      duration_sec=round(time.time() - t0, 2),
                      cypher_raw=raw)
    return _make_result(answer, cypher, status, rows, attempts, error, t0, raw)


# --------------------------------------------------------------------------- #
# Non-streaming pipeline                                                       #
# --------------------------------------------------------------------------- #

def ask(question: str, chat_id: str = None, history: list[dict] = None) -> dict:
    """Полный пайплайн: question → Cypher → Neo4j → answer (non-streaming)."""
    t0 = time.time()
    history_ctx = _build_history_context(history or [])
    cypher = None
    raw = None
    error = None

    for attempt in range(1, MAX_CYPHER_ATTEMPTS + 1):
        log.info("Cypher attempt %d/%d for: %s", attempt, MAX_CYPHER_ATTEMPTS, question[:80])
        user_msg = _build_user_message(question, history_ctx, error, attempt)
        try:
            raw = _llm_call(_cypher_prompt(), user_msg, max_tokens=_cfg().max_tokens)
        except Exception as e:
            log.error("LLM call failed: %s", e)
            return _make_result(
                f"Ошибка при обращении к LLM: {e}", None, "llm_error",
                0, attempt, str(e), t0)

        cypher = _extract_cypher(raw)

        if _is_invalid(cypher):
            log.info("LLM returned empty/INVALID")
            return _log_and_return(
                chat_id, question, cypher or "(empty)", "invalid",
                0, None, attempt, t0, raw,
                "Не нашёл информации по этому запросу. Я могу отвечать на вопросы "
                "о тайтлах, студиях, жанрах, персонажах, сэйю, режиссёрах и связях между ними.")

        if _is_clarify(cypher):
            clarify = _clarify_question(cypher)
            log.info("LLM requested clarification: %s", clarify)
            return _log_and_return(
                chat_id, question, cypher, "clarify",
                0, None, attempt, t0, raw, clarify)

        rows, error = _run_cypher(cypher)
        if error is not None:
            log.warning("Cypher error (attempt %d): %s", attempt, error[:200])
            continue

        safe_rows = rows or []
        row_count = len(safe_rows)
        status = "ok" if row_count else "empty"
        log.info("Cypher OK, %d rows returned", row_count)
        answer = _formulate_answer(question, safe_rows)
        return _log_and_return(
            chat_id, question, cypher, status, row_count, None, attempt, t0, raw, answer)

    error_answer = (f"Не удалось выполнить запрос к базе после "
                    f"{MAX_CYPHER_ATTEMPTS} попыток. Последняя ошибка: {error}")
    return _log_and_return(
        chat_id, question, cypher or "", "error", 0, error,
        MAX_CYPHER_ATTEMPTS, t0, raw, error_answer)


def _formulate_answer(question: str, rows: list[dict]) -> str:
    """Шаг 3: LLM формулирует ответ на русском из результатов Cypher."""
    if not rows:
        return "Не нашёл информации по этому запросу."
    try:
        return _llm_call(
            _answer_prompt(),
            f"Вопрос: {question}\n\n"
            f"Результат запроса ({len(rows)} строк):\n{_truncate_rows(rows)}",
            max_tokens=_cfg().max_tokens,
        )
    except Exception as e:
        log.error("Answer LLM call failed: %s", e)
        return f"Запрос выполнен ({len(rows)} строк), но не удалось сформулировать ответ: {e}"


# --------------------------------------------------------------------------- #
# Streaming pipeline (SSE for frontend)                                       #
# --------------------------------------------------------------------------- #

def ask_stream(question: str, chat_id: str = None,
               history: list[dict] = None) -> Generator[str, None, None]:
    """Streaming пайплайн. Yields SSE-formatted strings for EventSource.

    Events:
      data: {"type":"thinking","text":"..."}    — LLM thinking (if think=True)
      data: {"type":"cypher","text":"..."}       — Cypher chunk (step 1)
      data: {"type":"cypher_done","cypher":"...","raw":"..."}  — final Cypher
      data: {"type":"status","status":"ok|empty|invalid|clarify|error", ...}
      data: {"type":"answer","text":"..."}       — Answer chunk (step 3)
      data: {"type":"answer_done","answer":"..."}
      data: {"type":"result","answer":"...","cypher":"...","status":"...","rows":N,...}
    """
    t0 = time.time()
    cfg = _cfg()
    history_ctx = _build_history_context(history or [])
    cypher = None
    raw_parts = []
    error = None

    for attempt in range(1, MAX_CYPHER_ATTEMPTS + 1):
        log.info("Cypher attempt %d/%d for: %s", attempt, MAX_CYPHER_ATTEMPTS, question[:80])
        user_msg = _build_user_message(question, history_ctx, error, attempt)

        # Step 1: stream Cypher generation
        try:
            for event in _llm_stream(_cypher_prompt(), user_msg, cfg.max_tokens):
                if event["type"] == "content":
                    raw_parts.append(event["text"])
                    yield _sse({"type": "cypher", "text": event["text"]})
                elif event["type"] == "done":
                    break
        except Exception as e:
            log.error("LLM stream failed: %s", e)
            result = _make_result(
                f"Ошибка при обращении к LLM: {e}", None, "llm_error",
                0, attempt, str(e), t0)
            yield _sse({"type": "result", **result})
            return

        raw = "".join(raw_parts)
        cypher = _extract_cypher(raw)
        yield _sse({"type": "cypher_done", "cypher": cypher, "raw": raw})

        if _is_invalid(cypher):
            log.info("LLM returned empty/INVALID")
            result = _log_and_return(
                chat_id, question, cypher or "(empty)", "invalid",
                0, None, attempt, t0, raw,
                "Не нашёл информации по этому запросу. Я могу отвечать на вопросы "
                "о тайтлах, студиях, жанрах, персонажах, сэйю, режиссёрах и связях между ними.")
            yield _sse({"type": "result", **result})
            return

        if _is_clarify(cypher):
            clarify = _clarify_question(cypher)
            result = _log_and_return(
                chat_id, question, cypher, "clarify",
                0, None, attempt, t0, raw, clarify)
            yield _sse({"type": "result", **result})
            return

        # Step 2: run Cypher in Neo4j
        rows, error = _run_cypher(cypher)
        if error is not None:
            log.warning("Cypher error (attempt %d): %s", attempt, error[:200])
            yield _sse({"type": "cypher_error", "error": error, "attempt": attempt})
            continue

        # Step 3: stream answer formulation
        safe_rows = rows or []
        row_count = len(safe_rows)
        status = "ok" if row_count else "empty"
        yield _sse({"type": "status", "status": status, "rows": row_count})

        answer_parts = []
        if row_count:
            answer_msg = (f"Вопрос: {question}\n\n"
                          f"Результат запроса ({row_count} строк):\n"
                          f"{_truncate_rows(safe_rows)}")
            try:
                for event in _llm_stream(_answer_prompt(), answer_msg, cfg.max_tokens):
                    if event["type"] == "content":
                        answer_parts.append(event["text"])
                        yield _sse({"type": "answer", "text": event["text"]})
                    elif event["type"] == "done":
                        break
            except Exception as e:
                log.error("Answer LLM stream failed: %s", e)
                answer_parts.append(
                    f"Запрос выполнен ({row_count} строк), но не удалось сформулировать ответ: {e}")
        else:
            answer_parts.append("Не нашёл информации по этому запросу.")

        answer = "".join(answer_parts)
        result = _log_and_return(
            chat_id, question, cypher, status, row_count, None, attempt, t0, raw, answer)
        yield _sse({"type": "result", **result})
        return

    # All attempts exhausted
    error_answer = (f"Не удалось выполнить запрос к базе после "
                    f"{MAX_CYPHER_ATTEMPTS} попыток. Последняя ошибка: {error}")
    result = _log_and_return(
        chat_id, question, cypher or "", "error", 0, error,
        MAX_CYPHER_ATTEMPTS, t0, raw, error_answer)
    yield _sse({"type": "result", **result})


def _sse(data: dict) -> str:
    """Форматирует dict как SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"