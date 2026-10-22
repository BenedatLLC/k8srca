"""The answer-writer eval (design 010 §8): a scenario's facts in, the answer out.

Each case is one model call on fixed input, not a replay of an investigation.
The input is what an investigation of the scenario would have found: the facts
the facts layer derives from its recorded results (`tests/scenarios/<id>/
results/`), the readings an agent records from logs (the case's
`agent_facts`), and the knowledge base's output on them. The answer is
rendered as a reader would see it, put through the host's answer checks, and
graded on the scenario's checklist.

What it does not show: whether an agent gathers these facts, or reads the raw
results well. That is the live run's job. This settles the answer's shape
cheaply, before one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..facts import FactLog, block, derive
from ..facts.render import render
from ..review import answer_problems
from ..scenario import checklist as cl
from ..scenario.model import ScenarioDir

CASES = Path("tests/evals/answer")
SKILL = Path("skills/k8s-rca")


@dataclass
class Case:
    name: str
    scenario: str
    #: kb_query.py commands whose output the agent would have read last.
    kb: list[list[str]] = field(default_factory=list)
    #: Readings an agent records from text (010 §3.4): workload, kb, says.
    agent_facts: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Case":
        d = yaml.safe_load(path.read_text())
        return cls(name=path.stem, scenario=d["scenario"], kb=d.get("kb") or [],
                   agent_facts=d.get("agent_facts") or [])


def discover(root: Path = CASES) -> list[Case]:
    return [Case.load(p) for p in sorted(root.glob("*.yaml"))]


def _kb(*args: str, log: Path) -> str:
    r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), *args,
                        "--facts-log", str(log)] if args[0] in ("open", "fact", "facts")
                       else [sys.executable, str(SKILL / "kb_query.py"), *args],
                       capture_output=True, text=True)
    return r.stdout + r.stderr


def investigation(case: Case, scenarios: Path, workdir: Path) -> tuple[str, FactLog]:
    """The case's findings as the agent read them, and the facts log behind them."""
    log = FactLog(workdir)
    parts = []
    for p in sorted((scenarios / case.scenario / "results").glob("*.json")):
        e = json.loads(p.read_text())
        result = e.get("structured") or json.loads(e["text"][0])
        facts = log.add(derive(e["tool"].split("_", 1)[1], e["arguments"], result),
                        {"tool": e["tool"], "n": e["n"]})
        parts.append(f"result {e['n']}: {e['tool']}({json.dumps(e['arguments'])})\n"
                     + (block(facts) or "(no facts)"))
    for a in case.agent_facts:
        out = _kb("fact", a["workload"], a["kb"], "--says", a["says"], log=log.path)
        parts.append(f"$ kb_query.py fact {a['workload']} '{a['kb']}' --says ...\n{out}")
    for args in case.kb:
        parts.append(f"$ kb_query.py {' '.join(args)}\n{_kb(*args, log=log.path)}")
    return "\n\n".join(parts), FactLog(workdir)


def prompt(question: str, findings: str) -> tuple[str, str]:
    system = (SKILL / "SKILL.md").read_text().split("---", 2)[-1].strip()
    user = (f"{question.strip()}\n\n"
            f"The investigation is finished. Everything it read, as you read it:\n\n"
            f"{findings}\n\n"
            "Write the answer now, as the skill's answer section describes.")
    return system, user


def _grade(sd: ScenarioDir, client: Any, text: str) -> dict:
    checklist = sd.checklist()
    if checklist is None:
        return {}
    g = cl.grade(checklist, sd.scenario.question, text, client=client)
    return {"checklist": cl.score(checklist, g), "verdicts": g.model_dump()["items"]}


def run_case(case: Case, client: Any, model: str, workdir: Path, *,
             scenarios: Path = Path("tests/scenarios"), grade: bool = True,
             review_model: str | None = None) -> dict:
    """With `review_model`, the light reviewer reads the answer (010 §6), and
    when it flags anything the writer revises once, as the host would ask it
    to. Both versions are kept and graded."""
    sd = ScenarioDir(scenarios / case.scenario)
    findings, log = investigation(case, scenarios, workdir)
    system, user = prompt(sd.scenario.question, findings)
    messages = [{"role": "user", "content": user}]
    msg = client.messages.create(model=model, max_tokens=8000, system=system,
                                 thinking={"type": "adaptive"}, messages=messages)
    answer = "".join(b.text for b in msg.content if b.type == "text").strip()
    rendered = render(answer, log.facts)
    out = {"case": case.name, "scenario": case.scenario, "answer": answer,
           "rendered": rendered.text, "words": rendered.words,
           "problems": answer_problems(answer, workdir),
           "usage": {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}}
    if grade:
        out.update(_grade(sd, client, rendered.text))
    if review_model:
        from ..review import Review, light_review

        leader = next((ln.split(":", 1)[1].strip() for ln in findings.splitlines()
                       if ln.startswith("most likely:")), "")
        flags = light_review(client, answer, rendered.cited, review_model, leader)
        out["light"] = flags
        prompt_text = Review(opens=None, light=flags).prompt if flags else None
        if prompt_text:
            messages += [{"role": "assistant", "content": answer},
                         {"role": "user", "content": prompt_text}]
            msg = client.messages.create(model=model, max_tokens=8000, system=system,
                                         thinking={"type": "adaptive"}, messages=messages)
            revised = "".join(b.text for b in msg.content if b.type == "text").strip()
            r2 = render(revised, log.facts)
            out["revised"] = {"answer": revised, "rendered": r2.text, "words": r2.words,
                              **(_grade(sd, client, r2.text) if grade else {})}
    return out
