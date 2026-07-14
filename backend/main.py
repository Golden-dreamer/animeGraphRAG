"""FastAPI сервер GraphRAG: чаты + API для запросов к графу."""
import logging
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

import db
import graphrag

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Anime GraphRAG")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
# В Docker фронтенд смонтирован в /frontend
if not os.path.exists(FRONTEND_DIR):
    FRONTEND_DIR = "/frontend"


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


# --- API чатов ---

class ChatCreate(BaseModel):
    title: str = "Новый чат"


class ChatMessage(BaseModel):
    message: str


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

    # Метрики
    _metrics_inc("graphrag_requests_total")
    _metrics_inc("graphrag_cypher_attempts_total", result.get("attempts", 1))
    _metrics_inc("graphrag_rows_returned_total", result.get("rows", 0))
    _metrics_observe_duration(result.get("duration_sec", 0))
    status = result.get("status", "error")
    if status == "ok" or status == "empty":
        _metrics_inc("graphrag_requests_ok")
    elif status == "error":
        _metrics_inc("graphrag_requests_error")
    elif status == "invalid":
        _metrics_inc("graphrag_requests_invalid")
    elif status == "clarify":
        _metrics_inc("graphrag_requests_clarify")

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


def _metrics_inc(name: str, amount: int = 1):
    _metrics[name] = _metrics.get(name, 0) + amount


def _metrics_observe_duration(sec: float):
    _metrics["graphrag_duration_sec_sum"] += sec
    _metrics["graphrag_duration_sec_count"] += 1


@app.get("/metrics")
def metrics():
    """Prometheus text exposition format."""
    from fastapi import Response
    lines = []
    for name, val in _metrics.items():
        if name.endswith("_sum"):
            base = name[:-4]
            lines.append(f"# TYPE {base} summary")
            lines.append(f'{base}_sum {val}')
            lines.append(f'{base}_count {_metrics.get(base + "_count", 0)}')
        elif name.endswith("_count") and name[:-6] + "_sum" in _metrics:
            continue  # handled by _sum
        else:
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {val}")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")