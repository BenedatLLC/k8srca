"""Pinning the architecture skill beside a scenario's capture (004 §6.3)."""

import json
from pathlib import Path
from types import SimpleNamespace

from k8srca.config import ArchitectureSettings, ArchSource, HelmChartRef
from k8srca.scenario.model import ScenarioDir
from k8srca.scenario.record import snapshot_skill, wiki_settings

from test_scenario_model import write_scenario


def built_skill(root: Path, fmt: int) -> Path:
    skill = root / "skills" / "cluster-architecture"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: cluster-architecture\n---\n")
    (skill / "GENERATED.json").write_text(json.dumps({"format": fmt}))
    return skill


class TestSnapshot:
    def test_a_wiki_goes_beside_the_legacy_snapshot_not_over_it(self, tmp_path):
        d = write_scenario(tmp_path)
        legacy = d / "skill" / "cluster-architecture"
        legacy.mkdir(parents=True)
        (legacy / "architecture.json").write_text("{}")
        pinned = snapshot_skill(d, built_skill(tmp_path, 2))
        assert pinned == ScenarioDir(d, "wiki").skill_dir
        assert (legacy / "architecture.json").exists()

    def test_a_legacy_build_goes_to_the_legacy_slot(self, tmp_path):
        d = write_scenario(tmp_path)
        assert snapshot_skill(d, built_skill(tmp_path, 1)) == ScenarioDir(d, "legacy").skill_dir


class TestWikiSettings:
    """A scenario's wiki is built against its replay, never the live cluster."""

    def cfg(self):
        return SimpleNamespace(architecture=ArchitectureSettings(model="m", sources=[
            ArchSource(type="live_cluster", server="k8stools", namespaces=["default"]),
            ArchSource(type="chart_repo", helm=HelmChartRef(
                repo="https://charts.example", chart="demo", version="1.0.0")),
        ]))

    def test_the_configured_sources_are_the_default(self, tmp_path):
        sd = ScenarioDir(write_scenario(tmp_path), "wiki")
        got = wiki_settings(sd, self.cfg(), "replay")
        assert [s.type for s in got.sources] == ["live_cluster", "chart_repo"]
        assert got.sources[0].server == "replay" and got.model == "m"

    def test_the_configuration_is_not_modified(self, tmp_path):
        cfg = self.cfg()
        wiki_settings(ScenarioDir(write_scenario(tmp_path), "wiki"), cfg, "replay")
        assert cfg.architecture.sources[0].server == "k8stools"

    def test_a_scenario_from_another_install_uses_its_own(self, tmp_path):
        # Our chart and docs describe our install, not theirs (004 §12.1).
        d = write_scenario(tmp_path)
        with (d / "scenario.yaml").open("a") as f:
            f.write("architecture:\n  namespaces: [otel-demo]\n"
                    "  sources:\n    - type: live_cluster\n    - type: change_history\n")
        got = wiki_settings(ScenarioDir(d, "wiki"), self.cfg(), "replay")
        assert [s.type for s in got.sources] == ["live_cluster", "change_history"]
        assert all(s.server == "replay" and s.namespaces == ["otel-demo"] for s in got.sources)
