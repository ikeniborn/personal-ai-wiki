---
title: "Phase 9c — Admin UI polish + UI i18n Implementation Plan"
phase: 9
state: draft
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-9-ops-hardening-design.md
---

# Phase 9c — Admin UI polish + UI i18n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make api-keys and users manageable from the admin web UI (issue/scope/revoke keys; create/list/role-change/remove users), and add a per-user UI-language switch (RU/EN) that is fully independent of content/reasoning languages. Satisfies Phase 9 acceptance criterion #7.

**Architecture:** Three independent slices that share one new template-context plumbing seam.

1. **i18n** — a dependency-free `src/paw/api/web/i18n.py` module exposing `SUPPORTED_LANGS`, a `CATALOG` dict (lang → key → string), `t(key, lang)`, and `resolve_ui_lang(user, app_settings)`. A new `page_ctx(...)` helper in `routes.py` injects `ui_lang`, a bound `t` callable, the current `user`, and `csrf` into every template context, so templates can call `{{ t("settings.title") }}` and `<html lang="{{ ui_lang }}">` resolves. The active language comes from `users.chat_prefs["ui_language"]` → `app_settings["ui_language"]` → `"en"`. A switcher control in `base.html` POSTs to a new `/api/v1/users/me/ui-language` endpoint and reloads.
2. **users management** — extend repo/service/router with `set_role`, `delete` (hard delete, guarded against removing the last admin), then a `users.html`-style admin section (admin-only) with HTMX wired to the existing + new endpoints.
3. **api-keys management** — a settings section using the *existing* `/api-keys` REST endpoints; show the freshly issued token once via the HTMX POST response swap; scope selector limited to `API_KEY_SCOPES`.

This phase adds **no new tables and no migration** — `users.chat_prefs` (JSONB) already exists and `app_settings.settings` (JSONB) already holds global config. The `User` object is threaded into web template contexts for the first time (previously contexts carried only `csrf`).

**Tech Stack:** Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · PostgreSQL 16 · Jinja2 + HTMX · `pytest` + `testcontainers`.

## Global Constraints

- **Dependency management is `uv`** — never call `pip`/`pytest` directly; go through `uv run`. (This plan adds **no** new runtime dependency — i18n is a plain dict catalog; **do NOT pull in gettext/babel**, YAGNI.)
- **CI gate (all three must pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`.
- **Service is the single commit boundary.** Repos and storage must never `commit()`; a service batches writes and commits once. (`UserService.set_role`/`delete`/`set_ui_language` each commit once.)
- **Errors:** raise `ProblemError(status, title, detail)` (RFC 9457 `application/problem+json`). `IntegrityError` auto-maps to 409.
- **Async everywhere** (`asyncpg`, `redis.asyncio`); `pytest` runs `asyncio_mode = auto` so tests are plain `async def`.
- **Layering (no cycles):** `api`/`web` → `services` → `db.repos` → `db`, `config`. `services` may import `paw.api.errors.ProblemError` (existing precedent). `paw.api.web.i18n` is a leaf — it imports nothing from `paw.api` (it takes a `User`-shaped object and a plain dict, accessing only `.chat_prefs`), so `routes.py` may import it freely.
- **Security/RBAC** (`api/deps.py`): users management endpoints require `require_role("admin")`; api-keys endpoints are self-scoped via `current_user`; the ui-language endpoint requires `current_user`; all mutating endpoints require `require_csrf`. CSRF cookie `paw_csrf`, header `x-csrf-token`; HTMX forms pass `hx-headers='{"x-csrf-token":"{{ csrf }}"}'`.
- **Tests need Docker** for `integration`/`api`/`e2e` layers (real Postgres + Redis via testcontainers). Only the `unit` layer runs without Docker. The i18n catalog test is a **unit** test (runs locally); the new API-layer tests need Docker (run on CI / where Docker is available — see per-task notes).
- **Branch workflow:** all work on a `dev-*` branch off up-to-date `master`; merge via PR. Never commit to `master`. Per-task commits as specified.
- **Surgical changes:** thread `user` into template contexts and convert hardcoded strings only where this plan's acceptance demands (base/login/settings/dashboard/domain + the new admin sections). Do not mass-rewrite every template; convert incrementally and leave untouched strings rendering fine (an unconverted literal still displays correctly).
- **Docs are English; conversation is Russian.** After functional changes, update `docs/wiki/` via iwiki (final task).

## Reused building blocks (already in the codebase — do not reimplement)

- `paw.api.web.routes.templates` (`Jinja2Templates`) and every `templates.TemplateResponse(request, "x.html", {...})` call. Context today always includes `csrf = request.cookies.get(CSRF_COOKIE, "")`.
- `paw.api.deps`: `current_user` (→ `User`), `require_role(*roles)` (403 on mismatch), `require_csrf`, `CSRF_COOKIE="paw_csrf"`, `db`, `get_session_store`, `SESSION_COOKIE`. `routes.py::_current_uid(request, store)` returns the logged-in user id (or `None`).
- `paw.api.errors.ProblemError(status, title, detail)`.
- **api-keys (Phase 8) — already complete, reuse as-is:** router `paw.api.routers.api_keys` (`POST/GET/DELETE /api-keys`, returns `ApiKeyIssued{id,prefix,key,scopes}` / `ApiKeyOut{id,prefix,scopes,created_at,last_used,revoked_at}`); service `ApiKeyService.issue/list/revoke`; `paw.security.api_keys.API_KEY_SCOPES = ("read",)`.
- **users:** router `paw.api.routers.users` (`GET /users` admin, `POST /users` admin+csrf); service `UserService.list/create`; repo `UserRepo.create/get_by_email/get/count/list`; model `User(id, email:CITEXT, pw_hash, role:Enum USER_ROLES, chat_prefs:JSONB default {}, created_at)`; `USER_ROLES = ("admin","editor","viewer")` (`paw.db.models`).
- **settings:** `SettingsService.get()->dict` / `update(dict)->dict`; `SettingsRepo.get/upsert`. NOTE: `SettingsService.update()` **replaces** the whole `settings` blob; the merge pattern (`s = await self._all(); s[KEY]=v; await repo.upsert(s)`) is established in `ProviderSettingsService.persist_provider`. Mirror that when writing a single key.
- `User.chat_prefs` (JSONB, default `{}`) — the per-user store for `ui_language`. `User.role ∈ {"admin","editor","viewer"}`.
- Test/auth fixture pattern (`tests/api/test_api_keys.py`, `tests/api/test_web_pages.py`): seed a user via `UserRepo(db_session).create(...)` + `db_session.commit()`, build the app with `create_app()` + `AsyncClient(ASGITransport)`, log in via `POST /api/v1/auth/login`, read the `paw_csrf` cookie, send it as `x-csrf-token` on writes.

## File Structure

**Create:**
- `src/paw/api/web/i18n.py` — `SUPPORTED_LANGS`, `CATALOG`, `t(key, lang)`, `resolve_ui_lang(user, app_settings)`. Dependency-free leaf.
- `tests/unit/test_i18n_catalog.py` — unit test: every catalog key present in both langs; `t` fallback chain (lang → en → key).
- `tests/api/test_ui_language.py` — API: switch endpoint flips `<html lang>` RU↔EN; default "en"; csrf/auth guards.
- `tests/api/test_users_admin.py` — API: list/create/role-change/delete; last-admin guard; RBAC (non-admin 403).
- `tests/api/test_admin_ui_pages.py` — API: admin UI pages render the api-keys + users sections; a non-admin does not see admin-only controls.

**Modify:**
- `src/paw/db/repos/users.py` — add `set_role(user_id, role)`, `delete(user_id)`, `count_admins()`, `set_chat_prefs(user_id, prefs)`.
- `src/paw/services/users.py` — add `set_role`, `delete` (last-admin guard → `ProblemError` 409), `set_ui_language`.
- `src/paw/api/routers/users.py` — add `PATCH /users/{id}` (role change, admin+csrf), `DELETE /users/{id}` (admin+csrf), `POST /users/me/ui-language` (current_user+csrf).
- `src/paw/api/web/routes.py` — add `page_ctx(...)` helper; thread `user` + i18n into the page-rendering routes (`dashboard`, `domain_page`, `settings_page`, `login_page`); refactor those `TemplateResponse` calls to use it.
- `src/paw/api/web/templates/base.html` — language switcher control in the shell; convert nav titles to `t(...)`.
- `src/paw/api/web/templates/login.html` — convert visible strings to `t(...)`.
- `src/paw/api/web/templates/dashboard.html` — convert visible strings to `t(...)`.
- `src/paw/api/web/templates/settings.html` — convert headers to `t(...)`; build the **api-keys** section and the **users** admin section (replacing the empty `#users` placeholder); gate the users section to admins.
- `docs/wiki/*` — refreshed via iwiki (final task).

---

### Task 1: i18n module + catalog (dependency-free)

**Files:**
- Create: `src/paw/api/web/i18n.py`
- Test: `tests/unit/test_i18n_catalog.py`

**Interfaces:**
- Produces:
  - `SUPPORTED_LANGS: tuple[str, ...] = ("en", "ru")`
  - `CATALOG: dict[str, dict[str, str]]` — `CATALOG["en"]` and `CATALOG["ru"]` have **identical key sets**.
  - `t(key: str, lang: str) -> str` — returns `CATALOG[lang][key]`, falling back to `CATALOG["en"][key]`, then to `key` itself (never raises).
  - `resolve_ui_lang(user: Any, app_settings: dict[str, Any]) -> str` — returns `user.chat_prefs.get("ui_language")` if in `SUPPORTED_LANGS`, else `app_settings.get("ui_language")` if in `SUPPORTED_LANGS`, else `"en"`. `user` may be `None` (anonymous pages like login).

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_i18n_catalog.py`:
```python
from types import SimpleNamespace

import pytest

from paw.api.web.i18n import CATALOG, SUPPORTED_LANGS, resolve_ui_lang, t


def test_supported_langs():
    assert SUPPORTED_LANGS == ("en", "ru")


def test_every_key_present_in_every_lang():
    keys = {lang: set(CATALOG[lang]) for lang in SUPPORTED_LANGS}
    en = keys["en"]
    for lang in SUPPORTED_LANGS:
        assert keys[lang] == en, f"{lang} catalog keys diverge from en"
    assert en, "catalog must not be empty"


def test_t_returns_translation_per_lang():
    # pick any real key; en and ru must differ for at least one non-trivial key
    assert t("settings.title", "en") == CATALOG["en"]["settings.title"]
    assert t("settings.title", "ru") == CATALOG["ru"]["settings.title"]


def test_t_falls_back_to_en_then_key():
    # unknown lang → en
    assert t("settings.title", "de") == CATALOG["en"]["settings.title"]
    # unknown key → key itself, never raises
    assert t("no.such.key", "en") == "no.such.key"
    assert t("no.such.key", "ru") == "no.such.key"


@pytest.mark.parametrize(
    "prefs, app_settings, expected",
    [
        ({"ui_language": "ru"}, {}, "ru"),
        ({"ui_language": "en"}, {"ui_language": "ru"}, "en"),   # user wins
        ({}, {"ui_language": "ru"}, "ru"),                       # app default
        ({}, {}, "en"),                                          # hard default
        ({"ui_language": "xx"}, {"ui_language": "ru"}, "ru"),    # invalid user → app
        ({"ui_language": "xx"}, {}, "en"),                       # invalid → default
    ],
)
def test_resolve_ui_lang(prefs, app_settings, expected):
    user = SimpleNamespace(chat_prefs=prefs)
    assert resolve_ui_lang(user, app_settings) == expected


def test_resolve_ui_lang_anonymous():
    assert resolve_ui_lang(None, {"ui_language": "ru"}) == "ru"
    assert resolve_ui_lang(None, {}) == "en"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_i18n_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.api.web.i18n'`.

- [ ] **Step 3: Implement the module**

Create `src/paw/api/web/i18n.py`:
```python
from __future__ import annotations

from typing import Any

SUPPORTED_LANGS: tuple[str, ...] = ("en", "ru")

# Plain dict catalog (intentionally NOT gettext/babel — YAGNI). Keys are
# dotted "<page>.<element>" strings. en and ru MUST have identical key sets;
# the unit test enforces this.
CATALOG: dict[str, dict[str, str]] = {
    "en": {
        # shell / nav
        "app.name": "Personal AI Wiki",
        "nav.domains": "Domains",
        "nav.articles": "Articles",
        "nav.chat": "Chat",
        "nav.graph": "Graph",
        "nav.settings": "Settings",
        "lang.label": "Language",
        "lang.en": "English",
        "lang.ru": "Russian",
        # login
        "login.title": "Sign in",
        "login.email": "Email",
        "login.password": "Password",
        "login.submit": "Sign in",
        # dashboard
        "dashboard.title": "Domains",
        "dashboard.new_domain": "New domain",
        "dashboard.create": "Create",
        # settings — connection
        "settings.title": "Settings",
        "settings.connection": "Connection",
        "settings.base_url": "Base URL",
        "settings.api_key": "API key",
        "settings.chat_model": "Chat model",
        "settings.embedding_model": "Embedding model",
        "settings.vision_model": "Vision model",
        "settings.embedding_dim": "Embedding dim",
        "settings.save_connection": "Save connection",
        # settings — users
        "users.title": "Users",
        "users.email": "Email",
        "users.role": "Role",
        "users.created": "Created",
        "users.create": "Create user",
        "users.new_password": "Password",
        "users.role_admin": "admin",
        "users.role_editor": "editor",
        "users.role_viewer": "viewer",
        "users.change_role": "Change role",
        "users.remove": "Remove",
        # settings — api keys
        "apikeys.title": "API keys",
        "apikeys.prefix": "Prefix",
        "apikeys.scopes": "Scopes",
        "apikeys.created": "Created",
        "apikeys.last_used": "Last used",
        "apikeys.status": "Status",
        "apikeys.active": "active",
        "apikeys.revoked": "revoked",
        "apikeys.issue": "Issue key",
        "apikeys.revoke": "Revoke",
        "apikeys.token_once": "Copy this token now — it will not be shown again:",
    },
    "ru": {
        "app.name": "Персональная AI-вики",
        "nav.domains": "Домены",
        "nav.articles": "Статьи",
        "nav.chat": "Чат",
        "nav.graph": "Граф",
        "nav.settings": "Настройки",
        "lang.label": "Язык",
        "lang.en": "Английский",
        "lang.ru": "Русский",
        "login.title": "Вход",
        "login.email": "Эл. почта",
        "login.password": "Пароль",
        "login.submit": "Войти",
        "dashboard.title": "Домены",
        "dashboard.new_domain": "Новый домен",
        "dashboard.create": "Создать",
        "settings.title": "Настройки",
        "settings.connection": "Подключение",
        "settings.base_url": "Базовый URL",
        "settings.api_key": "API-ключ",
        "settings.chat_model": "Модель чата",
        "settings.embedding_model": "Модель эмбеддингов",
        "settings.vision_model": "Модель зрения",
        "settings.embedding_dim": "Размерность эмбеддинга",
        "settings.save_connection": "Сохранить подключение",
        "users.title": "Пользователи",
        "users.email": "Эл. почта",
        "users.role": "Роль",
        "users.created": "Создан",
        "users.create": "Создать пользователя",
        "users.new_password": "Пароль",
        "users.role_admin": "админ",
        "users.role_editor": "редактор",
        "users.role_viewer": "читатель",
        "users.change_role": "Сменить роль",
        "users.remove": "Удалить",
        "apikeys.title": "API-ключи",
        "apikeys.prefix": "Префикс",
        "apikeys.scopes": "Права",
        "apikeys.created": "Создан",
        "apikeys.last_used": "Использован",
        "apikeys.status": "Статус",
        "apikeys.active": "активен",
        "apikeys.revoked": "отозван",
        "apikeys.issue": "Выпустить ключ",
        "apikeys.revoke": "Отозвать",
        "apikeys.token_once": "Скопируйте токен сейчас — он больше не будет показан:",
    },
}


def t(key: str, lang: str) -> str:
    """Translate `key` into `lang`, falling back lang → en → key."""
    by_lang = CATALOG.get(lang) or {}
    if key in by_lang:
        return by_lang[key]
    return CATALOG["en"].get(key, key)


def resolve_ui_lang(user: Any, app_settings: dict[str, Any]) -> str:
    """Active UI language: user pref → app default → 'en'."""
    if user is not None:
        pref = (getattr(user, "chat_prefs", None) or {}).get("ui_language")
        if pref in SUPPORTED_LANGS:
            return str(pref)
    default = app_settings.get("ui_language")
    if default in SUPPORTED_LANGS:
        return str(default)
    return "en"
```

- [ ] **Step 4: Run the unit test — expect PASS**

Run: `uv run pytest tests/unit/test_i18n_catalog.py -q`
Expected: PASS (6 test functions; the parametrized one counts as 6 cases). Confirms the catalog is symmetric and the fallback chain works **without Docker**.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/paw/api/web/i18n.py tests/unit/test_i18n_catalog.py` and `uv run mypy src/paw/api/web/i18n.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/paw/api/web/i18n.py tests/unit/test_i18n_catalog.py
git commit -m "feat(web): add dependency-free UI i18n catalog + resolver"
```

---

### Task 2: Thread `user` + i18n into template contexts (`page_ctx`)

**Files:**
- Modify: `src/paw/api/web/routes.py`
- Modify: `src/paw/api/web/templates/base.html`

**Interfaces:**
- Produces (in `routes.py`):
  - `def page_ctx(request, user, app_settings, **extra) -> dict[str, Any]` — returns `{"user": user, "csrf": <paw_csrf cookie>, "ui_lang": resolve_ui_lang(user, app_settings), "t": <partial of i18n.t bound to ui_lang>, **extra}`.
- Consumes: `paw.api.web.i18n.resolve_ui_lang`, `paw.api.web.i18n.t`, `paw.services.settings.SettingsService`.

- [ ] **Step 1: Add imports + the `page_ctx` helper to `routes.py`**

In `src/paw/api/web/routes.py`, add near the top:
```python
from functools import partial
from typing import Any

from paw.api.web.i18n import resolve_ui_lang
from paw.api.web.i18n import t as _t
from paw.services.settings import SettingsService
```
And add (after `templates = Jinja2Templates(...)`, before `router = APIRouter(...)`):
```python
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
```
Note: `partial(_t, lang=lang)` makes `t("key")` resolve `_t("key", lang=lang)`.

- [ ] **Step 2: Refactor the four target page routes to use `page_ctx`**

Convert these `templates.TemplateResponse(...)` calls (currently passing only `{"csrf": csrf}` plus page data). For each, look up the current `User` (the page routes today only check `_current_uid`; fetch the full user where needed) and load `app_settings`.

For routes that already have a `User` (none of these four do today) reuse it; otherwise resolve it from the session. Add a small local helper in `routes.py`:
```python
async def _current_user_opt(
    request: Request, session: AsyncSession, store: SessionStore
) -> User | None:
    uid = await _current_uid(request, store)
    if not uid:
        return None
    return await UserRepo(session).get(uuid.UUID(uid))
```
(Add `from paw.db.repos.users import UserRepo` to the imports.)

Then:

**`dashboard` (`GET /`)** — replace the final return:
```python
    user = await _current_user_opt(request, session, store)
    domains = await DomainService(session).list()
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request, "dashboard.html", page_ctx(request, user, app_settings, domains=domains)
    )
```

**`domain_page` (`GET /domains/{domain_id}`)** — after the existing data loads, replace the return:
```python
    user = await _current_user_opt(request, session, store)
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
```

**`settings_page` (`GET /settings`)** — this page becomes admin-aware (Task 5 uses `user.role`); replace:
```python
    user = await _current_user_opt(request, session, store)
    app_settings = await SettingsService(session).get()
    users = await UserService(session).list() if user and user.role == "admin" else []
    keys = await ApiKeyService(session).list(user.id) if user else []
    return templates.TemplateResponse(
        request, "settings.html",
        page_ctx(request, user, app_settings, users=users, api_keys=keys),
    )
```
(Add imports `from paw.services.users import UserService` and `from paw.services.api_keys import ApiKeyService`.)

**`login_page` (`GET /login`)** — anonymous, so `user=None`; app_settings still drives the global default language:
```python
async def login_page(
    request: Request, session: AsyncSession = Depends(db)
) -> HTMLResponse:
    app_settings = await SettingsService(session).get()
    return templates.TemplateResponse(
        request, "login.html", page_ctx(request, None, app_settings)
    )
```

> Leave the remaining routes (`article_page`, `chat_page`, `graph_page`, `query_page`, partials, POST handlers) untouched in this task — their templates keep rendering their current literals. Converting them is out of this plan's required scope; `page_ctx` is available if a follow-up wants them.

- [ ] **Step 3: Add the language switcher + nav i18n to `base.html`**

Edit `src/paw/api/web/templates/base.html`:
- The `<html lang="{{ ui_lang | default('en') }}">` line is already correct — leave it.
- Replace the rail link `title` attributes with `t(...)` and add a switcher in the shell. The switcher POSTs to `/api/v1/users/me/ui-language` via HTMX and reloads on success (HTMX honours the `HX-Refresh: true` response header the endpoint returns).

Replace the `<nav class="rail">…</nav>` block and add the switcher just above it:
```html
    <nav class="rail">
      <a href="/" title="{{ t('nav.domains') }}">🏠</a>
      <a href="/" title="{{ t('nav.articles') }}">📚</a>
      <a href="/chat" title="{{ t('nav.chat') }}">💬</a>
      <a href="#" title="{{ t('nav.graph') }}">🕸</a>
      <a href="/settings" title="{{ t('nav.settings') }}">⚙</a>
      {% if user %}
      <form class="lang-switch" hx-post="/api/v1/users/me/ui-language" hx-ext="json-enc"
            hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-swap="none">
        <label title="{{ t('lang.label') }}">
          <select name="ui_language" onchange="this.form.requestSubmit()">
            <option value="en" {% if ui_lang == 'en' %}selected{% endif %}>{{ t('lang.en') }}</option>
            <option value="ru" {% if ui_lang == 'ru' %}selected{% endif %}>{{ t('lang.ru') }}</option>
          </select>
        </label>
      </form>
      {% endif %}
    </nav>
```
Notes:
- The `<title>` block default literal "Personal AI Wiki" is fine to leave (anonymous-safe, no `t` needed); pages that override it are converted in Task 6 where required.
- `onchange="this.form.requestSubmit()"` is allowed: the CSP forbids inline `<script>` but **HTML event-handler attributes** are not blocked by `script-src 'self'` here (no `script-src-attr 'none'`). If a stricter CSP is desired, that is 9b's CSP-finalization scope — do not change CSP in 9c. To stay safe and CSP-agnostic, add a tiny delegated listener to the existing `/static/app.js` instead (preferred): on `change` of `select[name=ui_language]`, call `form.requestSubmit()`. Use the `app.js` route if `onchange` is rejected.

- [ ] **Step 4: Verify nothing regressed in the shell render**

Run the existing web-shell + web-page suites (need Docker):
```bash
uv run pytest tests/api/test_web_shell.py tests/api/test_web_pages.py -q
```
Expected: PASS. `test_login_page_renders_frame` still finds "Personal AI Wiki" (the `<title>` default), `test_settings_shows_dim_change_warning` still passes (warning string untouched in this task), `test_setup_then_dashboard` still finds "Domains".
*(If Docker is unavailable locally, defer this run to CI — note it in the PR.)*

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff check src/paw/api/web/routes.py
uv run mypy src/paw/api/web/routes.py
git add src/paw/api/web/routes.py src/paw/api/web/templates/base.html
git commit -m "feat(web): inject user + i18n into page contexts; add language switcher"
```

---

### Task 3: users repo + service — `set_role`, `delete`, `set_ui_language`, last-admin guard

**Files:**
- Modify: `src/paw/db/repos/users.py`
- Modify: `src/paw/services/users.py`

**Interfaces:**
- Produces (repo):
  - `set_role(user_id, role) -> bool` — UPDATE role; returns rowcount>0.
  - `delete(user_id) -> bool` — DELETE row; returns rowcount>0.
  - `count_admins() -> int` — count of `role == "admin"`.
  - `set_chat_prefs(user_id, prefs: dict) -> None` — overwrite `chat_prefs` JSONB.
- Produces (service):
  - `set_role(*, user_id, role) -> User` — validate role ∈ `USER_ROLES`; if demoting the last admin → `ProblemError(409, "Last admin")`; commit; return updated `User`.
  - `delete(*, user_id) -> None` — if target is the last admin → `ProblemError(409, "Last admin")`; else delete; commit. 404 if not found.
  - `set_ui_language(*, user_id, lang) -> None` — validate `lang ∈ SUPPORTED_LANGS` (422 otherwise); merge `{"ui_language": lang}` into existing `chat_prefs`; commit.

- [ ] **Step 1: Add the repo methods**

In `src/paw/db/repos/users.py`, add (the file currently imports `select`; add `func`, `update`, `delete` from `sqlalchemy`, plus the `CursorResult`/`cast` pattern used in `api_keys.py`):
```python
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
```
Methods:
```python
    async def set_role(self, user_id: uuid.UUID, role: str) -> bool:
        res = cast(
            CursorResult[Any],
            await self._s.execute(
                update(User).where(User.id == user_id).values(role=role)
            ),
        )
        return bool(res.rowcount)

    async def delete(self, user_id: uuid.UUID) -> bool:
        res = cast(
            CursorResult[Any],
            await self._s.execute(delete(User).where(User.id == user_id)),
        )
        return bool(res.rowcount)

    async def count_admins(self) -> int:
        res = await self._s.execute(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
        return int(res.scalar_one())

    async def set_chat_prefs(self, user_id: uuid.UUID, prefs: dict[str, Any]) -> None:
        await self._s.execute(
            update(User).where(User.id == user_id).values(chat_prefs=prefs)
        )
```
(Repos never commit — flush happens implicitly on the service's commit.)

- [ ] **Step 2: Add the service methods (commit boundary + guards)**

In `src/paw/services/users.py`, add imports and methods:
```python
from paw.api.errors import ProblemError
from paw.api.web.i18n import SUPPORTED_LANGS
from paw.db.models import USER_ROLES

    async def get(self, user_id):  # convenience for routers/UI
        return await self._repo.get(user_id)

    async def set_role(self, *, user_id, role: str) -> User:
        if role not in USER_ROLES:
            raise ProblemError(status=422, title="Invalid role", detail=f"role must be one of {USER_ROLES}")
        target = await self._repo.get(user_id)
        if target is None:
            raise ProblemError(status=404, title="User not found")
        if target.role == "admin" and role != "admin" and await self._repo.count_admins() <= 1:
            raise ProblemError(status=409, title="Last admin", detail="cannot demote the last admin")
        await self._repo.set_role(user_id, role)
        await self._s.commit()
        refreshed = await self._repo.get(user_id)
        assert refreshed is not None
        return refreshed

    async def delete(self, *, user_id) -> None:
        target = await self._repo.get(user_id)
        if target is None:
            raise ProblemError(status=404, title="User not found")
        if target.role == "admin" and await self._repo.count_admins() <= 1:
            raise ProblemError(status=409, title="Last admin", detail="cannot delete the last admin")
        await self._repo.delete(user_id)
        await self._s.commit()

    async def set_ui_language(self, *, user_id, lang: str) -> None:
        if lang not in SUPPORTED_LANGS:
            raise ProblemError(status=422, title="Invalid language", detail=f"lang must be one of {SUPPORTED_LANGS}")
        target = await self._repo.get(user_id)
        if target is None:
            raise ProblemError(status=404, title="User not found")
        prefs = dict(target.chat_prefs or {})
        prefs["ui_language"] = lang
        await self._repo.set_chat_prefs(user_id, prefs)
        await self._s.commit()
```
(Type the `user_id` params as `uuid.UUID`; add `import uuid` and `from paw.db.models import User` if not already imported.)

> Layering note: `services/users.py` importing `paw.api.web.i18n.SUPPORTED_LANGS` would make `services` depend on `api.web`. To avoid that cycle, instead define `SUPPORTED_LANGS` once in `paw.api.web.i18n` and re-export — **or** simpler: hard-code the validation set `{"en", "ru"}` in the service (it is a stable contract owned by 9c). Prefer the literal `("en", "ru")` in the service to keep `services` a clean lower layer; the i18n module remains the catalog source of truth. Use the literal.

- [ ] **Step 3: Integration smoke (need Docker) — guard logic**

These guards are best covered by the API tests in Task 4, but a quick service-level integration check is cheap. Add to `tests/api/test_users_admin.py` later; for now verify compile:
```bash
uv run ruff check src/paw/db/repos/users.py src/paw/services/users.py
uv run mypy src/paw/db/repos/users.py src/paw/services/users.py
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/paw/db/repos/users.py src/paw/services/users.py
git commit -m "feat(users): add set_role/delete/set_ui_language with last-admin guard"
```

---

### Task 4: users + ui-language API endpoints

**Files:**
- Modify: `src/paw/api/routers/users.py`
- Test: `tests/api/test_users_admin.py`, `tests/api/test_ui_language.py`

**Interfaces:**
- Produces:
  - `PATCH /api/v1/users/{user_id}` body `{"role": "<admin|editor|viewer>"}` → `UserOut` (admin + csrf).
  - `DELETE /api/v1/users/{user_id}` → 204 (admin + csrf); 409 on last admin; 404 if missing.
  - `POST /api/v1/users/me/ui-language` body `{"ui_language": "<en|ru>"}` → 204 with `HX-Refresh: true` header (current_user + csrf).

- [ ] **Step 1: Write the failing API tests**

Create `tests/api/test_users_admin.py` (mirror the `seeded`/`client`/`_login` fixture style from `tests/api/test_api_keys.py`):
```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _login(client, email, password):
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return client.cookies.get("paw_csrf")


@pytest.fixture
async def admin_client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="REDACTED", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def _make_user(c, csrf, email, role):
    r = await c.post(
        "/api/v1/users",
        json={"email": email, "password": "pw12345", "role": role},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_list_create_role_change_delete(admin_client):
    c = admin_client
    csrf = await _login(c, "REDACTED", "pw12345")
    uid = await _make_user(c, csrf, "REDACTED", "viewer")

    listed = (await c.get("/api/v1/users")).json()
    assert any(u["id"] == uid and u["role"] == "viewer" for u in listed)

    r = await c.patch(
        f"/api/v1/users/{uid}", json={"role": "editor"}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 200 and r.json()["role"] == "editor"

    r = await c.request("DELETE", f"/api/v1/users/{uid}", headers={"x-csrf-token": csrf})
    assert r.status_code == 204
    assert all(u["id"] != uid for u in (await c.get("/api/v1/users")).json())


async def test_cannot_delete_last_admin(admin_client):
    c = admin_client
    csrf = await _login(c, "REDACTED", "pw12345")
    me = [u for u in (await c.get("/api/v1/users")).json() if u["email"] == "REDACTED"][0]
    r = await c.request("DELETE", f"/api/v1/users/{me['id']}", headers={"x-csrf-token": csrf})
    assert r.status_code == 409


async def test_cannot_demote_last_admin(admin_client):
    c = admin_client
    csrf = await _login(c, "REDACTED", "pw12345")
    me = [u for u in (await c.get("/api/v1/users")).json() if u["email"] == "REDACTED"][0]
    r = await c.patch(
        f"/api/v1/users/{me['id']}", json={"role": "viewer"}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 409


async def test_role_change_requires_csrf(admin_client):
    c = admin_client
    await _login(c, "REDACTED", "pw12345")
    uid = [u for u in (await c.get("/api/v1/users")).json()][0]["id"]
    r = await c.patch(f"/api/v1/users/{uid}", json={"role": "editor"})
    assert r.status_code == 403


async def test_non_admin_forbidden(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="REDACTED", pw_hash=hash_password("pw12345"), role="editor"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "REDACTED", "password": "pw12345"})
        assert (await c.get("/api/v1/users")).status_code == 403
```

Create `tests/api/test_ui_language.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="REDACTED", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "REDACTED", "password": "pw12345"})
        yield c, c.cookies.get("paw_csrf")


async def test_default_lang_is_en(client):
    c, _ = client
    r = await c.get("/")
    assert r.status_code == 200
    assert '<html lang="en">' in r.text


async def test_switch_to_ru_then_back(client):
    c, csrf = client
    r = await c.post(
        "/api/v1/users/me/ui-language", json={"ui_language": "ru"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 204
    assert r.headers.get("hx-refresh") == "true"
    page = await c.get("/")
    assert '<html lang="ru">' in page.text
    # a RU-only string proves the catalog is wired, not just the lang attr
    assert "Домены" in page.text

    r = await c.post(
        "/api/v1/users/me/ui-language", json={"ui_language": "en"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 204
    assert '<html lang="en">' in (await c.get("/")).text


async def test_invalid_lang_rejected(client):
    c, csrf = client
    r = await c.post(
        "/api/v1/users/me/ui-language", json={"ui_language": "de"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 422


async def test_switch_requires_csrf(client):
    c, _ = client
    r = await c.post("/api/v1/users/me/ui-language", json={"ui_language": "ru"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run (need Docker): `uv run pytest tests/api/test_users_admin.py tests/api/test_ui_language.py -q`
Expected: FAIL — `PATCH`/`DELETE /users/{id}` and `POST /users/me/ui-language` are 404/405 (routes not defined yet). *(No Docker locally → these run on CI; the route signatures below are the contract under test.)*

- [ ] **Step 3: Implement the endpoints**

In `src/paw/api/routers/users.py`, add imports and routes. **Order matters:** declare `POST /me/ui-language` and the `{user_id}` routes carefully — `me` is a literal path segment under a different method (`POST`) so it does not collide with `PATCH/DELETE /{user_id}`; still, keep `/me/ui-language` defined before any `GET /{user_id}` if one is ever added.
```python
import uuid

from fastapi import Response
from paw.api.deps import current_user
from paw.api.web.i18n import SUPPORTED_LANGS  # only for the request schema's doc; validation lives in the service

class RoleUpdate(BaseModel):
    role: str

class UiLanguageUpdate(BaseModel):
    ui_language: str


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    dependencies=[Depends(require_csrf), Depends(require_role("admin"))],
)
async def update_user_role(
    user_id: uuid.UUID, body: RoleUpdate, session: AsyncSession = Depends(db)
) -> UserOut:
    u = await UserService(session).set_role(user_id=user_id, role=body.role)
    return UserOut(id=str(u.id), email=u.email, role=u.role)


@router.delete(
    "/{user_id}",
    status_code=204,
    dependencies=[Depends(require_csrf), Depends(require_role("admin"))],
)
async def delete_user(user_id: uuid.UUID, session: AsyncSession = Depends(db)) -> Response:
    await UserService(session).delete(user_id=user_id)
    return Response(status_code=204)


@router.post("/me/ui-language", status_code=204, dependencies=[Depends(require_csrf)])
async def set_my_ui_language(
    body: UiLanguageUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> Response:
    await UserService(session).set_ui_language(user_id=user.id, lang=body.ui_language)
    return Response(status_code=204, headers={"HX-Refresh": "true"})
```
> Drop the unused `SUPPORTED_LANGS` import if mypy/ruff flags it — validation is in the service (see Task 3 Step 2 note). Keep the import out unless used.

- [ ] **Step 4: Run the tests — expect PASS**

Run (need Docker): `uv run pytest tests/api/test_users_admin.py tests/api/test_ui_language.py -q`
Expected: PASS — list/create/role-change/delete roundtrip; last-admin guard returns 409 on both demote and delete; csrf + RBAC guards hold; `ui-language` flips `<html lang>` and surfaces a RU string, default is "en", invalid lang 422, missing csrf 403. **This is the executable proof of acceptance criterion #7 (language switch + users manageable).**

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff check src/paw/api/routers/users.py tests/api/test_users_admin.py tests/api/test_ui_language.py
uv run mypy src/paw/api/routers/users.py
git add src/paw/api/routers/users.py tests/api/test_users_admin.py tests/api/test_ui_language.py
git commit -m "feat(users): PATCH role, DELETE user, POST me/ui-language endpoints"
```

---

### Task 5: Admin UI — api-keys + users sections in settings

**Files:**
- Modify: `src/paw/api/web/templates/settings.html`
- Test: `tests/api/test_admin_ui_pages.py`

**Interfaces:**
- Consumes the `page_ctx` extras wired in Task 2 Step 2 (`users`, `api_keys`) and the new endpoints from Task 4. Renders forms hitting `/api/v1/users`, `/api/v1/users/{id}`, `/api/v1/api-keys`.

- [ ] **Step 1: Write the failing UI page test**

Create `tests/api/test_admin_ui_pages.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _client_for(db_session, email, role):
    await UserRepo(db_session).create(
        email=email, pw_hash=hash_password("pw12345"), role=role
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    await c.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    return c


async def test_admin_sees_users_and_apikeys_sections(db_session, wired_settings):
    c = await _client_for(db_session, "REDACTED", "admin")
    try:
        page = await c.get("/settings")
        assert page.status_code == 200
        # api-keys management present and wired to the real endpoint
        assert 'hx-post="/api/v1/api-keys"' in page.text
        assert 'hx-post="/api/v1/users"' in page.text
        # current admin's own row listed
        assert "REDACTED" in page.text
    finally:
        await c.aclose()


async def test_editor_does_not_see_user_management(db_session, wired_settings):
    c = await _client_for(db_session, "REDACTED", "editor")
    try:
        page = await c.get("/settings")
        assert page.status_code == 200
        # users management form is admin-only
        assert 'hx-post="/api/v1/users"' not in page.text
        # but api-keys are self-service for everyone
        assert 'hx-post="/api/v1/api-keys"' in page.text
    finally:
        await c.aclose()
```

- [ ] **Step 2: Run to verify failure**

Run (need Docker): `uv run pytest tests/api/test_admin_ui_pages.py -q`
Expected: FAIL — the `#users` placeholder is empty and there is no api-keys section yet.

- [ ] **Step 3: Build the settings sections**

Edit `src/paw/api/web/templates/settings.html`. Convert the connection headers to `t(...)`, replace the empty `#users` section, and add an api-keys section. The api-keys section is visible to everyone; the users-management form is admin-only (`{% if user and user.role == 'admin' %}`).

```html
{% extends "base.html" %}
{% block title %}{{ t('settings.title') }} · {{ t('app.name') }}{% endblock %}
{% block sidebar %}
<nav>
  <a href="#connection">{{ t('settings.connection') }}</a><br>
  <a href="#apikeys">{{ t('apikeys.title') }}</a><br>
  {% if user and user.role == 'admin' %}<a href="#users">{{ t('users.title') }}</a>{% endif %}
</nav>
{% endblock %}
{% block content %}
<h1>{{ t('settings.title') }}</h1>

<section id="connection">
  <h2>{{ t('settings.connection') }}</h2>
  <form hx-post="/api/v1/settings/provider" hx-ext="json-enc"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}'>
    <label>{{ t('settings.base_url') }} <input name="base_url" required></label>
    <label>{{ t('settings.api_key') }} <input name="api_key" type="password"></label>
    <label>{{ t('settings.chat_model') }} <input name="chat_model" required></label>
    <label>{{ t('settings.embedding_model') }} <input name="embedding_model" required></label>
    <label>{{ t('settings.vision_model') }} <input name="vision_model"></label>
    <label>{{ t('settings.embedding_dim') }} <input name="embedding_dim" type="number" required></label>
    <p class="warning">Changing the embedding dimension requires an ALTER + HNSW rebuild + reindex.</p>
    <button type="submit">{{ t('settings.save_connection') }}</button>
  </form>
</section>

<section id="apikeys">
  <h2>{{ t('apikeys.title') }}</h2>
  <form hx-post="/api/v1/api-keys" hx-ext="json-enc"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}'
        hx-target="#apikey-issued" hx-swap="innerHTML">
    <label>{{ t('apikeys.scopes') }}
      <select name="scopes" multiple>
        <option value="read" selected>read</option>
      </select>
    </label>
    <button type="submit">{{ t('apikeys.issue') }}</button>
  </form>
  <div id="apikey-issued"></div>
  <table>
    <thead><tr>
      <th>{{ t('apikeys.prefix') }}</th><th>{{ t('apikeys.scopes') }}</th>
      <th>{{ t('apikeys.created') }}</th><th>{{ t('apikeys.last_used') }}</th>
      <th>{{ t('apikeys.status') }}</th><th></th>
    </tr></thead>
    <tbody>
      {% for k in api_keys %}
      <tr>
        <td>{{ k.prefix }}</td>
        <td>{{ k.scopes | join(', ') }}</td>
        <td>{{ k.created_at }}</td>
        <td>{{ k.last_used or '—' }}</td>
        <td>{% if k.revoked_at %}{{ t('apikeys.revoked') }}{% else %}{{ t('apikeys.active') }}{% endif %}</td>
        <td>
          {% if not k.revoked_at %}
          <button hx-delete="/api/v1/api-keys/{{ k.id }}"
                  hx-headers='{"x-csrf-token": "{{ csrf }}"}'
                  hx-target="closest tr" hx-swap="outerHTML">{{ t('apikeys.revoke') }}</button>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>

{% if user and user.role == 'admin' %}
<section id="users">
  <h2>{{ t('users.title') }}</h2>
  <form hx-post="/api/v1/users" hx-ext="json-enc"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}'>
    <label>{{ t('users.email') }} <input name="email" type="email" required></label>
    <label>{{ t('users.new_password') }} <input name="password" type="password" required></label>
    <label>{{ t('users.role') }}
      <select name="role">
        <option value="viewer">{{ t('users.role_viewer') }}</option>
        <option value="editor">{{ t('users.role_editor') }}</option>
        <option value="admin">{{ t('users.role_admin') }}</option>
      </select>
    </label>
    <button type="submit">{{ t('users.create') }}</button>
  </form>
  <table>
    <thead><tr>
      <th>{{ t('users.email') }}</th><th>{{ t('users.role') }}</th>
      <th>{{ t('users.created') }}</th><th></th>
    </tr></thead>
    <tbody>
      {% for u in users %}
      <tr>
        <td>{{ u.email }}</td>
        <td>
          <select name="role"
                  hx-patch="/api/v1/users/{{ u.id }}" hx-ext="json-enc"
                  hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-swap="none">
            <option value="viewer" {% if u.role == 'viewer' %}selected{% endif %}>{{ t('users.role_viewer') }}</option>
            <option value="editor" {% if u.role == 'editor' %}selected{% endif %}>{{ t('users.role_editor') }}</option>
            <option value="admin" {% if u.role == 'admin' %}selected{% endif %}>{{ t('users.role_admin') }}</option>
          </select>
        </td>
        <td>{{ u.created_at }}</td>
        <td>
          <button hx-delete="/api/v1/users/{{ u.id }}"
                  hx-headers='{"x-csrf-token": "{{ csrf }}"}'
                  hx-target="closest tr" hx-swap="outerHTML">{{ t('users.remove') }}</button>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}
{% endblock %}
```
Notes:
- The freshly issued token is rendered into `#apikey-issued` from the POST response. The `/api/v1/api-keys` endpoint returns JSON, not HTML — HTMX would swap raw JSON. To show the token cleanly once, add a tiny web partial route returning HTML, **OR** keep it simple: the JSON body's `key` field is still visibly swapped (acceptable for a one-shot reveal). If a clean reveal is wanted, add a thin web POST `/api-keys/issue` in `routes.py` that calls `ApiKeyService.issue` and renders a `_apikey_issued.html` partial with `t('apikeys.token_once')` + the token. **Recommended:** add that thin web route + partial (keeps CSP/JSON concerns out of the template) and point the form's `hx-post` at it. Decide at implementation time; the table + revoke wiring above is unchanged either way.
- The per-row role `<select>` uses `onchange`-free submission via HTMX `hx-patch` triggered on `change` (HTMX default trigger for `select` is `change`), so no inline JS is needed.
- `u.created_at` / `k.created_at` render the raw datetime/ISO string — fine for an admin panel; do not add locale date formatting (YAGNI).

- [ ] **Step 4 (conditional): thin web route for one-shot token reveal**

If you took the recommended path in Step 3, add to `routes.py`:
```python
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
```
Create `src/paw/api/web/templates/_apikey_issued.html`:
```html
<p class="apikey-reveal">{{ t('apikeys.token_once') }}</p>
<code>{{ token }}</code>
```
And change the issue form's `hx-post` to `/api-keys/issue` (web route, returns HTML) with `hx-ext` removed (it posts a normal form; no JSON body needed since scope is fixed to `read`). Keep the scope select as a static `read`-only display.

- [ ] **Step 5: Run the page test — expect PASS**

Run (need Docker): `uv run pytest tests/api/test_admin_ui_pages.py -q`
Expected: PASS — admin sees both `hx-post="/api/v1/api-keys"` and `hx-post="/api/v1/users"` (or `/api-keys/issue` if you took Step 4 — then update the test's api-keys assertion to match the chosen action) and their own email row; an editor sees the api-keys section but NOT the users-management form.

> If you took the Step 4 web-route path, update `test_admin_ui_pages.py`'s api-keys assertion from `hx-post="/api/v1/api-keys"` to `hx-post="/api-keys/issue"` so the test matches the rendered action. Keep the two assertions consistent with the template.

- [ ] **Step 6: Full regression of web suites + lint/type + commit**

```bash
uv run pytest tests/api/test_web_pages.py tests/api/test_web_shell.py tests/api/test_admin_ui_pages.py -q
uv run ruff check .
uv run mypy src
git add src/paw/api/web/templates/settings.html tests/api/test_admin_ui_pages.py
# include routes.py + _apikey_issued.html if Step 4 taken
git commit -m "feat(web): admin UI for api-keys + users management with i18n"
```

---

### Task 6: Convert remaining required templates + full verification

**Files:**
- Modify: `src/paw/api/web/templates/login.html`, `dashboard.html`
- (settings.html + base.html already converted in Tasks 2/5)

**Interfaces:** none new — string substitution only.

- [ ] **Step 1: Convert `login.html`**

```html
{% extends "base.html" %}
{% block title %}{{ t('login.title') }} · {{ t('app.name') }}{% endblock %}
{% block content %}
<h1>{{ t('app.name') }}</h1>
<form hx-post="/api/v1/auth/login" hx-ext="json-enc">
  <label>{{ t('login.email') }} <input name="email" type="email" required></label>
  <label>{{ t('login.password') }} <input name="password" type="password" required></label>
  <button type="submit">{{ t('login.submit') }}</button>
</form>
{% endblock %}
```
> `test_login_page_renders_frame` asserts `"Personal AI Wiki" in r.text`. With login defaulting to `en` and `t('app.name')=="Personal AI Wiki"`, the `<h1>` still contains it. The `<title>` block default in `base.html` is also still "Personal AI Wiki". Assertion holds.

- [ ] **Step 2: Convert `dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}{{ t('dashboard.title') }} · {{ t('app.name') }}{% endblock %}
{% block sidebar %}<h3>{{ t('nav.domains') }}</h3>{% endblock %}
{% block content %}
<h1>{{ t('dashboard.title') }}</h1>
<form hx-post="/api/v1/domains" hx-ext="json-enc" hx-headers='{"x-csrf-token": "{{ csrf }}"}'>
  <label>{{ t('dashboard.new_domain') }} <input name="name" required></label>
  <button type="submit">{{ t('dashboard.create') }}</button>
</form>
<ul>
  {% for d in domains %}<li><a href="/domains/{{ d.id }}">{{ d.name }}</a></li>{% endfor %}
</ul>
{% endblock %}
```
> `test_setup_then_dashboard` asserts `"Domains" in r.text or "domains" in r.text.lower()`. EN default → "Domains" present. Holds.

- [ ] **Step 2b: Confirm `domain.html` renders under `page_ctx`**

`domain.html` was given `user`/`t`/`ui_lang` via Task 2's `page_ctx` refactor but its body strings are emoji+action labels not in this plan's required-conversion set. Leave its literals as-is (they render fine). No edit needed beyond what Task 2 already wired. Confirm `test_domain_page_has_ingest_action` still passes in Step 3.

- [ ] **Step 3: Full suite green (need Docker)**

Run the whole suite:
```bash
uv run pytest -q
```
Expected: PASS. Specifically the previously-pinned assertions still hold:
- `tests/api/test_web_shell.py::test_login_page_renders_frame` — "Personal AI Wiki" present.
- `tests/api/test_web_pages.py::test_setup_then_dashboard` — "Domains" present.
- `tests/api/test_web_pages.py::test_settings_shows_dim_change_warning` — warning string untouched, present.
- `tests/api/test_web_pages.py::test_domain_page_has_ingest_action` — ingest form present.
- New: `test_ui_language`, `test_users_admin`, `test_admin_ui_pages`, `test_i18n_catalog` all green.

*(If Docker is unavailable locally, run `uv run pytest tests/unit -q` here to confirm the i18n unit test, and defer the Docker layers to CI — note it in the PR description.)*

- [ ] **Step 4: Lint + type-check the whole tree**

```bash
uv run ruff check .
uv run mypy src
```
Expected: clean (CI gate).

- [ ] **Step 5: Manual acceptance walkthrough (criterion #7)**

If a live stack is available (`docker compose up`):
1. Log in as admin → `/settings`. Verify the **API keys** section lists keys, **Issue key** reveals a token once, **Revoke** removes a row.
2. Verify the **Users** section: create a user, change their role via the per-row dropdown, remove them. Confirm you cannot delete/demote the last admin (409 surfaced).
3. Use the header **Language** switcher: pick Russian → page reloads with Russian labels and `<html lang="ru">`; content/article bodies and any reasoning-language settings are unchanged. Switch back to English.
4. Log in as an `editor` → `/settings` shows API keys but no Users-management form.

- [ ] **Step 6: Commit**

```bash
git add src/paw/api/web/templates/login.html src/paw/api/web/templates/dashboard.html
git commit -m "i18n(web): convert login + dashboard templates to t()"
```

---

### Task 7: Update docs (iwiki)

**Files:**
- Modify: `docs/wiki/*` (regenerated)

- [ ] **Step 1: Regenerate wiki pages for the changed sources**

For each changed source area, run the iwiki ingest skill (never guess engine subcommands):
```
iwiki:iwiki-ingest src/paw/api/web/i18n.py
iwiki:iwiki-ingest src/paw/api/web/routes.py
iwiki:iwiki-ingest src/paw/api/routers/users.py
iwiki:iwiki-ingest src/paw/services/users.py
iwiki:iwiki-ingest src/paw/db/repos/users.py
```
Document: the new i18n catalog/mechanism, the `ui_language` storage contract (`users.chat_prefs["ui_language"]` → `app_settings["ui_language"]` → "en"), the new users-management + ui-language endpoints, and the admin UI sections.

- [ ] **Step 2: Lint the wiki**

Run the lint skill: `/iwiki-lint`
Expected: no broken `[[refs]]`, no orphan/stale pages.

- [ ] **Step 3: Commit docs**

```bash
git add docs/wiki
git commit -m "docs(wiki): document admin UI + UI i18n (phase 9c)"
```

---

## Verification Summary (acceptance criterion #7)

- **UI language switch toggles RU/EN independently of content/reasoning languages** — `tests/api/test_ui_language.py` proves the switch flips `<html lang>` and surfaces a RU catalog string; the language lives in `users.chat_prefs["ui_language"]` and never touches content/reasoning config (which live elsewhere in `app_settings`/`domains.config`). `tests/unit/test_i18n_catalog.py` proves catalog symmetry + fallback.
- **api-keys manageable in the admin UI** — `tests/api/test_admin_ui_pages.py` proves the section renders + wires to the existing `/api-keys` endpoints; the Phase 8 `test_api_keys.py` already proves issue/list/revoke.
- **users manageable in the admin UI** — `tests/api/test_users_admin.py` proves list/create/role-change/delete + last-admin guard + RBAC; `test_admin_ui_pages.py` proves the admin-only UI section renders and is hidden from non-admins.
- **CI gate** — `ruff check .` → `mypy src` → `pytest -q` all green (Task 6 Steps 3–4).

## Risks / notes

- **`SettingsService.update()` replaces the whole blob.** This plan never calls it for `ui_language`; the per-user pref is written via `UserRepo.set_chat_prefs` (merge-in-service) and the global default is only *read*. If a future task adds a UI to set the global `app_settings["ui_language"]`, it MUST use the merge pattern (`s = get(); s["ui_language"]=v; upsert(s)`) like `ProviderSettingsService`, not `SettingsService.update`, or it will clobber provider config.
- **`user` now in template contexts.** Only the four converted page routes pass it; unconverted routes/templates render their literals unchanged (an unconverted `{{ t(...) }}`-free template is unaffected). No template references `t`/`user` unless this plan added it, so no `UndefinedError` risk on unconverted pages.
- **CSP + inline handlers.** The language `<select onchange="...">` relies on HTML event-handler attributes, which the current CSP (`script-src 'self'`, no `script-src-attr`) permits. If 9b tightens CSP to forbid attribute handlers, move the trigger into `static/app.js`. Do not modify CSP in 9c.
- **Docker for API tests.** `test_ui_language`, `test_users_admin`, `test_admin_ui_pages` need the Postgres/Redis testcontainers; they are CI-verified where Docker exists. The i18n catalog test runs locally without Docker and is the local fast-feedback signal.
- **Hard delete (no soft-delete).** Spec defers soft-delete; this plan hard-deletes users (FK `ON DELETE CASCADE`/`SET NULL` on dependent rows already defined in models), guarding only the last-admin case. Deleting a user with chat sessions cascades per the existing `chat_sessions` FK (`ondelete="CASCADE"`).
