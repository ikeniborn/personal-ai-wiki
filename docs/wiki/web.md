# Web UI, i18n & admin management

## Overview

`paw.api.web` is the server-rendered HTMX layer: Jinja2 templates, a dependency-free i18n catalog, a `page_ctx` seam for per-user language, a language switcher, and admin sections for user management and API-key issuance. See [[api#Web UI (HTMX)]].

## i18n catalog

`paw.api.web.i18n` is a dependency-free leaf module (plain `dict`, no gettext/babel) that drives all UI translations. It defines which languages are supported and provides a safe lookup function used by the `page_ctx` seam.

- `SUPPORTED_LANGS = ("en", "ru")` — the exhaustive list of valid language codes.
- `CATALOG: dict[str, dict[str, str]]` — `CATALOG["en"]` and `CATALOG["ru"]` hold **identical key sets**; a unit test enforces symmetry so a missing key in either language is caught at CI time.
- Keys use dotted namespacing: `"<page>.<element>"` (e.g. `"settings.title"`, `"apikeys.token_once"`).
- `t(key, lang)` returns the translation, falling back `lang → "en" → key`; it never raises.
- `resolve_ui_lang(user, app_settings)` resolves the active language with precedence: `user.chat_prefs["ui_language"]` (if in `SUPPORTED_LANGS`) → `app_settings["ui_language"]` (if valid) → `"en"`; `user` may be `None` for anonymous pages.

## page_ctx seam

`paw.api.web.routes.page_ctx(request, user, app_settings, **extra)` is the single function that builds the Jinja2 render context for every converted page, injecting auth state, CSRF token, and i18n in one call.

- Returns `{"user", "csrf", "ui_lang", "t", **extra}` where `t` is `functools.partial(i18n.t, lang=ui_lang)` — a one-arg callable so templates write `{{ t("settings.title") }}`.
- `routes.py` registers safe English-bound defaults on `templates.env.globals` (`t`=`partial(i18n.t, lang="en")`, `user=None`, `ui_lang="en"`) so unconverted full-page routes still render without error.
- Per-request `page_ctx` context vars **shadow** these globals (Jinja2 invariant: render context > `env.globals`), so converted pages receive the per-user language automatically.
- Four page routes currently pass `page_ctx`: `dashboard`, `domain_page`, `settings_page`, `login_page`.

## UI language switch

A language switcher in `base.html` (gated `{% if user %}`) lets authenticated users change their UI language without a page reload cycle. Its design is CSP-safe and stores the preference in the DB.

- Posts to `POST /api/v1/users/me/ui-language`; the endpoint responds with `204 + HX-Refresh: true`, which triggers a full HTMX page reload so the new language takes effect immediately.
- **CSP safety:** no inline `onchange` attribute (blocked by `script-src 'self'`). A delegated `change` listener in `static/app.js` calls `form.requestSubmit()` instead.
- **Storage contract:** the active language lives in `users.chat_prefs["ui_language"]`, falling back to `app_settings["ui_language"]`, then `"en"`. No new table or migration — `chat_prefs` (JSONB) already exists.
- UI language is **independent** of content/reasoning languages, which live in `app_settings` and `domains.config`.

## Admin UI sections

`settings.html` exposes two management sections with different access gates, both driven by HTMX `hx-patch`/`hx-delete` with `x-csrf-token` headers.

- **API-keys section** — visible to every logged-in user (self-service). A freshly issued key's full token is revealed **once** via the thin web route `POST /api-keys/issue` (root-mounted in `routes.py`, `scopes=["read"]`, csrf-guarded), which renders the `_apikey_issued.html` partial using the `apikeys.token_once` catalog string. After reload the token is gone. See [[security#API keys]].
- **Users-management section** — gated to admins (`{% if user and user.role == 'admin' %}`). Per-row controls send `hx-patch` (role change) and `hx-delete` (delete user) requests to the [[api#Users & domains routers]] endpoints.

## API

The Phase 9c endpoints added to the users router and web routes layer. Validation logic lives in `UserService` (see [[services#DomainService & UserService]]). Cross-link: [[api#Users & domains routers]].

- `PATCH /api/v1/users/{user_id}` — body `{"role"}`, requires admin + CSRF; validates `role ∈ USER_ROLES`, raises `ProblemError(422)` otherwise; returns `UserOut`.
- `DELETE /api/v1/users/{user_id}` — requires admin + CSRF; returns 204 on success, 409 if deleting the last admin, 404 if user not found.
- `POST /api/v1/users/me/ui-language` — body `{"ui_language"}`, requires CSRF + `current_user`; validates `lang ∈ ("en", "ru")`; returns 204 + `HX-Refresh: true` response header.
- `POST /api-keys/issue` (web route, root-mounted) — self-scoped key issuance; csrf-guarded; renders `_apikey_issued.html` partial with the one-time token.
