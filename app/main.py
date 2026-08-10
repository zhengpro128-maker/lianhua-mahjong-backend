"""莲花广麻 · 联网麻将后端 — FastAPI 入口"""

# 日志最先配置：storage 等模块导入时即可输出（见 app/logging_config.py）
from contextlib import asynccontextmanager
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.logging_config import (
    _patch_uvicorn_loggers,
    configure_logging,
    redact_sensitive_data,
)

configure_logging()

from app.api.rooms import router as rooms_router
from app.api.matches import router as matches_router
from app.api.moderation import router as moderation_router
from app.api.account import router as account_router
from app.ws.game_ws import router as ws_router
from app.storage.db import storage

DOCS_SHOW = os.getenv('DOCS_SHOW', 'False') == 'True'
logger.info(f"api文档开启: {DOCS_SHOW}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn 启动时用默认 dictConfig 覆盖了日志配置，这里重新接管 uvicorn 日志到 loguru
    _patch_uvicorn_loggers()
    yield


app = FastAPI(title="莲花广麻 Backend", version="0.2.0",
              docs_url="/docs" if DOCS_SHOW else None, lifespan=lifespan)

# 开发期跨域：Vite dev server (:4173) → 后端 REST。生产同源部署时由网关收窄。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'https://vibe.lumigrav.space', 
        'https://lianhuaguangdongmahjong.guoguo-labs.online/', 
        'http://localhost:4173',
        'http://127.0.0.1:4173',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


def _redact_query(query: str) -> str:
    """query 中 rejoin_code 为座位凭据，落日志时打码，避免凭据泄漏进日志文件。"""
    return redact_sensitive_data(query)


@app.middleware('http')
async def access_log_middleware(request: Request, call_next):
    """HTTP 访问日志：方法 / 路径 / 状态 / 耗时 / 来源 IP / request_id。"""
    start = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]
    # 上下文变量：随 async 任务传播，sync 路由跑在线程池时 anyio 也会拷贝上下文
    with logger.contextualize(request_id=request_id):
        client_ip = request.client.host if request.client else '-'
        # HTTP query 先脱敏；Uvicorn 的 WS 请求行由 InterceptHandler 统一脱敏。
        query = _redact_query(request.url.query)
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                f"HTTP 未处理异常 {request.method} {request.url.path} "
                f"状态=500 耗时={duration_ms:.1f}ms 来源={client_ip} 参数={query}")
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        log = logger.debug if request.url.path == '/api/health' else logger.info
        log(
            f"HTTP {request.method} {request.url.path} 状态={response.status_code} "
            f"耗时={duration_ms:.1f}ms 来源={client_ip} 参数={query}")
        return response


# 启动即建表（SQLite，幂等）
storage.init()

app.include_router(rooms_router)
app.include_router(matches_router)
app.include_router(moderation_router)
app.include_router(account_router)
app.include_router(ws_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
