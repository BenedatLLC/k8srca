"""Session latency decomposition (design 003 §2.3(d))."""

from datetime import datetime, timedelta, timezone

from k8srca.timing import build, render


class E:
    def __init__(self, type_, at):
        self.type, self.processed_at = type_, at


T0 = datetime(2026, 9, 9, 3, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class TestBuild:
    def test_measures_model_spans(self):
        tl = build([E("span.model_request_start", at(0)), E("span.model_request_end", at(3))])
        assert tl.model_seconds == 3 and tl.model_requests == [3.0]

    def test_measures_tool_calls(self):
        tl = build([E("agent.custom_tool_use", at(1)), E("user.custom_tool_result", at(4))])
        assert tl.tool_seconds == 3
        # Start times are relative to the session's FIRST event, so a tool call
        # that opens the session sits at 0.0 regardless of its wall-clock time.
        assert tl.tool_calls == [(0.0, 3.0)]

    def test_start_times_are_relative_to_the_first_event(self):
        tl = build([E("session.status_running", at(10)),
                    E("agent.tool_use", at(13)), E("user.tool_result", at(15))])
        assert tl.tool_calls == [(3.0, 2.0)]

    def test_unaccounted_is_the_remainder(self):
        # The gap that matters: queueing and sandbox availability show up
        # nowhere else.
        tl = build([
            E("session.status_running", at(0)),
            E("span.model_request_start", at(0)), E("span.model_request_end", at(2)),
            E("agent.tool_use", at(2)), E("user.tool_result", at(5)),
            E("session.status_idle", at(20)),
        ])
        assert tl.wall == 20 and tl.model_seconds == 2 and tl.tool_seconds == 3
        assert tl.unaccounted == 15

    def test_unaccounted_never_goes_negative(self):
        # Parallel tool calls can sum past the wall clock.
        tl = build([
            E("agent.tool_use", at(0)), E("agent.tool_use", at(0)),
            E("user.tool_result", at(5)), E("user.tool_result", at(5)),
        ])
        assert tl.unaccounted == 0

    def test_counts_delegations(self):
        assert build([E("session.thread_created", at(1))]).delegations == 1

    def test_first_tool_wait_is_from_session_start(self):
        tl = build([E("session.status_running", at(0)),
                    E("agent.tool_use", at(1)), E("user.tool_result", at(45))])
        assert tl.first_tool_wait == 45

    def test_events_out_of_order_are_sorted(self):
        tl = build([E("span.model_request_end", at(5)), E("span.model_request_start", at(1))])
        assert tl.model_seconds == 4

    def test_empty_input(self):
        assert build([]).wall == 0
        assert render(build([])) == "no timed events"


class TestRender:
    def test_flags_a_long_first_tool_wait(self):
        # 60s before the first tool result is the poller being busy, not a
        # slow tool -- the report must not let that be misread.
        tl = build([E("session.status_running", at(0)),
                    E("agent.tool_use", at(1)), E("user.tool_result", at(60))])
        assert "sandbox" in render(tl) and "poller" in render(tl)

    def test_stays_quiet_when_startup_was_fast(self):
        tl = build([E("agent.tool_use", at(0)), E("user.tool_result", at(2))])
        assert "poller" not in render(tl)

    def test_includes_cost_when_known(self):
        tl = build([E("agent.tool_use", at(0)), E("user.tool_result", at(2))])
        assert "$0.27" in render(tl, cost_cents=27)
