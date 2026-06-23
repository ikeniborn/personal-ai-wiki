import uuid
from datetime import UTC, datetime, timedelta

from paw.providers.config import ChatConfig
from paw.services.retention import resolve_retention, select_sessions_to_prune


def test_resolve_uses_global_when_prefs_empty():
    r = resolve_retention(ChatConfig(), {})
    assert r.history_depth == 10 and r.max_sessions == 50 and r.max_age_days == 90


def test_resolve_applies_overrides():
    prefs = {"history_depth": 3, "retention": {"max_sessions": 5, "max_age_days": 7}}
    r = resolve_retention(ChatConfig(), prefs)
    assert r.history_depth == 3 and r.max_sessions == 5 and r.max_age_days == 7


def test_resolve_null_key_falls_back_to_global():
    # null/absent keys -> global default (spec: "null key -> global default")
    prefs = {"history_depth": None, "retention": {"max_sessions": None, "max_age_days": 2}}
    r = resolve_retention(ChatConfig(), prefs)
    assert r.history_depth == 10 and r.max_sessions == 50 and r.max_age_days == 2


def test_prune_selects_overflow_and_aged():
    now = datetime(2026, 6, 23, tzinfo=UTC)
    ids = [uuid.uuid4() for _ in range(4)]
    sessions = [
        (ids[0], now),                          # newest, kept
        (ids[1], now - timedelta(days=1)),      # kept by count, fresh
        (ids[2], now - timedelta(days=2)),      # overflow when max_sessions=2
        (ids[3], now - timedelta(days=100)),    # aged out
    ]
    doomed = select_sessions_to_prune(sessions, max_sessions=2, max_age_days=30, now=now)
    assert set(doomed) == {ids[2], ids[3]}


def test_prune_keeps_all_within_limits():
    now = datetime(2026, 6, 23, tzinfo=UTC)
    sessions = [(uuid.uuid4(), now), (uuid.uuid4(), now - timedelta(days=1))]
    assert select_sessions_to_prune(sessions, max_sessions=50, max_age_days=90, now=now) == []
