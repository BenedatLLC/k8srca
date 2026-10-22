"""The answer as posted (010 §5.2): markers to notes, and the host's checks."""

from pathlib import Path
from types import SimpleNamespace

from k8srca.facts import Fact, FactLog
from k8srca.facts.render import MAX_WORDS, render, render_workspace, uncited_numbers
from k8srca.review import Review, answer_problems, check

FACTS = [
    Fact("default/Deployment/ad/ad", "last_termination", "OOMKilled",
         "ad: last 3 terminations OOMKilled, exit 137", id="F3",
         source={"tool": "k8s_get_workload_report", "n": 4}),
    Fact("default/Deployment/ad/ad", "memory_limit", "300Mi",
         "ad: memory limit 300Mi, equal to its request", id="F7",
         source={"tool": "k8s_get_pod_spec", "n": 6}),
    Fact("default/Workload/ad", "completes_startup_in_retained_lives", False,
         "the log stops before startup completes", id="F9", by="agent", source={"n": 5}),
]

ANSWER = """**Most likely:** ad's memory limit is too low for its JVM (very likely).

**Why:** it is killed for memory every time[F3], at a 300Mi limit[F7], before it
finishes starting[F9]. Killed again[F3].

**Next steps:** raise the limit to 512Mi."""


def test_markers_number_in_order_of_first_use():
    r = render(ANSWER, FACTS)
    assert "every time[1], at a 300Mi limit[2], before it\nfinishes starting[3]" in r.prose
    assert "Killed again[1]" in r.prose
    assert [f.id for f in r.cited] == ["F3", "F7", "F9"]
    assert "F3" not in r.text and "F7" not in r.text


def test_the_evidence_block_names_each_source():
    notes = render(ANSWER, FACTS).text.split("*Evidence*\n")[1].splitlines()
    assert notes == [
        "[1] ad: last 3 terminations OOMKilled, exit 137 (k8s_get_workload_report)",
        "[2] ad: memory limit 300Mi, equal to its request (k8s_get_pod_spec)",
        "[3] the log stops before startup completes (the agent's reading of result 5)",
    ]


def test_several_markers_in_one_bracket():
    assert render("both[F3, F7] and again[F7][F3]", FACTS).prose == "both[1][2] and again[2][1]"


def test_an_unknown_marker_is_reported_and_dropped():
    r = render("it is OOM[F3][F40]", FACTS)
    assert r.unresolved == ["F40"] and r.prose == "it is OOM[1]"


def test_word_count_is_the_prose_only():
    r = render(ANSWER, FACTS)
    assert r.words == len(ANSWER.replace("[F3]", "").replace("[F7]", "").replace("[F9]", "").split())


def test_numbers_must_be_in_a_cited_fact():
    assert uncited_numbers(ANSWER, FACTS) == []  # 512Mi is advice, under next steps
    assert uncited_numbers("killed 7216 times[F3] at 300Mi[F7]", FACTS) == ["7216"]
    # A number only in a fact the answer does not cite is still uncited.
    assert uncited_numbers("limit 300Mi[F3]", FACTS) == ["300Mi"]


def _workspace(tmp_path: Path) -> Path:
    log = FactLog(tmp_path)
    for f in FACTS:
        log.add([Fact(**{**f.as_dict(), "id": ""})], f.source)
    return tmp_path


def test_render_workspace_reads_the_sessions_log(tmp_path):
    ws = _workspace(tmp_path)
    ids = {f.statement: f.id for f in FactLog(ws).facts}
    answer = f"killed for memory[{ids[FACTS[0].statement]}]"
    assert render_workspace(answer, ws).startswith("killed for memory[1]\n\n*Evidence*")


def test_no_log_leaves_the_markers(tmp_path):
    assert render_workspace("killed[F3]", tmp_path / "elsewhere") == "killed[F3]"


def test_answer_problems(tmp_path):
    ws = _workspace(tmp_path)
    assert answer_problems("killed[F1] at 300Mi[F2]", ws) == []
    problems = answer_problems("killed 99 times[F1][F12] " + "word " * MAX_WORDS, ws)
    assert any("F12 name no fact" in p for p in problems)
    assert any(f"limit is {MAX_WORDS}" in p for p in problems)
    assert any("99" in p for p in problems)
    assert any("cites no facts" in p for p in answer_problems("no markers here", ws))


def test_the_answer_checks_reach_the_revision_prompt(tmp_path):
    ws = _workspace(tmp_path)
    cfg = SimpleNamespace(completeness=False, reviewer=False, answer=True)
    review = check("no markers here", ws, "s", cfg)
    assert review.opens is None and review.prompt and "cites no facts" in review.prompt
    assert check("killed[F1]", ws, "s", cfg).prompt is None
    assert Review(opens=None).prompt is None
