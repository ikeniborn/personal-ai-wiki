from datetime import UTC, datetime, timedelta

from paw.harness.ops.lint import (
    find_broken_refs,
    find_duplicate_entities,
    find_orphans,
    find_stale,
    issue_id,
)
from paw.security.sanitize import extract_wikilink_targets


def test_extract_wikilink_targets_drops_labels():
    md = "See [[tcp]] and [[quic|QUIC protocol]] but not [broken](x)."
    assert extract_wikilink_targets(md) == ["tcp", "quic"]


def test_find_broken_refs_flags_unknown_targets():
    bodies = [("intro", "links to [[tcp]] and [[ghost]]")]
    assert find_broken_refs(bodies, {"intro", "tcp"}) == [("intro", "ghost")]


def test_find_orphans_returns_unlinked_nodes():
    assert find_orphans(["a", "b", "c"], [("a", "b")]) == ["c"]


def test_find_stale_uses_cutoff():
    now = datetime(2026, 6, 23, tzinfo=UTC)
    fresh = now - timedelta(days=10)
    old = now - timedelta(days=400)
    assert find_stale([("a", fresh), ("b", old)], now=now, stale_days=180) == ["b"]


def test_find_duplicate_entities_groups_case_insensitively():
    groups = find_duplicate_entities(["QUIC", "quic", "TCP", " Quic "])
    assert groups == [["QUIC", "quic", " Quic "]]


def test_issue_id_is_stable_and_short():
    a = issue_id("broken_ref", "intro", "ghost")
    b = issue_id("broken_ref", "intro", "ghost")
    assert a == b and len(a) == 16
    assert issue_id("broken_ref", "intro", "other") != a
