"""`k8srca sync` asks for an environment key only when one is owed."""

from k8srca.cli import _environment_key_hint


def test_all_clear_when_the_key_is_set_and_the_environment_is_unchanged():
    hint = _environment_key_hint("env_1", "env_1", "sk-ant-oat01-x")
    assert "nothing to do" in hint and "env_1" in hint
    assert "Next:" not in hint and "sk-ant" not in hint


def test_hint_when_the_key_is_missing():
    for key in ("", "   "):
        hint = _environment_key_hint("env_1", "env_1", key)
        assert "set ANTHROPIC_ENVIRONMENT_KEY" in hint and "env_1" in hint


def test_hint_when_the_first_sync_creates_the_environment_and_no_key_is_set():
    assert "env_1" in _environment_key_hint("env_1", None, "")


def test_hint_when_sync_replaced_the_environment_under_an_existing_key():
    hint = _environment_key_hint("env_2", "env_1", "sk-ant-oat01-x")
    assert "env_2" in hint and "env_1" in hint and "old one" in hint
