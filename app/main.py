from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import initialize_database
from app.providers.mock import MockTutorProvider
from app.tutor import TutorService


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
settings = get_settings()
tutor = TutorService(MockTutorProvider())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class CreateSessionRequest(BaseModel):
    scenario: str = Field(default="free_conversation", min_length=1, max_length=80)
    level: str = Field(default="intermediate", pattern="^(beginner|intermediate|advanced)$")


class ChatRequest(BaseModel):
    session_id: int
    text: str = Field(min_length=1, max_length=4000)
    scenario: str = "free_conversation"
    level: str = "intermediate"


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "llm_provider": settings.llm_provider,
        "stt_provider": settings.stt_provider,
        "tts_provider": settings.tts_provider,
        "pronunciation_provider": settings.pronunciation_provider,
    }


@app.post("/api/sessions")
async def create_session(payload: CreateSessionRequest) -> dict:
    session_id = await tutor.create_session(payload.scenario, payload.level)
    return {"session_id": session_id}


@app.post("/api/chat")
async def chat(payload: ChatRequest) -> dict:
    if payload.session_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    reply = await tutor.send_text(
        payload.session_id,
        payload.text,
        scenario=payload.scenario,
        level=payload.level,
    )
    return {
        "text": reply.text,
        "correction": reply.correction,
        "explanation_ar": reply.explanation_ar,
    }


@app.get("/")
async def index():
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="UI not installed")
    return FileResponse(index_file)
