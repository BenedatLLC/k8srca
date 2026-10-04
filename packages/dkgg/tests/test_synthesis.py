"""Synthesis, review and typed blast (docs/design.md §5, migration step 3).

No model is called: a fake provider answers from the inputs it is given.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dkgg import synthesis as syn
from dkgg import wiki
from dkgg.pipeline import CostLimit, generate
from dkgg.providers import AnthropicProvider, OpenAIProvider, Usage, price, provider_for
from dkgg.review import Review
from dkgg.verify import check

from test_wiki import sample

KINDS = {"cart": "service", "checkout": "service", "valkey-cart": "cache"}
EDGE_KINDS = {("cart", "valkey-cart"): ("cache", True), ("checkout", "cart"): ("sync-call", False)}


def answer(inp: dict) -> dict:
    """A valid synthesis for these inputs, citing only what was given."""
    comps = []
    for c in inp["components"]:
        cite = [f"docs:{c['docs'][0]['origin']}"] if c["docs"] else ["derived:edges"]
        comps.append({
            "name": c["name"], "kind": KINDS[c["name"]], "kind_cites": cite,
            "purpose": [{"text": f"{c['name']} does its job.", "cites": cite}],
            "if_it_fails": [{"text": "Its callers lose it.", "cites": ["derived:callers"]}]
            if c["callers"] else [],
            "edges": [{"to": e["to"], "kind": EDGE_KINDS[(c["name"], e["to"])][0],
                       "soft": EDGE_KINDS[(c["name"], e["to"])][1],
                       "cites": [f"env:{v}" for v in e["vars"]]} for e in c["edges"]],
        })
    return {"overview": [{"text": "A small shop.", "cites": ["derived:edges"]}],
            "components": comps}


class FakeProvider:
    model = "claude-opus-5"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, system, prompt, schema, name):
        self.prompts.append(prompt)
        data = self.answers.pop(0)
        return copy.deepcopy(data), Usage(1000, 500, price(self.model, 1000, 500))


@pytest.fixture
def inp() -> dict:
    return syn.inputs(wiki.graph(sample()))


class TestInputs:
    def test_edges_carry_variable_names_only(self, inp):
        cart = next(c for c in inp["components"] if c["name"] == "cart")
        assert cart["edges"] == [{"to": "valkey-cart", "vars": ["VALKEY_ADDR"], "config": []}]
        assert "ready_replicas" not in json.dumps(inp)     # no live state either

    def test_callers_are_given(self, inp):
        cart = next(c for c in inp["components"] if c["name"] == "cart")
        assert cart["callers"] == ["checkout"]

    def test_digest_changes_with_model_and_inputs(self, inp):
        assert syn.digest(inp, "a") != syn.digest(inp, "b")
        other = copy.deepcopy(inp)
        other["components"][0]["callers"].append("x")
        assert syn.digest(inp, "a") != syn.digest(other, "a")


class TestValidate:
    def test_a_faithful_answer_passes(self, inp):
        assert syn.validate(answer(inp), inp) == []

    def test_an_invented_component_or_edge_is_rejected(self, inp):
        bad = answer(inp)
        bad["components"].append({**bad["components"][0], "name": "payment"})
        bad["components"][0]["edges"].append(
            {"to": "postgresql", "kind": "datastore", "soft": False, "cites": ["derived:edges"]})
        errors = syn.validate(bad, inp)
        assert "component payment was not given" in errors
        assert any("edge to postgresql was not given" in e for e in errors)

    def test_a_dropped_component_or_edge_is_rejected(self, inp):
        bad = answer(inp)
        bad["components"] = [c for c in bad["components"] if c["name"] != "valkey-cart"]
        next(c for c in bad["components"] if c["name"] == "cart")["edges"] = []
        errors = syn.validate(bad, inp)
        assert "component valkey-cart is missing" in errors
        assert "cart: edge to valkey-cart is not classified" in errors

    def test_citations_must_name_given_inputs(self, inp):
        bad = answer(inp)
        bad["components"][0]["purpose"][0]["cites"] = ["docs:made-up.md"]
        bad["overview"][0]["cites"] = []
        errors = syn.validate(bad, inp)
        assert any("'docs:made-up.md', which was not given" in e for e in errors)
        assert "overview: no citation" in errors

    def test_one_components_env_cannot_be_cited_on_another(self, inp):
        bad = answer(inp)
        checkout = next(c for c in bad["components"] if c["name"] == "checkout")
        checkout["kind_cites"] = ["env:VALKEY_ADDR"]
        assert any(e.startswith("checkout.kind: cites 'env:VALKEY_ADDR'")
                   for e in syn.validate(bad, inp))


class TestSynthesize:
    def test_one_call_when_valid(self, inp):
        p = FakeProvider(answer(inp))
        s = syn.synthesize(p, inp)
        assert s.errors == [] and len(p.prompts) == 1

    def test_one_repair_round_with_the_errors(self, inp):
        bad = answer(inp)
        bad["components"].pop()
        p = FakeProvider(bad, answer(inp))
        s = syn.synthesize(p, inp)
        assert s.errors == [] and len(p.prompts) == 2
        assert "is missing" in p.prompts[1]
        assert s.usage.input_tokens == 2000 and s.usage.usd == pytest.approx(2 * price(
            "claude-opus-5", 1000, 500))

    def test_still_invalid_after_repair_is_reported_not_hidden(self, inp):
        bad = answer(inp)
        bad["components"].pop()
        s = syn.synthesize(FakeProvider(bad, bad), inp)
        assert s.errors


class TestReview:
    REVIEW = {
        "edges": {"add": [{"from": "checkout", "to": "valkey-cart", "kind": "cache",
                           "soft": True, "why": "docs"}],
                  "remove": [{"from": "cart", "to": "valkey-cart"}],
                  "kind": [{"from": "checkout", "to": "cart", "kind": "async-event"}]},
        "components": {"valkey-cart": {"kind": "datastore"}},
        "claims": [{"page": "cart", "reject": "does its job"}],
    }

    def test_unknown_keys_are_refused(self):
        with pytest.raises(Exception):
            Review.model_validate({"edge": {}})

    def test_adjust_adds_and_removes_edges(self):
        g = Review.model_validate(self.REVIEW).adjust(wiki.graph(sample()))
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        assert ("cart", "valkey-cart") not in pairs and ("checkout", "valkey-cart") in pairs

    def test_review_kinds_win_over_the_models(self, inp):
        r = Review.model_validate(self.REVIEW)
        data = syn.apply_review(answer(inp), r)
        vc = next(c for c in data["components"] if c["name"] == "valkey-cart")
        assert vc["kind"] == "datastore" and vc["kind_cites"] == ["review"]
        checkout = next(c for c in data["components"] if c["name"] == "checkout")
        assert checkout["edges"][0]["kind"] == "async-event"

    def test_check_finds_a_repeated_rejected_claim(self, tmp_path):
        r = Review.model_validate({"claims": [{"page": "cart", "reject": "does its job"}]})
        g = wiki.graph(sample())
        wiki.write(sample(), tmp_path, g=g, synthesis=answer(syn.inputs(g)))
        rows = [f for f in check(tmp_path, review=r) if f.row == "review"]
        assert rows and "does its job" in rows[0].detail


class TestPipeline:
    def test_synthesis_is_cached_by_digest(self, tmp_path, inp):
        p = FakeProvider(answer(inp))
        first = generate(sample(), tmp_path / "w", model="claude-opus-5", cache=tmp_path,
                         provider=p)
        again = generate(sample(), tmp_path / "w", model="claude-opus-5", cache=tmp_path,
                         provider=FakeProvider())
        assert not first.cached and again.cached
        assert again.usage.input_tokens == 0
        assert check(tmp_path / "w") == []

    def test_cost_limit_refuses_before_calling(self, tmp_path):
        p = FakeProvider()
        with pytest.raises(CostLimit):
            generate(sample(), tmp_path / "w", model="claude-opus-5", cache=tmp_path,
                     max_usd=0.0, provider=p)
        assert p.prompts == []

    def test_written_pages_carry_kinds_prose_and_typed_edges(self, tmp_path, inp):
        generate(sample(), tmp_path, model="claude-opus-5", cache=tmp_path / "c",
                 provider=FakeProvider(answer(inp)))
        g = json.loads((tmp_path / "graph.json").read_text())
        assert g["components"]["valkey-cart"]["kind"] == "cache"
        edge = next(e for e in g["edges"] if e["from"] == "cart")
        assert (edge["kind"], edge["soft"]) == ("cache", True)
        page = (tmp_path / "components" / "cart.md").read_text()
        assert "## Purpose" in page and "-.->|cache|" in page

    def test_guidance_in_written_prose_is_a_finding(self, tmp_path, inp):
        a = answer(inp)
        a["components"][0]["purpose"][0]["text"] = "First, check the logs."
        generate(sample(), tmp_path, model="claude-opus-5", cache=tmp_path / "c",
                 provider=FakeProvider(a))
        assert any(f.row == "guidance" for f in check(tmp_path))


class TestBlast:
    def blast(self, root: Path, name: str) -> str:
        return subprocess.run([sys.executable, str(root / "wiki.py"), "blast", name],
                              capture_output=True, text=True, check=True).stdout

    def test_a_soft_edge_degrades_and_a_sync_call_fails(self, tmp_path, inp):
        generate(sample(), tmp_path, model="claude-opus-5", cache=tmp_path / "c",
                 provider=FakeProvider(answer(inp)))
        out = self.blast(tmp_path, "valkey-cart")
        assert "keep running, affected" in out and "cart  (runs without the cache)" in out
        assert "fail with it" not in out
        assert "checkout  (directly)" in self.blast(tmp_path, "cart")

    def test_a_cache_the_caller_cannot_bypass_takes_it_down(self, tmp_path, inp):
        a = answer(inp)
        next(c for c in a["components"] if c["name"] == "cart")["edges"][0]["soft"] = False
        generate(sample(), tmp_path, model="claude-opus-5", cache=tmp_path / "c",
                 provider=FakeProvider(a))
        out = self.blast(tmp_path, "valkey-cart")
        assert "cart  (directly)" in out and "checkout  (2 hops away)" in out


class TestTypedScore:
    def score(self, g, **truth):
        from dkgg.eval import Truth, score_wiki
        return score_wiki("c", g, {}, Truth(**truth))

    def test_unsynthesised_wiki_is_not_scored_on_kinds(self):
        s = self.score(wiki.graph(sample()), kinds={"cart": "service"})
        assert s.rates()["kind_accuracy"] is None and not s.synthesised

    def test_kinds_and_edge_kinds_against_truth(self, tmp_path, inp):
        g = generate(sample(), tmp_path, model="claude-opus-5", cache=tmp_path / "c",
                     provider=FakeProvider(answer(inp))).graph
        s = self.score(g, kinds={"cart": "service", "valkey-cart": "datastore", "gone": "job"},
                       edge_kinds={"cart -> valkey-cart": "cache soft",
                                   "checkout -> cart": "async-event",
                                   "a -> b": "load"})
        assert s.kinds_expected == 2 and s.kinds_wrong == [
            "valkey-cart: wiki cache, truth datastore"]
        assert s.edge_kinds_expected == 2 and s.edge_kinds_wrong == [
            "checkout -> cart: wiki sync-call, truth async-event"]
        assert s.rates()["edge_kind_accuracy"] == 0.5


class TestProviders:
    def test_model_prefix_picks_the_provider(self):
        assert isinstance(provider_for("claude-opus-5"), AnthropicProvider)
        p = provider_for("openai:gpt-x")
        assert isinstance(p, OpenAIProvider) and p.model == "gpt-x"

    def test_unknown_model_has_unknown_cost(self):
        assert price("nobody-1", 10, 10) is None

    def test_anthropic_request_and_parse(self):
        calls = {}

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="thinking"),
                             SimpleNamespace(type="text", text='{"ok": true}')],
                    usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0))

        def stream(**kw):
            calls.update(kw)
            return Stream()

        client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
        data, usage = AnthropicProvider("claude-opus-5", client=client).complete(
            "sys", "hi", {"type": "object"}, "n")
        assert data == {"ok": True} and usage.usd == 5.0
        assert calls["output_config"]["format"]["type"] == "json_schema"

    def test_openai_request_and_parse(self):
        calls = {}

        def create(**kw):
            calls.update(kw)
            return SimpleNamespace(output_text='{"ok": 1}',
                                   usage=SimpleNamespace(input_tokens=3, output_tokens=4))

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        data, usage = OpenAIProvider("gpt-x", client=client).complete(
            "sys", "hi", {"type": "object"}, "n")
        assert data == {"ok": 1} and usage.usd is None
        assert calls["text"]["format"]["strict"] is True

    def test_the_judge_reads_generated_prose(self, tmp_path, inp):
        from dkgg.eval import wiki_pages
        g = generate(sample(), tmp_path, model="claude-opus-5", cache=tmp_path / "c",
                     provider=FakeProvider(answer(inp))).graph
        pages = {p["page"]: p["text"] for p in wiki_pages(g)}
        assert "cart does its job." in pages["components/cart.md (generated)"]
        assert pages["index.md (generated)"] == "A small shop."


def test_a_kind_named_like_its_component_gets_a_distinct_subgraph_id():
    """Mermaid refuses a subgraph whose id is one of its own nodes."""
    g = {"components": {"load-generator": {"kind": "load-generator", "workload": "Deployment"}},
         "edges": []}
    d = wiki.system_diagram(g)
    assert 'subgraph group_load_generator["load-generator"]' in d
    assert '    load_generator["load-generator"]' in d
