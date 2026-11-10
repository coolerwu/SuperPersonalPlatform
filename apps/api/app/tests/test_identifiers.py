import pytest

from app.services.identifiers import valid_agent_id


@pytest.mark.parametrize(
    "value",
    [
        "default",
        "agent-1",
        "A_B",
        "a" * 64,
        "com10",
        "lpt0",
        "console",
    ],
)
def test_valid_agent_id_accepts_safe_components(value):
    assert valid_agent_id(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "con",
        "CON",
        "Prn",
        "aux",
        "nul",
        "com1",
        "COM9",
        "lpt1",
        "LPT9",
        "",
        "a" * 65,
        "../escape",
        "a/b",
        "a.b",
        "名字",
        None,
        123,
    ],
)
def test_valid_agent_id_rejects_unsafe_or_reserved_values(value):
    assert valid_agent_id(value) is False
