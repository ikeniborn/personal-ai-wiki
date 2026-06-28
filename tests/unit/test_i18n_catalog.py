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
