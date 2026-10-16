"""Mining public guides into the knowledge format (design 007 §8), and the
layers it adds to (§9.3). No network and no model: a fake provider answers."""

import copy
import io
import json
from pathlib import Path

import pytest
import yaml

from k8srca.kb import build as build_mod
from k8srca.kb import knowledge, mine
from kubewiki.providers import Usage

from test_kb import MINIMAL, write_layer

SOURCE_TEXT = """# Debugging a crash loop

If the container's last exit code is 1 and its logs end with a stack trace, the
application itself crashed.

When a dependency is unreachable at startup the process often exits with a
`connection refused` error in its logs.
"""


class TestQuotes:
    def test_found_through_markdown_and_wrapping(self):
        assert mine.quoted("the process often exits with a connection refused error",
                           SOURCE_TEXT)

    def test_invented_text_is_not_a_quote(self):
        assert not mine.quoted("the kubelet restarts the node", SOURCE_TEXT)

    def test_a_scrap_is_not_a_quote(self):
        # "exit code" appears, but proves nothing about what the source says.
        assert not mine.quoted("exit code", SOURCE_TEXT)


class TestHtml:
    def test_keeps_the_article_and_drops_the_chrome(self):
        html = ("<html><body><nav>Products Pricing</nav><article><h2>Causes</h2>"
                "<p>The container ran out of memory.</p><script>track()</script>"
                "</article><footer>Terms</footer></body></html>")
        text = mine.html_to_text(html)
        assert "ran out of memory" in text and "## Causes" in text
        assert "Pricing" not in text and "track" not in text and "Terms" not in text


class TestManifest:
    def test_the_shipped_manifest_loads(self):
        m = mine.load_manifest()
        assert "pod-restarting" in m.families
        for sid in m.families["pod-restarting"].sources:
            assert m.sources[sid].use == "vendor"

    def test_a_reference_only_source_cannot_be_mined(self):
        data = {"sources": {"x": {"title": "X", "publisher": "P", "url": "u",
                                  "licence": "all-rights-reserved", "use": "reference"}},
                "families": {"f": {"title": "F", "symptoms": [], "scope": "s",
                                   "sources": ["x"]}}}
        with pytest.raises(ValueError, match="reference-only"):
            mine.Manifest.model_validate(data)


# --- layers ----------------------------------------------------------------------------

MINED = {
    "symptoms": {"restarting": {"title": "a pod restarts", "claims": ["missing-dependency"]}},
    "facts": {"logs_connection_refused": {"params": ["Container"],
                                          "says": "the logs show connection refused"}},
    "actions": {"read-logs": {"requires": "k8stools",
                              "call": "get_logs_for_pod_and_container",
                              "produces": ["logs_connection_refused"]}},
    "claims": {"missing-dependency": {"title": "a dependency is unreachable",
                                      "confirm": ["logs_connection_refused[C]"],
                                      "refute": ["not logs_connection_refused[C]"],
                                      "refute_drafted": True},
               "oom": {"title": "mined OOM", "confirm": ["exit_code[C, 137]"],
                       "refute": ["exit_code[C, X], X != 137"]},
               "leak": {"title": "a leak", "confirm": ["growing[C]"],
                        "refute": ["not growing[C]"]}},
    "refinements": {"oom": ["leak"]},
}


def layers(tmp_path, mined=MINED, authored=MINIMAL):
    write_layer(tmp_path / "mined", mined, "pod.yaml")
    write_layer(tmp_path / "knowledge", authored)
    return knowledge.load_layers([tmp_path / "mined", tmp_path / "knowledge"])


class TestLayers:
    def test_mined_candidates_join_an_authored_symptom(self, tmp_path):
        k = layers(tmp_path)
        # Authored first, then what mining added: a lower layer can add a
        # candidate but never take one away.
        assert k.layer.symptoms["restarting"].claims == ["oom", "crash", "missing-dependency"]
        assert k.layer.symptoms["restarting"].title == "restarts"

    def test_authored_hides_a_mined_definition_and_says_so(self, tmp_path):
        k = layers(tmp_path)
        assert k.layer.claims["oom"].title == "OOM"
        assert any("claims oom" in o and "hidden by knowledge" in o for o in k.overridden)

    def test_a_refinement_survives_its_claim_being_redefined(self, tmp_path):
        # The mined layer refines `oom`; the authored layer defines `oom`
        # without that sub-cause. The refinement is additive.
        k = layers(tmp_path)
        assert "leak" in k.layer.claims["oom"].refined_by

    def test_the_combination_checks(self, tmp_path):
        k = layers(tmp_path)
        assert knowledge.check(k).problems == []

    def test_a_refinement_of_nothing_is_a_problem(self, tmp_path):
        mined = copy.deepcopy(MINED)
        mined["refinements"] = {"no-such-claim": ["leak"]}
        k = layers(tmp_path, mined=mined)
        assert any("no-such-claim" in p for p in knowledge.check(k).problems)

    def test_the_build_reads_the_mined_layer_beside_the_authored_one(self, tmp_path):
        write_layer(tmp_path / "mined", MINED, "pod.yaml")
        # Unmapped: a mapped vendored alert has to account for its hypotheses,
        # which TestConversion covers.
        write_layer(tmp_path / "knowledge", MINIMAL | {"alerts": {}})
        data, report = build_mod.build(knowledge_dir=tmp_path / "knowledge")
        assert "missing-dependency" in data["knowledge"]["claims"]
        assert report.mined_claims == 2  # missing-dependency, leak; oom is authored


# --- the pipeline -----------------------------------------------------------------------


class FakeProvider:
    model = "fake-model"

    def __init__(self, extraction, merged, vendored=None):
        self.extraction, self.merged, self.vendored = extraction, merged, vendored
        self.calls: list[str] = []

    def complete(self, system, prompt, schema, name):
        self.calls.append(name)
        if name != "extraction":
            out = self.merged
        elif f"Source `{mine.VENDORED_ID}`" in prompt:
            self.calls.append("vendored")
            out = self.vendored or EMPTY
        else:
            out = self.extraction
        return copy.deepcopy(out), Usage(1000, 500, 0.05)


class FakeResponse(io.BytesIO):
    headers = {"Content-Type": "text/markdown"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_opener(request, timeout=None):
    return FakeResponse(SOURCE_TEXT.encode())


MANIFEST = mine.Manifest.model_validate({
    "sources": {"guide": {"title": "Guide", "publisher": "Docs", "url": "https://e/g",
                          "fetch": "https://e/g.md", "licence": "CC-BY-4.0",
                          "use": "vendor"}},
    "families": {"restarts": {"title": "Restarts", "symptoms": ["restarting"],
                              "scope": "crash loops", "sources": ["guide"]}},
})

EMPTY = {"symptoms": [], "alerts": [], "facts": [], "actions": [], "claims": [],
         "refinements": [], "exclusive": []}

EXTRACTION = EMPTY | {
    "symptoms": [{"name": "restarting", "title": "restarts",
                  "claims": ["missing-dependency"],
                  "quote": "Debugging a crash loop"}],
    "facts": [{"name": "logs_connection_refused", "params": ["Container"],
               "says": "the logs show connection refused", "functional": False,
               "quote": "exits with a `connection refused` error in its logs"}],
    "actions": [{"name": "read-logs", "requires": "k8stools",
                 "call": "get_logs_for_pod_and_container", "read": "the last lines",
                 "produces": ["logs_connection_refused"], "cost": 2,
                 "quality": "complete", "limits": "",
                 "quote": "error in its logs"}],
    "claims": [{"name": "missing-dependency", "title": "a dependency is unreachable",
                "confirm": [["logs_connection_refused[C]"]],
                "refute": [["not logs_connection_refused[C]"]], "supports": [],
                "refined_by": [], "note": "", "refute_drafted": True,
                "quote": "When a dependency is unreachable at startup the process often "
                         "exits"},
               {"name": "cosmic-rays", "title": "cosmic rays",
                "confirm": [["exit_code[C, 1]"]], "refute": [["exit_code[C, 0]"]],
                "supports": [], "refined_by": [], "note": "", "refute_drafted": False,
                "quote": "Cosmic rays flip bits in the container runtime"}],
}


def merged_from(extraction):
    out = {}
    for section, items in extraction.items():
        if section in ("refinements", "exclusive"):
            out[section] = items
            continue
        out[section] = [{k: v for k, v in i.items() if k != "quote"}
                        | {"from": f"guide:{i['name']}"}
                        for i in items if i["name"] != "cosmic-rays"]
    return out


#: Vendored records for the tests: CrashLoopBackOff is converted by the
#: authored layer, PodRestartSpike is left for mining.
RECORDS = {
    "CrashLoopBackOff": {"alert_name": "CrashLoopBackOff", "root_causes": "OOM; crash",
                         "symptom": "Pod restarts", "resource_type": "Pod"},
    "PodRestartSpike": {"alert_name": "PodRestartSpike",
                        "root_causes": "dependency unreachable; node issue",
                        "symptom": "Restarts spike", "resource_type": "Pod",
                        "potential_solutions": "restart the node"},
}
AUTHORED = MINIMAL | {"converts": {"CrashLoopBackOff": {"OOM": "oom", "crash": "crash"}}}


def run(tmp_path, provider, manifest=None, **kw):
    write_layer(tmp_path / "knowledge", AUTHORED)
    return mine.run("restarts", provider=provider, records=RECORDS,
                    tools={"get_workload_report": "Report.",
                           "get_logs_for_pod_and_container": "Logs."},
                    manifest=manifest or MANIFEST, knowledge_dir=tmp_path / "knowledge",
                    cache=tmp_path / "cache", opener=fake_opener, **kw)


class TestPipeline:
    def test_an_unquotable_item_never_reaches_the_merge(self, tmp_path):
        p = FakeProvider(EXTRACTION, merged_from(EXTRACTION))
        report = run(tmp_path, p)
        e = report.extractions[0]
        assert any("cosmic-rays" in d for d in e.dropped)
        assert "cosmic-rays" not in json.dumps(e.items)

    def test_the_draft_checks_and_carries_its_quotes(self, tmp_path):
        report = run(tmp_path, FakeProvider(EXTRACTION, merged_from(EXTRACTION)))
        assert report.problems == [] and not report.repaired
        draft = yaml.safe_load(report.draft.read_text())
        claim = draft["claims"]["missing-dependency"]
        assert claim["refute_drafted"] is True
        assert claim["sources"] == [{"source": "guide", "quote": EXTRACTION["claims"][0]["quote"]}]
        assert "DRAFT" in report.draft.read_text()

    def test_a_merged_item_without_an_extracted_source_is_dropped(self, tmp_path):
        merged = merged_from(EXTRACTION)
        merged["claims"][0]["from"] = "guide:invented"
        report = run(tmp_path, FakeProvider(EXTRACTION, merged))
        assert "missing-dependency" not in report.layer.claims
        assert any("dropped, no source" in n for n in report.merge_notes)

    def test_check_failures_get_one_repair_round(self, tmp_path):
        merged = merged_from(EXTRACTION)
        merged["claims"][0]["refute"] = []  # no refute: the build refuses it
        p = FakeProvider(EXTRACTION, merged)
        report = run(tmp_path, p)
        assert report.repaired and p.calls.count("merge") == 2
        assert any("no `refute`" in x for x in report.problems)

    def test_extractions_are_cached(self, tmp_path):
        p = FakeProvider(EXTRACTION, merged_from(EXTRACTION))
        run(tmp_path, p)
        run(tmp_path, p)
        assert p.calls.count("extraction") == 1

    def test_spending_stops_at_the_cap(self, tmp_path):
        with pytest.raises(RuntimeError, match="max-usd"):
            run(tmp_path, FakeProvider(EXTRACTION, merged_from(EXTRACTION)), max_usd=0.01)


# --- converting the vendored records (007 §8.5) -------------------------------------------

WITH_ALERTS = MANIFEST.model_copy(deep=True)
WITH_ALERTS.families["restarts"].alerts = ["CrashLoopBackOff", "PodRestartSpike"]

VENDORED_EXTRACTION = EMPTY | {
    "alerts": [{"name": "PodRestartSpike", "symptom": "restarting",
                "quote": "PodRestartSpike reports: Restarts spike"}],
    "claims": [{"name": "dependency-unreachable", "title": "dependency unreachable",
                "confirm": [["logs_connection_refused[C]"]],
                "refute": [["not logs_connection_refused[C]"]], "supports": [],
                "refined_by": [], "note": "", "refute_drafted": True,
                "quote": "PodRestartSpike candidate cause: dependency unreachable"}],
}


def merged_with_vendored():
    merged = merged_from(EXTRACTION)
    merged["claims"][0]["from"] = ("guide:missing-dependency, "
                                   "vendored-kb:dependency-unreachable")
    merged["alerts"] = [{"name": "PodRestartSpike", "symptom": "restarting",
                         "from": "vendored-kb:PodRestartSpike"}]
    return merged


class TestConversion:
    def test_render_lists_each_hypothesis_on_its_own_line_and_no_remediation(self):
        text = mine.render_vendored(RECORDS, ["PodRestartSpike"])
        assert "PodRestartSpike candidate cause: dependency unreachable" in text
        assert "PodRestartSpike candidate cause: node issue" in text
        assert "restart the node" not in text
        assert "CrashLoopBackOff" not in text

    def test_only_unconverted_alerts_are_read(self, tmp_path):
        p = FakeProvider(EXTRACTION, merged_with_vendored(), VENDORED_EXTRACTION)
        report = run(tmp_path, p, manifest=WITH_ALERTS)
        assert p.calls.count("vendored") == 1
        assert "CrashLoopBackOff" not in report.layer.converts

    def test_a_cited_hypothesis_is_converted_and_an_uncited_one_left_for_review(self, tmp_path):
        p = FakeProvider(EXTRACTION, merged_with_vendored(), VENDORED_EXTRACTION)
        report = run(tmp_path, p, manifest=WITH_ALERTS)
        table = report.layer.converts["PodRestartSpike"]
        assert table["dependency unreachable"] == "missing-dependency"
        assert table["node issue"].declined.startswith(knowledge.UNDECIDED)
        # Undecided is a failing check, so the draft cannot ship as it is.
        assert any("'node issue' is undecided" in x for x in report.problems)

    def test_a_family_alert_left_unmapped_is_a_problem(self, tmp_path):
        merged = merged_with_vendored()
        merged["alerts"] = []
        report = run(tmp_path, FakeProvider(EXTRACTION, merged, VENDORED_EXTRACTION),
                     manifest=WITH_ALERTS)
        assert any("PodRestartSpike is not mapped" in x for x in report.problems)


class TestConversionCheck:
    """The build's own check: a mapped vendored alert accounts for every hypothesis."""

    LEGACY = {"CrashLoopBackOff": ["OOM", "crash"]}

    def check(self, tmp_path, converts):
        k = knowledge.load(write_layer(tmp_path / "k", MINIMAL | {"converts": converts}))
        return knowledge.check(k, legacy_hypotheses=self.LEGACY).problems

    def test_complete(self, tmp_path):
        assert self.check(tmp_path, {"CrashLoopBackOff": {"OOM": "oom", "crash": "crash"}}) == []

    def test_a_missing_hypothesis_fails(self, tmp_path):
        got = self.check(tmp_path, {"CrashLoopBackOff": {"OOM": "oom"}})
        assert any("'crash' is not converted" in p for p in got)

    def test_a_reasoned_decline_passes_and_an_empty_one_fails(self, tmp_path):
        ok = {"CrashLoopBackOff": {"OOM": "oom", "crash": {"declined": "a node symptom"}}}
        assert self.check(tmp_path, ok) == []
        bad = {"CrashLoopBackOff": {"OOM": "oom", "crash": {"declined": "TODO: decide"}}}
        assert any("undecided" in p for p in self.check(tmp_path, bad))

    def test_a_claim_the_symptom_cannot_reach_fails(self, tmp_path):
        data = MINIMAL | {"converts": {"CrashLoopBackOff": {"OOM": "oom", "crash": "orphan"}}}
        data = copy.deepcopy(data)
        data["claims"]["orphan"] = {"title": "o", "confirm": ["growing[C]"],
                                    "refute": ["not growing[C]"]}
        k = knowledge.load(write_layer(tmp_path / "k", data))
        got = knowledge.check(k, legacy_hypotheses=self.LEGACY).problems
        assert any("does not reach" in p for p in got)


class TestReview:
    def test_an_action_calling_a_tool_another_layer_calls_is_flagged(self):
        """007 §8.3: a mined duplicate of an authored action is for the reader
        to merge, not a failure -- two reads of one tool are often both right."""
        authored = knowledge.Layer.model_validate(MINIMAL)
        draft = knowledge.Layer(actions={
            "read-exit": knowledge.Action(requires="k8stools", call="get_workload_report",
                                          produces=["exit_code"]),
            "read-logs": knowledge.Action(requires="k8stools", call="get_logs_for_pod_and_container",
                                          produces=["exit_code"])})
        k = knowledge.combine([("knowledge", knowledge.Knowledge(layer=authored)),
                               ("mined", knowledge.Knowledge(layer=draft))])
        got = mine.overlaps(draft, k)
        assert got == ["action read-exit calls get_workload_report, as read-state does: "
                       "merge it unless it reads something different"]
