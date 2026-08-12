from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from api.chat import router as chat_router
from api.files import router as files_router
from api.sessions import router as sessions_router
from config import get_settings
from graph.agent import agent_manager
from tools.skills_scanner import refresh_snapshot


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    refresh_snapshot(settings.backend_dir)
    checkpoint_path = settings.backend_dir / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        await checkpointer.setup()
        agent_manager.initialize(settings.backend_dir, checkpointer=checkpointer)
        yield


app = FastAPI(
    title="Repository Steward API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(files_router, prefix="/api", tags=["files"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
