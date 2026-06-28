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
