# ruff: noqa: E402 -- sentry_sdk.init() below must run before the other
# first-party imports (router setup, Pydantic/SQLAlchemy construction, etc.
# -- anything that could fail at import time), which is why they follow a
# non-import statement instead of being grouped at the very top.
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src import api_docs
from src.config import settings
from src.sentry_scrub import scrub_sso_token  # stdlib-only: safe to import before init

# X-2d: error tracking, initialized right after `settings` -- before any
# other first-party import -- so an import-time crash in one of those
# modules (e.g. a router's own Pydantic/SQLAlchemy setup) is
# still captured instead of crashing invisibly before Sentry exists. An
# empty SENTRY_DSN (the default) disables the SDK entirely -- no error, no
# events sent -- safe in local dev/CI.
sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    environment=settings.ENVIRONMENT.value,
    traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
    # The diary card's deep link carries an SSO token, and Sentry attaches
    # stack-frame locals to exceptions -- see src/sentry_scrub.py.
    before_send=scrub_sso_token,
)

from src.admin.router import router as admin_router
from src.agriculture.router import router as agriculture_router
from src.analytics.router import router as analytics_router
from src.auth.router import router as auth_router
from src.chat.liff import router as liff_tasks_router
from src.chat.router import router as line_router
from src.collection.router import router as collection_router
from src.diary.router import router as diary_router
from src.exceptions import register_exception_handlers
from src.forms.router import router as forms_router
from src.jobs.router import router as internal_router
from src.jobs.scheduler import configure_jobs, scheduler
from src.logging_config import configure_logging
from src.me.router import router as me_router
from src.notifications.router import router as notifications_router
from src.processing.router import router as processing_router
from src.reports.router import router as reports_router
from src.request_id import request_id_middleware
from src.tasks.router import router as tasks_router

# X-2e: must run before the app is built, and before uvicorn serves its
# first request, so every log line carries the request ID (see
# src/logging_config.py for why this has to happen after uvicorn's own
# logging setup, which it does -- uvicorn imports this module).
configure_logging()

if not settings.ENVIRONMENT.is_deployed:
    from src.chat.dev_router import router as conversation_test_router

# Vercel sets this env var on every deployment automatically. A serverless
# function there is frozen between requests -- APScheduler's own in-process
# timers never get a chance to fire on schedule (live-caught 2026-09: nothing
# but "Run time of job ... was missed by ..." warnings, no on-time runs
# ever). Starting it there anyway is pure noise: it can't work, and the
# misfire warnings are misleading. src/jobs/router.py's endpoints are
# the real trigger path on Vercel (wired to Vercel Cron / an external
# scheduler instead); on a persistent host (Render, a VM, local `uvicorn`)
# this is unset and the scheduler starts exactly as before.
_IS_SERVERLESS = bool(settings.VERCEL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if not _IS_SERVERLESS:
        configure_jobs()
        scheduler.start()
    yield
    if not _IS_SERVERLESS:
        scheduler.shutdown()


app = FastAPI(
    title=api_docs.TITLE,
    description=api_docs.DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    openapi_tags=api_docs.TAGS,
    # /docs (Swagger UI) and /redoc follow openapi_url: None hides all three.
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters=api_docs.SWAGGER_UI_PARAMETERS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# X-2e: added last so it's the outermost middleware (Starlette executes
# middleware in reverse registration order) -- the request ID needs to be
# set before CORS or anything else in the stack runs.
app.add_middleware(BaseHTTPMiddleware, dispatch=request_id_middleware)


register_exception_handlers(app)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}


app.include_router(auth_router)
app.include_router(me_router)
app.include_router(diary_router)
app.include_router(admin_router)
app.include_router(tasks_router)
app.include_router(forms_router)
app.include_router(reports_router)
app.include_router(analytics_router)
app.include_router(agriculture_router)
app.include_router(collection_router)
app.include_router(processing_router)
app.include_router(line_router)
app.include_router(liff_tasks_router)
app.include_router(notifications_router)
app.include_router(internal_router)

if not settings.ENVIRONMENT.is_deployed:
    app.include_router(conversation_test_router)
