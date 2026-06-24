import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from paw.api.errors import install_error_handlers
from paw.api.routers import api_keys as api_keys_router
from paw.api.routers import articles as articles_router
from paw.api.routers import auth as auth_router
from paw.api.routers import chat as chat_router
from paw.api.routers import domains as domains_router
from paw.api.routers import graph as graph_router
from paw.api.routers import jobs as jobs_router
from paw.api.routers import maintenance as maintenance_router
from paw.api.routers import query as query_router
from paw.api.routers import settings as settings_router
from paw.api.routers import setup as setup_router
from paw.api.routers import sources as sources_router
from paw.api.routers import users as users_router
from paw.api.web import routes as web_routes
from paw.mcp.auth import MCPAuthMiddleware
from paw.mcp.server import build_mcp

_STATIC_DIR = Path(__file__).parent / "api" / "web" / "static"

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'"
)


def create_app() -> FastAPI:
    mcp = build_mcp()
    # streamable_http_app() must be called before session_manager is accessed
    mcp_asgi = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Personal AI Wiki", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def csp(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = _CSP
        return resp

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for r in (
        auth_router,
        domains_router,
        sources_router,
        articles_router,
        setup_router,
        settings_router,
        users_router,
        api_keys_router,
        jobs_router,
        query_router,
        chat_router,
        graph_router,
        maintenance_router,
    ):
        app.include_router(r.router, prefix="/api/v1")
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.mount("/mcp", mcp_asgi)
    app.add_middleware(MCPAuthMiddleware)
    return app


app = create_app()
