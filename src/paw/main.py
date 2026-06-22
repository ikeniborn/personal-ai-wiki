from fastapi import FastAPI

from paw.api.errors import install_error_handlers
from paw.api.routers import auth as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Personal AI Wiki", version="0.1.0")
    install_error_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router.router, prefix="/api/v1")
    return app


app = create_app()
