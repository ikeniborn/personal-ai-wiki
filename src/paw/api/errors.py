from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        type_: str = "about:blank",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type_
        self.headers = dict(headers or {})
        super().__init__(title)


def problem_response(exc: ProblemError) -> JSONResponse:
    body = {"type": exc.type, "title": exc.title, "status": exc.status}
    if exc.detail:
        body["detail"] = exc.detail
    return JSONResponse(
        status_code=exc.status,
        content=body,
        media_type="application/problem+json",
        headers=exc.headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _handle(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(exc)

    @app.exception_handler(IntegrityError)
    async def _handle_integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        return problem_response(
            ProblemError(status=409, title="Conflict", detail="resource already exists")
        )
