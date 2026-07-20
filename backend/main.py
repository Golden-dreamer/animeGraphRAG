"""FastAPI сервер GraphRAG: чаты + API для запросов к графу."""
import logging
import os
import uuid

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import graphrag
import session_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Anime GraphRAG")


def _resolve_frontend_dir() -> str:
    """Определяет путь к frontend/ (Docker или локально)."""
    local = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return local if os.path.exists(local) else "/frontend"


FRONTEND_DIR = _resolve_frontend_dir()


@app.on_event("startup")
def startup():
    db.init_db()
    cfg = session_config.get_config()
    log.info("GraphRAG server started. LLM model=%s, base_url=%s, Neo4j=%s",
             cfg.model, cfg.base_url, graphrag.NEO4J_URI)


# --- Статика (фронтенд) ---

@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# --- Модели запросов ---

class ChatCreate(BaseModel):
    title: str = "Новый чат"


class ChatMessage(BaseModel):
    message: str


class SettingsUpdate(BaseModel):
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int | None = None
    think: bool | None = None
    cypher_prompt: str | None = None
    answer_prompt: str | None = None


# --- API чатов ---

@app.get("/api/chats")
def api_list_chats():
    return {"chats": db.list_chats()}


@app.post("/api/chats")
def api_create_chat(req: ChatCreate):
    chat_id = uuid.uuid4().hex[:12]
    db.create_chat(chat_id, req.title)
    return {"id": chat_id, "title": req.title}


@app.delete("/api/chats/{chat_id}")
def api_delete_chat(chat_id: str):
    db.delete_chat(chat_id)
    return {"ok": True}


@app.put("/api/chats/{chat_id}")
def api_rename_chat(chat_id: str, req: ChatCreate):
    db.rename_chat(chat_id, req.title)
    return {"ok": True}


@app.get("/api/chats/{chat_id}/messages")
def api_get_messages(chat_id: str):
    return {"messages": db.get_messages(chat_id)}


# --- API запросов к графу ---

@app.post("/api/chats/{chat_id}/title")
def api_generate_title(chat_id: str, req: ChatMessage):
    """Генерирует название чата из первого сообщения пользователя."""
    title = graphrag.generate_title(req.message)
    db.rename_chat(chat_id, title)
    return {"title": title}


@app.post("/api/chats/{chat_id}/ask")
def api_ask(chat_id: str, req: ChatMessage):
    """Non-streaming ask (fallback)."""
    history = db.get_messages(chat_id)
    db.add_message(chat_id, "user", req.message)
    result = graphrag.ask(req.message, chat_id=chat_id, history=history)
    db.add_message(chat_id, "assistant", result["answer"])
    _record_metrics(result)
    return _build_ask_response(result)


@app.post("/api/chats/{chat_id}/ask/stream")
def api_ask_stream(chat_id: str, req: ChatMessage):
    """Streaming ask via SSE (Server-Sent Events)."""
    history = db.get_messages(chat_id)
    db.add_message(chat_id, "user", req.message)

    def generate():
        answer_parts = []
        for sse in graphrag.ask_stream(req.message, chat_id=chat_id, history=history):
            # Перехватываем answer для сохранения в БД
            try:
                import json
                data = json.loads(sse.replace("data: ", "").strip())
                if data.get("type") == "answer":
                    answer_parts.append(data.get("text", ""))
                elif data.get("type") == "result":
                    answer = data.get("answer", "")
                    if answer:
                        db.add_message(chat_id, "assistant", answer)
                    _record_metrics_from_result(data)
            except Exception:
                pass
            yield sse

    return StreamingResponse(generate(), media_type="text/event-stream")


def _build_ask_response(result: dict) -> dict:
    return {
        "answer": result["answer"],
        "cypher": result["cypher"],
        "status": result["status"],
        "rows": result["rows"],
        "attempts": result["attempts"],
        "model": result.get("model"),
        "llm_base_url": result.get("llm_base_url"),
        "duration_sec": result.get("duration_sec"),
        "cypher_raw": result.get("cypher_raw"),
    }


# --- API настроек ---

@app.get("/api/settings")
def api_get_settings():
    return session_config.get_settings()


@app.put("/api/settings")
def api_update_settings(req: SettingsUpdate):
    updated = session_config.update_settings(**req.model_dump(exclude_none=True))
    log.info("Settings updated: model=%s, think=%s, base_url=%s",
             updated.get("model"), updated.get("think"), updated.get("base_url"))
    return updated


# --- API логов ---

@app.get("/api/logs")
def api_logs(limit: int = 100):
    return {"logs": db.get_logs(limit)}


@app.get("/logs")
def logs_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "logs.html"))


@app.get("/api/health")
def health():
    cfg = session_config.get_config()
    return {"ok": True, "model": cfg.model, "llm_base_url": cfg.base_url}


# --- Prometheus metrics ---

_metrics = {
    "graphrag_requests_total": 0,
    "graphrag_requests_ok": 0,
    "graphrag_requests_error": 0,
    "graphrag_requests_invalid": 0,
    "graphrag_requests_clarify": 0,
    "graphrag_cypher_attempts_total": 0,
    "graphrag_rows_returned_total": 0,
    "graphrag_duration_sec_sum": 0.0,
    "graphrag_duration_sec_count": 0,
}

_STATUS_METRIC_MAP = {
    "ok": "graphrag_requests_ok",
    "empty": "graphrag_requests_ok",
    "error": "graphrag_requests_error",
    "invalid": "graphrag_requests_invalid",
    "clarify": "graphrag_requests_clarify",
}


def _metrics_inc(name: str, amount: int = 1):
    _metrics[name] = _metrics.get(name, 0) + amount


def _metrics_observe_duration(sec: float):
    _metrics["graphrag_duration_sec_sum"] += sec
    _metrics["graphrag_duration_sec_count"] += 1


def _record_metrics(result: dict):
    _metrics_inc("graphrag_requests_total")
    _metrics_inc("graphrag_cypher_attempts_total", result.get("attempts", 1))
    _metrics_inc("graphrag_rows_returned_total", result.get("rows", 0))
    _metrics_observe_duration(result.get("duration_sec", 0))
    metric = _STATUS_METRIC_MAP.get(result.get("status", "error"))
    if metric:
        _metrics_inc(metric)


def _record_metrics_from_result(data: dict):
    """Извлекает метрики из SSE result event."""
    _metrics_inc("graphrag_requests_total")
    _metrics_inc("graphrag_cypher_attempts_total", data.get("attempts", 1))
    _metrics_inc("graphrag_rows_returned_total", data.get("rows", 0))
    _metrics_observe_duration(data.get("duration_sec", 0))
    metric = _STATUS_METRIC_MAP.get(data.get("status", "error"))
    if metric:
        _metrics_inc(metric)


@app.get("/metrics")
def metrics():
    lines = []
    for name, val in _metrics.items():
        if name.endswith("_sum"):
            lines.extend(_format_summary(name, val))
        elif name.endswith("_count") and name[:-6] + "_sum" in _metrics:
            continue
        else:
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {val}")
    return Response(content="\n".join(lines) + "\n",
                    media_type="text/plain; version=0.0.4")


def _format_summary(name: str, val: float) -> list[str]:
    base = name[:-4]
    return [
        f"# TYPE {base} summary",
        f"{base}_sum {val}",
        f'{base}_count {_metrics.get(base + "_count", 0)}',
    ]