"""k8srca.yaml: what a deployment declares."""

from pathlib import Path

import pytest
import yaml

from k8srca.config import Config
class TestCapabilities:
    """What a deployment can observe with (design 007 §4.1): declared per MCP
    server, passed to the sandbox so kb_query.py knows which checks it can
    make. Declared, never probed (005 §5.4)."""

    def _cfg(self, extra_server: str = "", agent_tools: str = "k8stools: full"):
        text = Path("k8srca.yaml").read_text()
        cfg = yaml.safe_load(text)
        if extra_server:
            cfg["mcp"].append(yaml.safe_load(extra_server))
            for a in cfg["agents"].values():
                if a.get("role") == "specialist":
                    a["mcp_tools"] = yaml.safe_load(agent_tools)
        return Config.model_validate(cfg)

    def test_the_shipped_config_has_k8stools_alone(self):
        assert self._cfg().capabilities() == ["k8stools"]

    def test_a_server_named_for_a_capability_provides_it(self):
        cfg = self._cfg("{name: prometheus, url: 'http://p:9090/mcp', groups: {all: '*'}}",
                        "{k8stools: full, prometheus: all}")
        assert cfg.capabilities() == ["k8stools", "prometheus"]

    def test_a_server_no_agent_uses_provides_nothing(self):
        """Configured but wired to no agent: the agent cannot reach it."""
        cfg = self._cfg("{name: prometheus, url: 'http://p:9090/mcp', groups: {all: '*'}}")
        assert cfg.capabilities() == ["k8stools"]

    def test_an_otherwise_named_server_must_declare(self):
        cfg = self._cfg("{name: vmetrics, url: 'http://v/mcp', groups: {all: '*'}}",
                        "{k8stools: full, vmetrics: all}")
        assert cfg.capabilities() == ["k8stools"]
        cfg = self._cfg("{name: vmetrics, url: 'http://v/mcp', groups: {all: '*'}, "
                        "provides: [prometheus]}", "{k8stools: full, vmetrics: all}")
        assert cfg.capabilities() == ["k8stools", "prometheus"]

    def test_an_unknown_capability_is_refused(self):
        with pytest.raises(ValueError, match="provides"):
            self._cfg("{name: x, url: 'http://x/mcp', provides: [promethus]}")

    def test_no_server_provides_a_human(self):
        with pytest.raises(ValueError, match="provides"):
            self._cfg("{name: x, url: 'http://x/mcp', provides: [human]}")
