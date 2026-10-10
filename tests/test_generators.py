"""The generator contract (design 005 §6.2), and both generators behind it."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from k8srca.config import ArchitectureConfig, ArchSource
from k8srca.core.generator import (MANIFEST, GeneratorReport, content_digest, finalize,
                                   read_manifest, run)

KB_SOURCE = Path("background/kubernetes_rca_knowledge_base_v2.json")


class Fake:
    """The smallest generator: writes what it is told to."""

    name = "fake-skill"
    format = 3

    def __init__(self, files: dict[str, str], warnings=()):
        self.files, self.warnings = files, list(warnings)

    async def generate(self, cfg, dest: Path) -> GeneratorReport:
        dest.mkdir(parents=True, exist_ok=True)
        for rel, text in self.files.items():
            (dest / rel).write_text(text)
        return GeneratorReport(generator=self.name, lines=["built"], warnings=self.warnings,
                               counts={"files": len(self.files)})


def generate(gen, tmp_path, cfg=None):
    return asyncio.run(run(gen, cfg, tmp_path / gen.name, reports=tmp_path / "reports"))


class TestManifest:
    def test_every_bundle_gets_one(self, tmp_path):
        bundle, _ = generate(Fake({"SKILL.md": "x"}), tmp_path)
        m = read_manifest(bundle.path)
        assert m == {"name": "fake-skill", "generator": "Fake", "format": 3,
                     "k8srca": m["k8srca"], "content_digest": bundle.content_digest}

    def test_the_digest_excludes_the_manifest_that_records_it(self, tmp_path):
        bundle, _ = generate(Fake({"SKILL.md": "x"}), tmp_path)
        assert content_digest(bundle.path) == bundle.content_digest

    def test_it_holds_no_timestamp(self, tmp_path):
        """Sync re-uploads on any digest change; a timestamp would make every
        build of an unchanged skill look new, and churn committed bundles."""
        a, _ = generate(Fake({"SKILL.md": "x"}), tmp_path)
        first = (a.path / MANIFEST).read_text()
        b, _ = generate(Fake({"SKILL.md": "x"}), tmp_path)
        assert (b.path / MANIFEST).read_text() == first

    def test_a_content_change_moves_the_digest(self, tmp_path):
        a, _ = generate(Fake({"SKILL.md": "x"}), tmp_path)
        b, _ = generate(Fake({"SKILL.md": "y"}), tmp_path)
        assert a.content_digest != b.content_digest

    def test_finalize_on_a_hand_built_bundle(self, tmp_path):
        (tmp_path / "SKILL.md").write_text("x")
        assert finalize(tmp_path, Fake({})).content_digest == content_digest(tmp_path)


class TestReport:
    def test_saved_outside_the_bundle(self, tmp_path):
        """It is for people and evals, not the agent, so it is not shipped."""
        bundle, _ = generate(Fake({"SKILL.md": "x"}), tmp_path)
        assert not any(p.name.endswith("report.json") for p in bundle.path.rglob("*"))
        saved = json.loads((tmp_path / "reports" / "fake-skill.json").read_text())
        assert saved["counts"] == {"files": 1}
        assert saved["bundle"]["content_digest"] == bundle.content_digest

    def test_warnings_are_kept_and_rendered(self, tmp_path):
        _, report = generate(Fake({"SKILL.md": "x"}, warnings=["skipped a.yaml"]), tmp_path)
        assert "WARNING  skipped a.yaml" in report.render()


DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: ad, namespace: default}
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: ad
          image: example/ad:1.0
          resources:
            requests: {memory: 300Mi}
            limits: {memory: 300Mi}
"""


class TestArchitectureGenerator:
    def _cfg(self, tmp_path):
        charts = tmp_path / "charts"
        charts.mkdir()
        (charts / "ad.yaml").write_text(DEPLOYMENT)

        class Cfg:
            mcp = []
            architecture = ArchitectureConfig(sources=[
                ArchSource(type="chart_repo", path=charts)])
        return Cfg()

    def test_builds_a_manifested_bundle_without_a_cluster(self, tmp_path):
        from k8srca.arch.generator import ArchitectureGenerator
        from kubewiki.render import FORMAT

        bundle, report = generate(ArchitectureGenerator(), tmp_path, self._cfg(tmp_path))
        data = json.loads((bundle.path / "architecture.json").read_text())
        assert "ad" in data["services"]
        assert data["version"] == FORMAT == read_manifest(bundle.path)["format"]
        assert report.counts["services"] == 1
        assert (bundle.path / "SKILL.md").exists() and (bundle.path / "arch_query.py").exists()

    def test_no_sources_is_an_error_not_an_empty_skill(self, tmp_path):
        from k8srca.arch.generator import ArchitectureGenerator

        class Cfg:
            mcp = []
            architecture = ArchitectureConfig(sources=[])
        with pytest.raises(ValueError, match="no architecture sources"):
            generate(ArchitectureGenerator(), tmp_path, Cfg())


@pytest.mark.skipif(not KB_SOURCE.exists(), reason="KB source is in gitignored background/")
class TestKnowledgeBaseGenerator:
    def test_writes_into_a_hand_written_bundle(self, tmp_path):
        from k8srca.kb.build import FORMAT
        from k8srca.kb.generator import KnowledgeBaseGenerator

        dest = tmp_path / "k8s-rca"
        dest.mkdir()
        (dest / "SKILL.md").write_text("hand-written")
        bundle, report = asyncio.run(run(KnowledgeBaseGenerator(), None, dest,
                                         reports=tmp_path / "reports"))
        data = json.loads((dest / "knowledge_base.json").read_text())
        assert data["version"] == FORMAT == read_manifest(dest)["format"]
        assert (dest / "SKILL.md").read_text() == "hand-written"
        assert report.counts["alerts"] > 0

    def test_the_committed_bundle_is_reproducible(self, tmp_path):
        """Regenerating skills/k8s-rca from its sources must not change it."""
        from k8srca.kb.generator import KnowledgeBaseGenerator

        committed = Path("skills/k8s-rca")
        before = content_digest(committed)
        copy = tmp_path / "k8s-rca"
        copy.mkdir()
        for f in ("SKILL.md", "kb_query.py"):
            (copy / f).write_bytes((committed / f).read_bytes())
        bundle, _ = asyncio.run(run(KnowledgeBaseGenerator(), None, copy,
                                    reports=tmp_path / "reports"))
        assert bundle.content_digest == before
