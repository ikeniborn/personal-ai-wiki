from fastapi import FastAPI

from paw.api.errors import install_error_handlers
from paw.api.routers import articles as articles_router
from paw.api.routers import auth as auth_router
from paw.api.routers import domains as domains_router
from paw.api.routers import settings as settings_router
from paw.api.routers import setup as setup_router
from paw.api.routers import sources as sources_router
from paw.api.routers import users as users_router


def create_app() -> FastAPI:
    app = FastAPI(title="Personal AI Wiki", version="0.1.0")
    install_error_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(domains_router.router, prefix="/api/v1")
    app.include_router(sources_router.router, prefix="/api/v1")
    app.include_router(articles_router.router, prefix="/api/v1")
    app.include_router(setup_router.router, prefix="/api/v1")
    app.include_router(settings_router.router, prefix="/api/v1")
    app.include_router(users_router.router, prefix="/api/v1")
    return app


app = create_app()
