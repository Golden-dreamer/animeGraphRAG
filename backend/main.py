"""FastAPI сервер GraphRAG: чаты + API для запросов к графу."""
import logging
import os
import uuid

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import graphrag

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
    log.info("GraphRAG server started. LLM model=%s, Neo4j=%s",
             graphrag.LLM_MODEL, graphrag.NEO4J_URI)


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

@app.post("/api/chats/{chat_id}/ask")
def api_ask(chat_id: str, req: ChatMessage):
    history = db.get_messages(chat_id)
    db.add_message(chat_id, "user", req.message)

    result = graphrag.ask(req.message, chat_id=chat_id, history=history)
    db.add_message(chat_id, "assistant", result["answer"])

    _record_metrics(result)
    return _build_ask_response(result)


def _build_ask_response(result: dict) -> dict:
    """Собирает JSON-ответ из result пайплайна."""
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


# --- API логов ---

@app.get("/api/logs")
def api_logs(limit: int = 100):
    return {"logs": db.get_logs(limit)}


@app.get("/logs")
def logs_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "logs.html"))


@app.get("/api/health")
def health():
    return {"ok": True, "model": graphrag.LLM_MODEL, "llm_base_url": graphrag.LLM_BASE_URL}


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
    """Обновляет счётчики Prometheus по результату ask()."""
    _metrics_inc("graphrag_requests_total")
    _metrics_inc("graphrag_cypher_attempts_total", result.get("attempts", 1))
    _metrics_inc("graphrag_rows_returned_total", result.get("rows", 0))
    _metrics_observe_duration(result.get("duration_sec", 0))
    metric = _STATUS_METRIC_MAP.get(result.get("status", "error"))
    if metric:
        _metrics_inc(metric)


@app.get("/metrics")
def metrics():
    """Prometheus text exposition format."""
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
    """Форматирует summary-метрику (_sum + _count)."""
    base = name[:-4]
    return [
        f"# TYPE {base} summary",
        f"{base}_sum {val}",
        f'{base}_count {_metrics.get(base + "_count", 0)}',
    ]