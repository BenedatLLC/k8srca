import pytest

from k8srca.settings import MissingCredential, SlackSettings, load_dotenv


@pytest.fixture
def slack_env(monkeypatch):
    monkeypatch.setenv("SLACK_SOCKET_MODE_TOKEN", "xapp-1-test")
    monkeypatch.setenv("SLACK_BOT_USER_OAUTH_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_ALLOWED_CHANNELS", raising=False)


def test_missing_credential_names_where_to_find_it(monkeypatch):
    monkeypatch.delenv("SLACK_SOCKET_MODE_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_BOT_USER_OAUTH_TOKEN", "xoxb-test")
    with pytest.raises(MissingCredential, match="App-Level Tokens"):
        SlackSettings.from_env()


def test_empty_allowlist_permits_any_channel(slack_env):
    assert SlackSettings.from_env().channel_allowed("C123")


def test_allowlist_restricts(slack_env, monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_CHANNELS", "C123, C456")
    s = SlackSettings.from_env()
    assert s.channel_allowed("C123") and s.channel_allowed("C456")
    assert not s.channel_allowed("C999")


def test_repr_hides_tokens(slack_env):
    # Tokens must not leak into logs or tracebacks.
    text = repr(SlackSettings.from_env())
    assert "xoxb-test" not in text and "xapp-1-test" not in text


def test_dotenv_does_not_override_real_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_OAUTH_TOKEN", "xoxb-from-env")
    f = tmp_path / ".env"
    f.write_text("SLACK_BOT_USER_OAUTH_TOKEN=xoxb-from-file\n# comment\nFOO=bar\n")
    load_dotenv(f)
    import os
    assert os.environ["SLACK_BOT_USER_OAUTH_TOKEN"] == "xoxb-from-env"
    assert os.environ["FOO"] == "bar"
