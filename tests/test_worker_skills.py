"""Skill delivery is checked while it is visible (001 §5, CLAUDE.md).

Skills are the only channel by which knowledge reaches a self-hosted sandbox, and
their absence is invisible after the fact: the platform downloads them at the
start of a turn and the SDK deletes each directory when its toolset context
exits, so a successful download and a failed one both leave an empty `skills/`
dir. An RCA answered without the knowledge base reads exactly like one answered
with it.
"""

import asyncio

import pytest

from k8srca.worker.runner import (SkillsNotDelivered, expected_skills,
                                  report_skills, watch_skills)


class TestExpectedSkills:
    def test_a_comma_list_is_parsed(self, monkeypatch):
        monkeypatch.setenv("K8SRCA_SKILLS", "k8s-rca,cluster-architecture")
        assert expected_skills() == ("k8s-rca", "cluster-architecture")

    def test_whitespace_and_blanks_are_ignored(self, monkeypatch):
        monkeypatch.setenv("K8SRCA_SKILLS", " k8s-rca , ,cluster-architecture ")
        assert expected_skills() == ("k8s-rca", "cluster-architecture")

    def test_unset_is_empty(self, monkeypatch):
        monkeypatch.delenv("K8SRCA_SKILLS", raising=False)
        assert expected_skills() == ()


class TestReport:
    def test_total_non_delivery_raises(self):
        with pytest.raises(SkillsNotDelivered, match="general knowledge"):
            report_skills(("k8s-rca", "cluster-architecture"), set())

    def test_partial_delivery_does_not_raise(self, caplog):
        """Which skill is missing decides what the answer is worth; failing the
        item would discard one that may be sound."""
        with caplog.at_level("ERROR"):
            report_skills(("k8s-rca", "cluster-architecture"), {"k8s-rca"})
        assert "skills_partial" in caplog.text
        assert "cluster-architecture" in caplog.text

    def test_full_delivery_is_quiet(self, caplog):
        with caplog.at_level("ERROR"):
            report_skills(("k8s-rca",), {"k8s-rca"})
        assert "skills_partial" not in caplog.text

    def test_an_agent_with_no_skills_is_not_a_failure(self):
        """k8s-investigator carries none; absence is correct there."""
        report_skills((), set())


class TestWatcher:
    def _watch(self, workdir, expected, seen, timeout=2.0):
        async def go():
            task = asyncio.create_task(watch_skills(str(workdir), expected, seen,
                                                    interval=0.02))
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        asyncio.run(go())

    def test_a_populated_skill_dir_is_seen(self, tmp_path):
        d = tmp_path / "skills" / "k8s-rca"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")
        seen = set()
        self._watch(tmp_path, ("k8s-rca",), seen)
        assert seen == {"k8s-rca"}

    def test_an_empty_skill_dir_does_not_count(self, tmp_path):
        """The SDK creates the directory before filling it; an empty one is not
        a delivered skill."""
        (tmp_path / "skills" / "k8s-rca").mkdir(parents=True)
        seen = set()
        self._watch(tmp_path, ("k8s-rca",), seen, timeout=0.3)
        assert seen == set()

    def test_a_missing_skills_root_is_tolerated(self, tmp_path):
        """It does not exist until the first download; polling must not crash."""
        seen = set()
        self._watch(tmp_path, ("k8s-rca",), seen, timeout=0.3)
        assert seen == set()

    def test_a_skill_appearing_late_is_still_seen(self, tmp_path):
        async def go():
            seen = set()
            task = asyncio.create_task(watch_skills(str(tmp_path), ("late",), seen,
                                                    interval=0.02))
            await asyncio.sleep(0.1)
            d = tmp_path / "skills" / "late"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("x")
            await asyncio.wait_for(task, timeout=2.0)
            return seen
        assert asyncio.run(go()) == {"late"}

    def test_the_watcher_returns_once_everything_arrived(self, tmp_path):
        """It must not spin for the whole turn after its job is done."""
        for name in ("a", "b"):
            d = tmp_path / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("x")
        seen = set()
        self._watch(tmp_path, ("a", "b"), seen, timeout=1.0)
        assert seen == {"a", "b"}
