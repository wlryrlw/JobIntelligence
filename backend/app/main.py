from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import crawl_manager, router


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await crawl_manager.shutdown()


app = FastAPI(
    title="OfferAgent API",
    version="0.2.0",
    description="OfferAgent 求职决策系统 API，支持岗位市场查询、求职分析和 Human-in-the-loop 工作流",
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
