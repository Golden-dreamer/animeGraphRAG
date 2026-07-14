"""GraphRAG engine: question → LLM генерирует Cypher → Neo4j → LLM формулирует ответ.

Two-step pipeline:
  1. text→Cypher: LLM получает схему графа + вопрос, генерирует Cypher-запрос
  2. result→answer: LLM получает результат из Neo4j, формулирует ответ на русском

Самопроверка: если Cypher упал с синтаксической ошибкой — бэкенд возвращает
ошибку LLM, та пробует снова (до 3 попыток).
"""
import json
import logging
import os
import re
import time

import requests
from neo4j import GraphDatabase

import db

log = logging.getLogger("graphrag")

# --- Конфигурация LLM ---

LLM_BASE_URL = os.environ.get("GRAPHRAG_LLM_BASE_URL", "https://ollama.com/v1")
LLM_API_KEY = os.environ.get("GRAPHRAG_LLM_API_KEY", os.environ.get("OLLAMA_API_KEY", ""))
LLM_MODEL = os.environ.get("GRAPHRAG_LLM_MODEL", "glm-5.2")
LLM_MAX_TOKENS = int(os.environ.get("GRAPHRAG_LLM_MAX_TOKENS", "8192"))

# --- Конфигурация Neo4j ---

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

MAX_CYPHER_ATTEMPTS = 3

# --- Схема графа (для system prompt) ---

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

CYPHER_SYSTEM_PROMPT = f"""\
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

ANSWER_SYSTEM_PROMPT = """\
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
# Helpers: LLM calls, Cypher extraction, Neo4j execution                      #
# --------------------------------------------------------------------------- #

def _llm_call(system_prompt: str, user_content: str, max_tokens: int = 4096) -> str:
    """Вызов LLM через OpenAI-compatible API."""
    resp = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return _extract_content(resp.json())


def _extract_content(data: dict) -> str:
    """Извлекает текст ответа из JSON ответа LLM API."""
    return data["choices"][0]["message"]["content"].strip()


def _extract_cypher(text: str) -> str:
    """Извлекает Cypher из ответа LLM (убирает markdown-блоки если есть)."""
    m = re.search(r'```(?:cypher)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _run_cypher(cypher: str) -> tuple[list[dict] | None, str | None]:
    """Выполняет Cypher в Neo4j. Возвращает (rows, error)."""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            return [dict(r) for r in session.run(cypher)], None
    except Exception as e:
        return None, str(e)
    finally:
        driver.close()


# --------------------------------------------------------------------------- #
# Helpers: result construction                                                 #
# --------------------------------------------------------------------------- #

def _make_result(answer, cypher, status, rows, attempts, error,
                 t0, raw=None) -> dict:
    """Создаёт единый dict результата с метаданными."""
    return {
        "answer": answer,
        "cypher": cypher,
        "status": status,
        "rows": rows,
        "attempts": attempts,
        "error": error,
        "model": LLM_MODEL,
        "llm_base_url": LLM_BASE_URL,
        "duration_sec": round(time.time() - t0, 2),
        "cypher_raw": raw,
    }


def _is_invalid(cypher: str) -> bool:
    """True если LLM вернул пустой ответ или INVALID."""
    return not cypher.strip() or cypher.upper().strip() == "INVALID"


def _is_clarify(cypher: str) -> bool:
    """True если LLM запросил уточнение (CLARIFY: ...)."""
    return cypher.strip().upper().startswith("CLARIFY:")


def _clarify_question(cypher: str) -> str:
    """Извлекает уточняющий вопрос из 'CLARIFY: <вопрос>'."""
    return cypher.strip()[len("CLARIFY:"):].strip()


def _build_history_context(history: list[dict]) -> str:
    """Превращает историю чата в текстовый контекст для LLM."""
    if not history:
        return ""
    lines = ["", "Предыдущий контекст разговора:"]
    for m in history[-6:]:
        role = "Пользователь" if m["role"] == "user" else "Ответ"
        lines.append(f"{role}: {m['content'][:200]}")
    return "\n".join(lines) + "\n"


def _build_retry_message(question: str, error: str) -> str:
    """Сообщение для повторной попытки Cypher после ошибки."""
    return (f"Вопрос: {question}\n\n"
            f"Предыдущая попытка Cypher упала с ошибкой:\n{error}\n\n"
            "Исправь запрос и попробуй снова.")


def _truncate_rows(rows: list[dict]) -> str:
    """Ограничивает размер данных для LLM: 100 строк max."""
    limited = rows[:100]
    result_str = json.dumps(limited, ensure_ascii=False, default=str)
    if len(rows) > 100:
        result_str += f"\n... (показано 100 из {len(rows)} строк)"
    return result_str


def _formulate_answer(question: str, rows: list[dict]) -> str:
    """Шаг 3: LLM формулирует ответ на русском из результатов Cypher."""
    if not rows:
        return "Не нашёл информации по этому запросу."
    try:
        return _llm_call(
            ANSWER_SYSTEM_PROMPT,
            f"Вопрос: {question}\n\n"
            f"Результат запроса ({len(rows)} строк):\n{_truncate_rows(rows)}",
            max_tokens=LLM_MAX_TOKENS,
        )
    except Exception as e:
        log.error("Answer LLM call failed: %s", e)
        return f"Запрос выполнен ({len(rows)} строк), но не удалось сформулировать ответ: {e}"


# --------------------------------------------------------------------------- #
# Helpers: logging                                                             #
# --------------------------------------------------------------------------- #

def _log_and_return(chat_id, question, cypher, status, rows, error, attempts,
                    t0, raw, answer):
    """Логирует запрос в SQLite и возвращает result dict."""
    if chat_id:
        db.log_query(chat_id, question, cypher, status, rows, error, attempts,
                      model=LLM_MODEL, llm_base_url=LLM_BASE_URL,
                      answer=answer,
                      duration_sec=round(time.time() - t0, 2),
                      cypher_raw=raw)
    return _make_result(answer, cypher, status, rows, attempts, error, t0, raw)


# --------------------------------------------------------------------------- #
# Main pipeline                                                                #
# --------------------------------------------------------------------------- #

def ask(question: str, chat_id: str = None, history: list[dict] = None) -> dict:
    """Полный пайплайн: question → Cypher → Neo4j → answer.

    Возвращает: {answer, cypher, status, rows, attempts, error, model,
                 llm_base_url, duration_sec, cypher_raw}
    """
    t0 = time.time()
    history_ctx = _build_history_context(history or [])
    cypher = None
    raw = None
    error = None

    for attempt in range(1, MAX_CYPHER_ATTEMPTS + 1):
        log.info("Cypher attempt %d/%d for: %s", attempt, MAX_CYPHER_ATTEMPTS, question[:80])

        user_msg = _build_user_message(question, history_ctx, error, attempt)
        try:
            raw = _llm_call(CYPHER_SYSTEM_PROMPT, user_msg, max_tokens=2048)
        except Exception as e:
            log.error("LLM call failed: %s", e)
            return _make_result(
                f"Ошибка при обращении к LLM: {e}", None, "llm_error",
                0, attempt, str(e), t0)

        cypher = _extract_cypher(raw)

        if _is_invalid(cypher):
            log.info("LLM returned empty/INVALID for this question")
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

        # Cypher выполнен успешно
        safe_rows = rows or []
        row_count = len(safe_rows)
        status = "ok" if row_count else "empty"
        log.info("Cypher OK, %d rows returned", row_count)
        answer = _formulate_answer(question, safe_rows)
        return _log_and_return(
            chat_id, question, cypher, status, row_count, None, attempt, t0, raw, answer)

    # Все попытки исчерпаны
    error_answer = (f"Не удалось выполнить запрос к базе после "
                    f"{MAX_CYPHER_ATTEMPTS} попыток. Последняя ошибка: {error}")
    return _log_and_return(
        chat_id, question, cypher or "", "error", 0, error,
        MAX_CYPHER_ATTEMPTS, t0, raw, error_answer)


def _build_user_message(question: str, history_ctx: str,
                        error: str | None, attempt: int) -> str:
    """Собирает сообщение для LLM в зависимости от номера попытки."""
    if attempt == 1:
        return f"Вопрос: {question}{history_ctx}"
    return _build_retry_message(question, error)