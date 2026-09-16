from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import BASE_DIR
from .demo_data import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    database_dir = BASE_DIR / "data" / "databases" / "askdata_mock"
    if not database_dir.exists() or not any(database_dir.glob("*.csv")):
        seed_demo_data(database_dir=database_dir)
    yield


app = FastAPI(
    title="AskData Studio API",
    version="0.2.0",
    description="基于LangGraph、支持Human-in-the-loop的轻量Text-to-SQL服务",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
