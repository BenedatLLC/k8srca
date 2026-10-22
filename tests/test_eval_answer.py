"""The answer-writer eval (010 §8), without a model."""

from types import SimpleNamespace

from k8srca.evals.answer import discover, investigation, run_case


def test_every_recorded_scenario_has_a_case():
    assert {c.scenario for c in discover()} == {
        "checkout-gc-pressure", "itbench-33-invalid-node-selector",
        "jvm-oom-on-startup", "jvm-oom-with-metrics"}


def test_the_writer_reads_facts_and_the_ranking(tmp_path):
    case = next(c for c in discover() if c.name == "jvm-oom-on-startup")
    findings, log = investigation(case, __import__("pathlib").Path("tests/scenarios"), tmp_path)
    assert "result 4: k8s_get_workload_report" in findings
    assert "most likely: OOM [oom-kill], strong" in findings
    assert any(f.by == "agent" for f in log.facts)


def test_an_answer_is_rendered_and_checked(tmp_path):
    case = next(c for c in discover() if c.name == "jvm-oom-on-startup")
    text = "**Most likely:** ad's memory limit is too low (very likely)[F2]."
    msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                          usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: msg))
    out = run_case(case, client, "m", tmp_path, grade=False)
    assert out["rendered"].startswith("**Most likely:** ad's memory limit is too low "
                                      "(very likely)[1].\n\n*Evidence*\n[1] ")
    assert out["problems"] == []
