from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from paw.api.errors import install_error_handlers
from paw.api.routers import articles as articles_router
from paw.api.routers import auth as auth_router
from paw.api.routers import domains as domains_router
from paw.api.routers import jobs as jobs_router
from paw.api.routers import settings as settings_router
from paw.api.routers import setup as setup_router
from paw.api.routers import sources as sources_router
from paw.api.routers import users as users_router
from paw.api.web import routes as web_routes

_STATIC_DIR = Path(__file__).parent / "api" / "web" / "static"

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'"
)


def create_app() -> FastAPI:
    app = FastAPI(title="Personal AI Wiki", version="0.1.0")
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
        jobs_router,
    ):
        app.include_router(r.router, prefix="/api/v1")
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return app


app = create_app()
