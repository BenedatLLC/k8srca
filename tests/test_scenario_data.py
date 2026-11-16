"""Scenario captures and skills fetched by digest from the data repository
(issue #7). Hermetic: the "repository" is a directory, as a local checkout of
it is to the runner."""

import json
import shutil

import pytest

from k8srca.kb.skills import bundle_digest
from k8srca.scenario import sources
from k8srca.scenario.model import MissingData, Scenario, ScenarioDir, pin_capture_digest

from test_scenario_model import CAPTURE, write_scenario


@pytest.fixture
def data_repo(tmp_path, monkeypatch):
    repo = tmp_path / "data"
    repo.mkdir()
    monkeypatch.setenv(sources.DATA_DIR_ENV, str(repo))
    # Nothing is served from here: a fetch that missed the checkout would fail.
    monkeypatch.setenv(sources.DATA_URL_ENV, "http://127.0.0.1:9/none")
    monkeypatch.setenv(sources.FETCH_ENV, "1")
    return repo


def skill(d):
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# skill\n")
    (d / "graph.json").write_text('{"nodes": []}')
    return d


class TestPacking:
    def test_the_same_capture_packs_to_the_same_bytes(self, tmp_path):
        a, b = tmp_path / "a.json.gz", tmp_path / "elsewhere" / "b.json.gz"
        assert sources.pack_capture(CAPTURE, a) == sources.pack_capture(CAPTURE, b)
        assert a.read_bytes() == b.read_bytes()

    def test_the_same_skill_packs_to_the_same_bytes(self, tmp_path):
        s = skill(tmp_path / "s" / "cluster-architecture")
        a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
        assert sources.pack_skill(s, a) == bundle_digest(s)
        (s / "graph.json").touch()  # a new mtime, the same content
        sources.pack_skill(s, b)
        assert a.read_bytes() == b.read_bytes()


class TestFetching:
    def test_a_capture_round_trips(self, data_repo, tmp_path):
        digest = sources.pack_capture(CAPTURE, data_repo / sources.data_path("x", "capture"))
        (data_repo / sources.data_path("x", "capture")).rename(
            data_repo / sources.data_path(digest, "capture"))
        got = sources.fetch_capture(digest, tmp_path / "k8s.json")
        assert got.read_bytes() == CAPTURE.read_bytes()

    def test_a_capture_with_the_wrong_digest_is_refused(self, data_repo, tmp_path):
        sources.pack_capture(CAPTURE, data_repo / sources.data_path("0123456789abcdef", "capture"))
        with pytest.raises(sources.SourceError, match="has digest"):
            sources.fetch_capture("0123456789abcdef", tmp_path / "k8s.json")
        assert not (tmp_path / "k8s.json").exists()

    def test_a_skill_round_trips(self, data_repo, tmp_path):
        s = skill(tmp_path / "built" / "cluster-architecture")
        digest = sources.pack_skill(s, data_repo / sources.data_path(bundle_digest(s), "skill"))
        dest = tmp_path / "scenario" / "skill" / "wiki" / "cluster-architecture"
        sources.fetch_skill(digest, dest)
        assert bundle_digest(dest) == digest
        assert [p.name for p in dest.parent.iterdir()] == ["cluster-architecture"]


class TestAScenario:
    def pinned(self, tmp_path, data_repo):
        """A scenario whose capture and wiki are published and not here."""
        d = write_scenario(tmp_path / "s")
        digest = sources.metrics_digest(d / "k8s.json")
        sources.pack_capture(d / "k8s.json", data_repo / sources.data_path(digest, "capture"))
        pin_capture_digest(d / "scenario.yaml", digest)
        wiki = skill(d / "skill" / "wiki" / "cluster-architecture")
        sources.pack_skill(wiki, data_repo / sources.data_path(bundle_digest(wiki), "skill"))
        truth = (d / "truth.yaml").read_text()
        (d / "truth.yaml").write_text(truth.replace(
            "capture:\n", f"capture:\n  skill_digests:\n    wiki: {bundle_digest(wiki)}\n", 1))
        (d / "k8s.json").unlink()
        shutil.rmtree(d / "skill")
        return d

    def test_reading_it_fetches_what_it_pins(self, tmp_path, data_repo):
        d = self.pinned(tmp_path, data_repo)
        sd = ScenarioDir(d, "wiki")
        assert sd.capture() == json.loads(CAPTURE.read_text())
        assert (d / "skill" / "wiki" / "cluster-architecture" / "SKILL.md").exists()
        sd.check_truth_is_current()

    def test_with_fetching_off_it_says_how_to_fetch(self, tmp_path, data_repo, monkeypatch):
        d = self.pinned(tmp_path, data_repo)
        monkeypatch.setenv(sources.FETCH_ENV, "0")
        with pytest.raises(MissingData, match="k8srca scenario fetch"):
            ScenarioDir(d, "wiki").capture()

    def test_a_capture_already_here_is_never_overwritten(self, tmp_path, data_repo):
        d = self.pinned(tmp_path, data_repo)
        (d / "k8s.json").write_text('{"captured_at": "re-recorded"}')
        assert ScenarioDir(d, "wiki").capture() == {"captured_at": "re-recorded"}

    def test_an_unpinned_capture_is_read_where_it_is(self, tmp_path, data_repo):
        d = write_scenario(tmp_path / "s")
        assert ScenarioDir(d, "legacy").capture() == json.loads(CAPTURE.read_text())


class TestFetchPinned:
    """`k8srca scenario fetch` (issue #48): a file already here is compared
    with what the scenario pins, and replaced only when asked."""

    def published(self, tmp_path, data_repo):
        """A scenario whose capture, wiki and metrics block are published,
        pinned and here."""
        d = write_scenario(tmp_path / "s")
        captured_at = json.loads(CAPTURE.read_text())["captured_at"]
        digest = sources.metrics_digest(d / "k8s.json")
        sources.pack_capture(d / "k8s.json", data_repo / sources.data_path(digest, "capture"))
        pin_capture_digest(d / "scenario.yaml", digest)
        scenario = (d / "scenario.yaml").read_text()
        (d / "scenario.yaml").write_text(scenario.replace(
            "    clock: frozen\n", "    clock: frozen\n  prometheus: {}\n", 1))
        wiki = skill(d / "skill" / "wiki" / "cluster-architecture")
        sources.pack_skill(wiki, data_repo / sources.data_path(bundle_digest(wiki), "skill"))
        block = d / "metrics" / "block.tar.gz"
        block.parent.mkdir()
        block.write_bytes(b"the pinned block")
        block_digest = sources.metrics_digest(block)
        published = data_repo / sources.data_path(block_digest, "metrics")
        published.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(block, published)
        truth = (d / "truth.yaml").read_text()
        (d / "truth.yaml").write_text(truth.replace(
            "capture:\n",
            f"capture:\n  skill_digests:\n    wiki: {bundle_digest(wiki)}\n"
            f"  metrics:\n    end: '{captured_at}'\n    digest: {block_digest}\n", 1))
        return d

    def make_stale(self, d):
        """What a machine holds that fetched before the scenario was re-recorded."""
        (d / "k8s.json").write_text('{"captured_at": "an older recording"}')
        (d / "skill" / "wiki" / "cluster-architecture" / "graph.json").write_text('{"old": 1}')
        (d / "metrics" / "block.tar.gz").write_bytes(b"an older block")

    def pins(self, d):
        sd = ScenarioDir(d, "wiki")
        return (sd.scenario.sources.k8stools.digest, sd.truth.capture.pinned("wiki"),
                sd.truth.capture.metrics.digest)

    def local(self, d):
        return (sources.metrics_digest(d / "k8s.json"),
                bundle_digest(d / "skill" / "wiki" / "cluster-architecture"),
                sources.metrics_digest(d / "metrics" / "block.tar.gz"))

    def test_files_that_match_their_pins_are_ok(self, tmp_path, data_repo):
        d = self.published(tmp_path, data_repo)
        got = sources.fetch_pinned(ScenarioDir(d, "wiki"))
        assert [(name, outcome) for name, outcome, _ in got] == [
            ("capture", "ok"), ("wiki skill", "ok"), ("metrics", "ok")]

    def test_missing_files_are_fetched(self, tmp_path, data_repo):
        d = self.published(tmp_path, data_repo)
        (d / "k8s.json").unlink()
        shutil.rmtree(d / "skill")
        (d / "metrics" / "block.tar.gz").unlink()
        got = sources.fetch_pinned(ScenarioDir(d, "wiki"))
        assert {outcome for _, outcome, _ in got} == {"fetched"}
        assert self.local(d) == self.pins(d)

    def test_a_file_that_differs_from_its_pin_is_reported_and_kept(self, tmp_path, data_repo):
        d = self.published(tmp_path, data_repo)
        self.make_stale(d)
        before = self.local(d)
        got = sources.fetch_pinned(ScenarioDir(d, "wiki"))
        assert [(name, outcome) for name, outcome, _ in got] == [
            ("capture", "differs"), ("wiki skill", "differs"), ("metrics", "differs")]
        assert tuple(have for _, _, have in got) == before
        # Kept: a recording made here and not published yet must not be lost.
        assert self.local(d) == before != self.pins(d)

    def test_replace_fetches_the_pinned_file_over_it(self, tmp_path, data_repo):
        d = self.published(tmp_path, data_repo)
        self.make_stale(d)
        got = sources.fetch_pinned(ScenarioDir(d, "wiki"), replace=True)
        assert {outcome for _, outcome, _ in got} == {"replaced"}
        assert self.local(d) == self.pins(d)
        assert [p.name for p in (d / "skill" / "wiki").iterdir()] == ["cluster-architecture"]
        ScenarioDir(d, "wiki").check_truth_is_current()

    def test_a_skill_is_kept_when_its_replacement_cannot_be_fetched(self, tmp_path, data_repo):
        d = self.published(tmp_path, data_repo)
        self.make_stale(d)
        stale_skill = self.local(d)[1]
        shutil.rmtree(data_repo / "skills")
        with pytest.raises(sources.SourceError):
            sources.fetch_pinned(ScenarioDir(d, "wiki"), replace=True)
        wiki = d / "skill" / "wiki"
        assert bundle_digest(wiki / "cluster-architecture") == stale_skill
        assert [p.name for p in wiki.iterdir()] == ["cluster-architecture"]

    def test_an_unpinned_capture_is_left_alone(self, tmp_path, data_repo):
        d = write_scenario(tmp_path / "s")
        (d / "k8s.json").write_text('{"captured_at": "a draft"}')
        assert sources.fetch_pinned(ScenarioDir(d, "legacy"), replace=True) == []
        assert (d / "k8s.json").read_text() == '{"captured_at": "a draft"}'

    def test_the_command_fails_on_a_stale_file_and_says_how_to_repair_it(self, tmp_path,
                                                                         data_repo):
        from typer.testing import CliRunner

        from k8srca.cli import app

        d = self.published(tmp_path, data_repo)
        self.make_stale(d)
        root = str(d.parent)
        r = CliRunner().invoke(app, ["scenario", "fetch", "--root", root])
        assert r.exit_code == 1, r.output
        assert "STALE demo" in r.output and "--replace" in r.output
        assert self.local(d) != self.pins(d)
        r = CliRunner().invoke(app, ["scenario", "fetch", "--root", root, "--replace"])
        assert r.exit_code == 0, r.output
        assert "replaced capture" in r.output
        assert self.local(d) == self.pins(d)
        r = CliRunner().invoke(app, ["scenario", "fetch", "--root", root])
        assert r.exit_code == 0 and r.output.strip() == "demo: ok", r.output


def test_pinning_keeps_the_file_and_its_comments(tmp_path):
    p = tmp_path / "scenario.yaml"
    p.write_text("id: a\nquestion: q\nsources:\n  # the capture\n  k8stools:\n"
                 "    state: k8s.json\n    clock: frozen\n  prometheus: {}\nbudget: {}\n")
    pin_capture_digest(p, "aaaa")
    pin_capture_digest(p, "bbbb")
    assert p.read_text() == ("id: a\nquestion: q\nsources:\n  # the capture\n  k8stools:\n"
                             "    state: k8s.json\n    digest: bbbb\n    clock: frozen\n"
                             "  prometheus: {}\nbudget: {}\n")
    import yaml

    assert Scenario.model_validate(yaml.safe_load(p.read_text())).sources.k8stools.digest == "bbbb"
