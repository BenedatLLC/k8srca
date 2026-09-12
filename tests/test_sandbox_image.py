"""Sandbox image identity.

A fixed tag does not pin: containers are per work item, so a rebuild swaps code
underneath a running session even though the poller recorded "the image" at
session start. The tag therefore has to change when the image content changes.
"""

import subprocess
from pathlib import Path

import pytest

from k8srca import sandbox as sbx
from k8srca.config import SandboxConfig


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo shaped like the image build context."""
    def run(*args):
        return subprocess.run(args, cwd=tmp_path, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\n")
    (tmp_path / "README.md").write_text("t\n")
    (tmp_path / "k8srca.yaml").write_text("mcp: []\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("docs\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return tmp_path


class TestTagIdentity:
    def test_clean_tree_tags_with_the_commit(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        tag = sbx.image_tag(repo)
        assert "-dirty" not in tag and len(tag) == 12

    def test_editing_an_image_input_changes_the_tag(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        before = sbx.image_tag(repo)
        (repo / "src" / "app.py").write_text("x = 2\n")
        after = sbx.image_tag(repo)
        assert after != before and "-dirty" in after

    def test_editing_docs_does_not_change_the_tag(self, repo, monkeypatch):
        # Documentation is not in the image; invalidating its tag would force a
        # rebuild on every docs commit.
        monkeypatch.chdir(repo)
        before = sbx.image_tag(repo)
        (repo / "docs" / "guide.md").write_text("different\n")
        assert sbx.image_tag(repo) == before

    def test_two_different_dirty_states_get_different_tags(self, repo, monkeypatch):
        # "-dirty" alone would let two trees share a tag, which is the original
        # bug with extra steps.
        monkeypatch.chdir(repo)
        (repo / "src" / "app.py").write_text("x = 2\n")
        first = sbx.image_tag(repo)
        (repo / "src" / "app.py").write_text("x = 3\n")
        assert sbx.image_tag(repo) != first

    def test_the_same_dirty_state_is_stable(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        (repo / "src" / "app.py").write_text("x = 2\n")
        assert sbx.image_tag(repo) == sbx.image_tag(repo)

    def test_untracked_image_input_counts_as_dirty(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        before = sbx.image_tag(repo)
        (repo / "src" / "new.py").write_text("y = 1\n")
        assert sbx.image_tag(repo) != before

    def test_pycache_is_ignored(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        before = sbx.content_digest(repo)
        cache = repo / "src" / "__pycache__"
        cache.mkdir()
        (cache / "app.cpython-312.pyc").write_bytes(b"\x00compiled")
        assert sbx.content_digest(repo) == before

    def test_outside_a_git_checkout_falls_back_to_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("1")
        assert sbx.image_tag(tmp_path).startswith("tree.")


class TestReference:
    def test_reference_joins_repository_and_tag(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        assert sbx.image_ref("k8srca/sandbox", repo).startswith("k8srca/sandbox:")


class TestConfigValidation:
    def test_a_tagged_image_is_rejected(self):
        # Accepting it silently would leave the operator believing they had
        # pinned something.
        with pytest.raises(ValueError, match="without a tag"):
            SandboxConfig(image="k8srca/sandbox:0.1.0")

    def test_a_repository_is_accepted(self):
        assert SandboxConfig(image="k8srca/sandbox").image == "k8srca/sandbox"

    def test_a_registry_host_with_a_port_is_not_mistaken_for_a_tag(self):
        # The colon in "registry:5000" is a port, not a tag.
        assert SandboxConfig(image="registry:5000/k8srca/sandbox")


def test_image_inputs_match_the_dockerfile():
    """IMAGE_INPUTS must cover everything the Dockerfile COPYs, or the tag
    fails to change when the image content does."""
    dockerfile = Path("docker/Dockerfile.sandbox").read_text()
    copied = set()
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            parts = line.split()[1:-1]          # drop COPY and the destination
            copied.update(p for p in parts if not p.startswith("--"))
    missing = copied - set(sbx.IMAGE_INPUTS)
    assert not missing, (
        f"Dockerfile COPYs {sorted(missing)} which are not in IMAGE_INPUTS; "
        f"edits to them would not change the image tag."
    )
