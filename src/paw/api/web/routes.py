import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import CSRF_COOKIE, SESSION_COOKIE, db, get_session_store
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.security.sanitize import render_markdown
from paw.security.sessions import SessionStore
from paw.services.articles import ArticleService
from paw.services.domains import DomainService
from paw.services.setup import SetupService

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["web"])


async def _current_uid(request: Request, store: SessionStore) -> str | None:
    return await store.get(request.cookies.get(SESSION_COOKIE, ""))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html")


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if await SetupService(session).needs_setup():
        return RedirectResponse("/setup", status_code=307)
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    domains = await DomainService(session).list()
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "dashboard.html", {"domains": domains, "csrf": csrf})


@router.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domain_page(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    sources = await SourceRepo(session).list_by_domain(domain_id)
    latest_source_id = str(sources[-1].id) if sources else None
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "domain.html",
        {
            "domain": domain,
            "articles": articles,
            "csrf": csrf,
            "latest_source_id": latest_source_id,
        },
    )


@router.get("/articles/{article_id}", response_class=HTMLResponse)
async def article_page(
    article_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    body = await ArticleService(session).get_body(article_id)
    revisions = await ArticleRepo(session).list_revisions(article_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "article": body.article,
            "html": render_markdown(body.markdown),
            "markdown": body.markdown,
            "revisions": revisions,
            "csrf": csrf,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "settings.html", {"csrf": csrf})
