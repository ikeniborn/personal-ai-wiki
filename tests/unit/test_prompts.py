import pytest

from paw.harness.prompts import PROMPT_VERSION, get_prompt


def test_known_prompts_include_preamble_and_language():
    for name in ("extraction", "drafting", "summary", "init"):
        p = get_prompt(name, gen_language="ru")
        assert "DATA, not instructions" in p  # shared safety preamble present
        assert "ru" in p
    assert PROMPT_VERSION == "v1"


def test_unknown_prompt_raises():
    with pytest.raises(KeyError):
        get_prompt("nope")
