from paw.harness.ops.format import check_format_invariant


def test_invariant_holds_when_facts_preserved():
    assert check_format_invariant(
        ["QUIC", "UDP"], ["runs over UDP"],
        new_markdown="## Overview\n\nQUIC runs over UDP. Reformatted prose.",
    )


def test_invariant_fails_when_entity_dropped():
    assert not check_format_invariant(
        ["QUIC", "UDP"], [], new_markdown="## Overview\n\nQUIC only, no transport named.",
    )


def test_invariant_fails_when_citation_dropped():
    assert not check_format_invariant(
        ["QUIC"], ["runs over UDP"], new_markdown="## Overview\n\nQUIC is a protocol.",
    )
