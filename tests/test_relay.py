"""Slack rendering (design 001 §7.5)."""

from k8srca.slack.relay import TurnRender, chunk, describe_tool, to_mrkdwn, _summarize


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert chunk("hello") == ["hello"]

    def test_every_chunk_fits_slacks_limit(self):
        parts = chunk("word " * 4000, limit=3800)
        assert len(parts) > 1 and all(len(p) <= 3800 for p in parts)

    def test_prefers_paragraph_boundaries(self):
        text = "a" * 3000 + "\n\n" + "b" * 1500
        parts = chunk(text, limit=3800)
        assert parts[0].endswith("a") and parts[1].startswith("b")

    def test_loses_no_content(self):
        text = "\n\n".join("para " + "x" * 900 for _ in range(8))
        assert sum(len(p) for p in chunk(text)) >= len(text) - 20


class TestMrkdwn:
    def test_headings_become_bold(self):
        assert to_mrkdwn("## Finding") == "*Finding*"

    def test_double_asterisk_becomes_single(self):
        # Slack renders **bold** literally.
        assert to_mrkdwn("**Cause** here") == "*Cause* here"

    def test_bullets_normalized(self):
        assert to_mrkdwn("- one\n* two") == "• one\n• two"

    def test_leaves_plain_text_alone(self):
        assert to_mrkdwn("nothing to do here") == "nothing to do here"


class TestTurnRender:
    def test_answer_is_the_last_message_not_the_first(self):
        # A delegating turn emits narration first ("I'll wait for its
        # report"); only the final message is the answer (001 §7.5).
        r = TurnRender(messages=["I'll wait for the subagent.", "**Finding** — OOMKilled."])
        assert r.answer == "**Finding** — OOMKilled."

    def test_no_answer_when_nothing_was_said(self):
        assert TurnRender().answer is None

    def test_status_line_describes_the_latest_tool(self):
        r = TurnRender(tools=["k8s_get_pod_summaries", "k8s_get_pod_events"])
        assert "pod events" in r.status_line

    def test_status_line_mentions_delegation(self):
        assert "delegated" in TurnRender(tools=["bash"], delegations=2).status_line

    def test_status_line_has_a_default(self):
        assert TurnRender().status_line == "thinking"


class TestToolLabels:
    def test_known_tools_read_naturally(self):
        assert describe_tool("k8s_get_logs_for_pod_and_container") == "reading container logs"

    def test_unknown_tools_degrade_readably(self):
        assert describe_tool("k8s_get_pvc_summaries") == "running get pvc summaries"


class TestSummarize:
    def test_takes_the_first_meaningful_line(self):
        assert _summarize("\n\n## Checking pods\nmore text") == "Checking pods"

    def test_truncates(self):
        assert _summarize("x" * 200).endswith("…")
