import uuid
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    db,
    get_redis,
    get_session_store,
    require_csrf,
    require_role,
)
from paw.api.web.i18n import resolve_ui_lang
from paw.api.web.i18n import t as _t
from paw.db.models import User
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chat import ChatRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.db.repos.users import UserRepo
from paw.security.sanitize import render_markdown, resolve_wikilinks
from paw.security.sessions import SessionStore
from paw.services.api_keys import ApiKeyService
from paw.services.articles import ArticleService
from paw.services.chat import ChatService
from paw.services.domains import DomainService
from paw.services.graph import GraphService
from paw.services.jobs import JobService
from paw.services.maintenance import MaintenanceService
from paw.services.query import QueryService
from paw.services.query_cache import QueryCacheService
from paw.services.settings import SettingsService
from paw.services.setup import SetupService
from paw.services.users import UserService

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Safe defaults so the shared base.html shell never depends on a route passing
# them. page_ctx still overrides these per-request on converted pages (context
# vars shadow env globals); unconverted pages get English nav + hidden switcher.
templates.env.globals["t"] = partial(_t, lang="en")
templates.env.globals["user"] = None
templates.env.globals["ui_lang"] = "en"


def page_ctx(
    request: Request, user: User | None, app_settings: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    """Build a template context with i18n + the current user injected.

    `t` is a one-arg callable bound to the active language so templates call
    `t("settings.title")`. `ui_lang` feeds `<html lang="...">`.
    """
    lang = resolve_ui_lang(user, app_settings)
    return {
        "user": user,
        "csrf": request.cookies.get(CSRF_COOKIE, ""),
        "ui_lang": lang,
        "t": partial(_t, lang=lang),
        **extra,
    }


router = APIRouter(tags=["web"])


async def _require_web_user(
    request: Request, session: AsyncSession, store: SessionStore
) -> User | None:
    """Resolve the logged-in user for web pages, evicting stale sessions.

    Returns None when there is no session or the user was deleted; callers
    redirect to /login. Mirrors api.deps.current_user for the HTML routes.
    """
    sid = request.cookies.get(SESSION_COOKIE, "")
    uid = await store.get(sid)
    if not uid:
        return None
    user = await UserRepo(session).get(uuid.UUID(uid))
    if user is None:
        await store.delete(sid)
        return None
    return user


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, session: AsyncSession = Depends(db)
) -> HTMLResponse:
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request, "login.html", page_ctx(request, None, app_settings)
    )


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    needs_setup = await SetupService(session).needs_setup()
    if needs_setup and not request.cookies.get(SESSION_COOKIE, ""):
        return RedirectResponse("/setup", status_code=307)
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    domains = await DomainService(session).list()
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request, "dashboard.html", page_ctx(request, user, app_settings, domains=domains)
    )


@router.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domain_page(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    sources = await SourceRepo(session).list_by_domain(domain_id)
    tree = await ArticleService(session).domain_tree(domain_id)
    latest_source_id = str(sources[-1].id) if sources else None
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request,
        "domain.html",
        page_ctx(
            request, user, app_settings,
            domain=domain, articles=articles, tree=tree,
            domain_name=domain.name if domain else "",
            latest_source_id=latest_source_id,
        ),
    )


@router.get("/domains/{domain_id}/graph", response_class=HTMLResponse)
async def graph_page(
    domain_id: uuid.UUID,
    request: Request,
    root: uuid.UUID | None = None,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    cfg = await GraphService(session).config_for(domain_id)
    tree = await ArticleService(session).domain_tree(domain_id)
    root_id = root if root is not None else (articles[0].id if articles else None)
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request,
        "graph.html",
        page_ctx(
            request, user, app_settings,
            domain=domain, articles=articles, cfg=cfg, tree=tree,
            domain_name=domain.name if domain else "", root_id=root_id,
        ),
    )


@router.post("/domains/{domain_id}/ingest", response_class=HTMLResponse)
async def web_start_ingest(
    domain_id: uuid.UUID,
    request: Request,
    source_id: uuid.UUID = Form(...),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    # Start the job and return the SSE-wired drawer partial so HTMX swaps a live
    # progress drawer into #job-drawer (not the raw JSON the API endpoint returns).
    job = await JobService(session).start_ingest(domain_id=domain_id, source_id=source_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "_job_drawer.html", {"job_id": job.id, "csrf": csrf})


async def _web_start_maintenance(
    domain_id: uuid.UUID, request: Request, session: AsyncSession, op: str
) -> Response:
    svc = MaintenanceService(session)
    starter = {
        "lint": svc.start_lint,
        "format": svc.start_format,
        "reindex": svc.start_reindex,
        "rebuild_graph": svc.start_graph_rebuild,
    }[op]
    job = await starter(domain_id=domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "_job_drawer.html", {"job_id": job.id, "csrf": csrf})


@router.post("/domains/{domain_id}/lint", response_class=HTMLResponse)
async def web_lint(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "lint")


@router.post("/domains/{domain_id}/format", response_class=HTMLResponse)
async def web_format(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "format")


@router.post("/domains/{domain_id}/reindex", response_class=HTMLResponse)
async def web_reindex(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "reindex")


@router.post("/domains/{domain_id}/rebuild-graph", response_class=HTMLResponse)
async def web_rebuild_graph(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "rebuild_graph")


@router.get("/domains/{domain_id}/lint/{job_id}/results", response_class=HTMLResponse)
async def web_lint_results(
    domain_id: uuid.UUID,
    job_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    job = await JobRepo(session).get(job_id)
    issues: list[dict[str, object]] = []
    if job is not None:
        for entry in job.log:
            if entry.get("step") == "issues":
                issues = entry.get("issues", [])
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "_lint_results.html",
        {"domain_id": domain_id, "issues": issues, "csrf": csrf, "user": user},
    )


@router.post("/domains/{domain_id}/fix", response_class=HTMLResponse)
async def web_fix(
    domain_id: uuid.UUID,
    request: Request,
    issue_ids: list[str] = Form(default=[]),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    job = await MaintenanceService(session).start_fix(domain_id=domain_id, issue_ids=issue_ids)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "_job_drawer.html", {"job_id": job.id, "csrf": csrf})


@router.get("/domains/{domain_id}/query", response_class=HTMLResponse)
async def query_page(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request, "query.html", {"domain": domain, "csrf": csrf, "user": user}
    )


@router.post("/domains/{domain_id}/query", response_class=HTMLResponse)
async def web_query(
    domain_id: uuid.UUID,
    request: Request,
    q: str = Form(...),
    refresh: int = 0,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor", "viewer")),
) -> Response:
    csrf = request.cookies.get(CSRF_COOKIE, "")
    csvc = QueryCacheService(session).with_redis(get_redis())
    qsvc = QueryService(session).with_redis(get_redis())
    cfg = await csvc.config(domain_id)

    if cfg.enabled and not refresh:
        hit = await csvc.lookup(domain_id=domain_id, question=q, cfg=cfg)
        if hit is not None:
            await csvc.touch(hit.id)
            return templates.TemplateResponse(
                request,
                "_query_result.html",
                {
                    "answer_html": render_markdown(hit.answer_md),
                    "refs": hit.refs,
                    "passages": hit.passages,
                    "cached": True,
                    "stale": hit.stale,
                    "domain_id": domain_id,
                    "question": q,
                    "csrf": csrf,
                },
            )

    prepared = await qsvc.prepare(domain_id=domain_id, question=q)
    answer = await qsvc.complete(prepared)
    if cfg.enabled and answer.refs:
        await csvc.upsert(
            domain_id=domain_id, question=q, answer_md=answer.answer_md,
            refs=answer.refs, passages=answer.passages, model=prepared.chat_model,
        )
    return templates.TemplateResponse(
        request,
        "_query_result.html",
        {
            "answer_html": render_markdown(answer.answer_md),
            "refs": answer.refs,
            "passages": answer.passages,
            "cached": False,
            "stale": False,
            "domain_id": domain_id,
            "question": q,
            "csrf": csrf,
        },
    )


@router.get("/domains/{domain_id}/suggest", response_class=HTMLResponse)
async def web_suggest(
    domain_id: uuid.UUID,
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return HTMLResponse("")
    domain = await DomainRepo(session).get(domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    svc = QueryCacheService(session)
    cfg = await svc.config(domain_id)
    suggestions = await svc.suggest(domain_id=domain_id, q=q, top_k=cfg.suggest_top_k)
    return templates.TemplateResponse(
        request,
        "_suggestions.html",
        {"domain": domain, "suggestions": suggestions, "csrf": csrf, "user": user},
    )


@router.get("/articles/{article_id}", response_class=HTMLResponse)
async def article_page(
    article_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    svc = ArticleService(session)
    body = await svc.get_body(article_id)
    meta = await svc.get_meta(article_id)
    tree = await svc.domain_tree(body.article.domain_id)
    slug_map = await svc.slug_map(body.article.domain_id)
    domain = await DomainRepo(session).get(body.article.domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "article": body.article,
            "html": render_markdown(resolve_wikilinks(body.markdown, slug_map)),
            "markdown": body.markdown,
            "meta": meta,
            "tree": tree,
            "domain_name": domain.name if domain else "",
            "csrf": csrf,
            "user": user,
        },
    )


@router.post("/articles/{article_id}/rollback")
async def web_rollback(
    article_id: uuid.UUID,
    rev_no: int = Form(...),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    user: User = Depends(require_role("admin", "editor")),
) -> Response:
    await ArticleService(session).rollback(
        article_id=article_id, rev_no=rev_no, author_id=user.id
    )
    # HTMX reloads the page so the new revision + metadata sections refresh.
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/api-keys/issue", response_class=HTMLResponse)
async def web_issue_api_key(
    request: Request,
    session: AsyncSession = Depends(db),
    user: User = Depends(require_role("admin", "editor", "viewer")),
    _: None = Depends(require_csrf),
) -> Response:
    issued = await ApiKeyService(session).issue(user_id=user.id, scopes=["read"])
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request, "_apikey_issued.html",
        page_ctx(request, user, app_settings, token=issued.token, prefix=issued.prefix),
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    app_settings = await SettingsService(session).get()
    users = await UserService(session).list() if user.role == "admin" else []
    keys = await ApiKeyService(session).list(user.id)
    return templates.TemplateResponse(
        request, "settings.html",
        page_ctx(request, user, app_settings, users=users, api_keys=keys),
    )


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    sessions = await ChatRepo(session).list_by_user(user.id, limit=50)
    domains = await DomainService(session).list()
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "sessions": sessions,
            "domains": domains,
            "session": None,
            "messages": [],
            "csrf": csrf,
            "user": user,
        },
    )


@router.get("/chat/{session_id}", response_class=HTMLResponse)
async def chat_session_page(
    session_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
    svc = ChatService(session)
    sess = await svc.get_owned(session_id=session_id, user_id=user.id)  # 404 if not owned
    rows = await svc.session_messages(session_id)
    messages = [
        {"role": m.role, "content": m.content, "html": render_markdown(m.content)} for m in rows
    ]
    sessions = await ChatRepo(session).list_by_user(user.id, limit=50)
    domains = await DomainService(session).list()
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "sessions": sessions,
            "domains": domains,
            "session": sess,
            "messages": messages,
            "csrf": csrf,
            "user": user,
        },
    )


@router.post("/chat", response_class=HTMLResponse)
async def web_chat(
    request: Request,
    q: str = Form(...),
    domain_id: uuid.UUID | None = Form(None),
    session_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    user: User = Depends(require_role("admin", "editor", "viewer")),
) -> Response:
    svc = ChatService(session)
    is_new = session_id is None
    sess = await svc.resolve_session(user=user, domain_id=domain_id, session_id=session_id)
    prepared = await svc.prepare_turn(session=sess, question=q)
    turn, usage = await svc.complete_turn(prepared)
    await svc.record_turn(
        session=sess, question=q, answer_md=turn.answer_md, refs=turn.refs,
        model=prepared.model, prompt_version=prepared.prompt_version, usage=usage,
    )
    return templates.TemplateResponse(
        request,
        "_chat_turn.html",
        {
            "question": q,
            "answer_html": render_markdown(turn.answer_md),
            "refs": turn.refs,
            "new_session_id": str(sess.id) if is_new else None,
        },
    )
