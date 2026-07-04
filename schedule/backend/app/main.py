from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import qq_sync, xuexitong  # noqa: F401  兼容 re-export：测试通过 main.xuexitong / main.qq_sync 打补丁
from .database import init_db
from .routers import events, inbox, integrations
from .settings import get_cors_origins, get_db_path


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Local Schedule API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "timezone": "Asia/Shanghai",
        "db_path": str(get_db_path()),
    }


app.include_router(events.router)
app.include_router(integrations.router)
app.include_router(inbox.router)
