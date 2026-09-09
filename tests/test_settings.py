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


class TestScrubEnvironment:
    """The in-process worker runs agent-authored bash in its own environment,
    so anything left in os.environ is readable -- and, with a kubeconfig,
    writable *through* -- by the agent (design 001 §8.2)."""

    def test_removes_the_cluster_credential(self, monkeypatch):
        from k8srca.settings import scrub_environment

        monkeypatch.setenv("KUBECONFIG", "/home/me/.kube/admin.yaml")
        assert "KUBECONFIG" in scrub_environment()

    def test_points_kubeconfig_at_nothing_rather_than_unsetting_it(self, monkeypatch):
        # Unsetting is not enough: kubectl falls back to ~/.kube/config, which
        # on a developer workstation is usually cluster-admin.
        import os

        from k8srca.settings import scrub_environment

        monkeypatch.setenv("KUBECONFIG", "/home/me/.kube/admin.yaml")
        scrub_environment()
        assert os.environ["KUBECONFIG"] == "/dev/null"

    def test_removes_api_and_slack_credentials(self, monkeypatch):
        from k8srca.settings import scrub_environment

        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_ENVIRONMENT_KEY",
                     "SLACK_BOT_USER_OAUTH_TOKEN", "SLACK_SOCKET_MODE_TOKEN"):
            monkeypatch.setenv(name, "secret")
        removed = set(scrub_environment())
        assert {"ANTHROPIC_API_KEY", "ANTHROPIC_ENVIRONMENT_KEY",
                "SLACK_BOT_USER_OAUTH_TOKEN", "SLACK_SOCKET_MODE_TOKEN"} <= removed

    def test_keeps_non_secrets(self, monkeypatch):
        import os

        from k8srca.settings import scrub_environment

        monkeypatch.setenv("SLACK_ALLOWED_CHANNELS", "C123")
        scrub_environment()
        assert os.environ["SLACK_ALLOWED_CHANNELS"] == "C123"

    def test_keep_list_is_honoured(self, monkeypatch):
        import os

        from k8srca.settings import scrub_environment

        monkeypatch.setenv("ANTHROPIC_ENVIRONMENT_KEY", "sk-ant-oat01-x")
        scrub_environment(keep=("ANTHROPIC_ENVIRONMENT_KEY",))
        assert os.environ["ANTHROPIC_ENVIRONMENT_KEY"] == "sk-ant-oat01-x"

    def test_is_idempotent(self, monkeypatch):
        from k8srca.settings import scrub_environment

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        scrub_environment()
        assert scrub_environment() == []
