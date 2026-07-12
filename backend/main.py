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

    return {
        "answer": result["answer"],
        "cypher": result["cypher"],
        "status": result["status"],
        "rows": result["rows"],
        "attempts": result["attempts"],
    }


# --- API логов ---

@app.get("/api/logs")
def api_logs(limit: int = 50):
    return {"logs": db.get_logs(limit)}


@app.get("/api/health")
def health():
    return {"ok": True, "model": graphrag.LLM_MODEL}