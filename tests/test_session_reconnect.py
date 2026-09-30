"""A turn survives a dropped event stream.

SSE has no replay. A scenario batch was lost when a run's stream closed
mid-turn ("peer closed connection without sending complete message"): the
session carried on unobserved and the error killed the remaining runs. The
Slack relay had the same gap, where a drop loses the answer for the user.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx2
import pytest

from k8srca.session import consume, sent_event_id, turn_events
from k8srca.slack.relay import consume as relay_consume


def ev(eid, etype, **kw):
    return SimpleNamespace(id=eid, type=etype, **kw)


def msg(eid, text):
    return ev(eid, "agent.message", content=[SimpleNamespace(type="text", text=text)])


def tool(eid, name):
    return ev(eid, "agent.custom_tool_use", name=name)


def idle(eid, reason="end_turn"):
    return ev(eid, "session.status_idle", stop_reason=SimpleNamespace(type=reason))


def user(eid):
    return ev(eid, "user.message", content=[])


def drop():
    return httpx2.RemoteProtocolError("peer closed connection without sending complete message")


class Stream:
    """Yields its events, then raises `fail` if given. Records close()."""

    def __init__(self, events, fail=None):
        self.events, self.fail, self.closed = list(events), fail, False

    def __iter__(self):
        yield from self.events
        if self.fail is not None:
            raise self.fail

    def close(self):
        self.closed = True


class Client:
    """Just the surface turn_events touches: reopen a stream, list history."""

    def __init__(self, reopened, history, list_failures=0):
        self.reopened = list(reopened)
        self.history = list(history)
        self.list_failures = list_failures
        self.opened: list[Stream] = []
        self.beta = SimpleNamespace(sessions=SimpleNamespace(events=SimpleNamespace(
            stream=self._stream, list=self._list)))

    def _stream(self, session_id):
        s = self.reopened.pop(0)
        self.opened.append(s)
        return s

    def _list(self, session_id):
        if self.list_failures:
            self.list_failures -= 1
            raise drop()
        return iter(self.history)


#: A previous turn, then this one. The previous turn's final idle is the thing
#: a careless replay would deliver, ending this turn with the last one's answer.
PREVIOUS = [user("u1"), msg("m0", "old answer"), idle("i0")]


def run(client, first, **kw):
    return consume(turn_events(client, "sesn_x", first, sleep=lambda s: None, **kw))


def test_a_drop_mid_turn_recovers_every_event_exactly_once():
    first = Stream([tool("t1", "get_pods"), msg("m1", "looking")], fail=drop())
    history = PREVIOUS + [user("u2"), tool("t1", "get_pods"), msg("m1", "looking"),
                          tool("t2", "get_logs"), msg("m2", "the answer")]
    # The reopened stream re-sends what history already had; seen-ids drop it.
    client = Client([Stream([msg("m2", "the answer"), idle("i2")])], history)

    turn = run(client, first, after="u2")

    assert turn.tool_calls == ["get_pods", "get_logs"]
    assert turn.messages == ["looking", "the answer"]
    assert turn.stop_reason == "end_turn"


def test_a_drop_before_any_event_does_not_replay_the_previous_turn():
    first = Stream([], fail=drop())
    history = PREVIOUS + [user("u2"), msg("m2", "new answer")]
    client = Client([Stream([idle("i2")])], history)

    turn = run(client, first, after="u2")

    assert turn.messages == ["new answer"]
    assert "old answer" not in turn.messages


def test_a_first_turn_replays_from_the_start_of_the_session():
    first = Stream([], fail=drop())
    history = [user("u1"), msg("m1", "answer")]
    client = Client([Stream([idle("i1")])], history)

    turn = run(client, first, from_start=True)

    assert turn.messages == ["answer"]
    assert turn.stop_reason == "end_turn"


def test_a_sent_message_not_yet_in_history_replays_nothing():
    """Queued, so the turn has produced nothing yet -- and earlier history is
    the previous turn's, which must not be delivered."""
    first = Stream([], fail=drop())
    client = Client([Stream([msg("m2", "new answer"), idle("i2")])], PREVIOUS)

    turn = run(client, first, after="u2")

    assert turn.messages == ["new answer"]


def test_the_network_still_down_at_reconnect_is_retried():
    first = Stream([msg("m1", "part")], fail=drop())
    history = [user("u1"), msg("m1", "part"), msg("m2", "rest")]
    client = Client([Stream([]), Stream([idle("i1")])], history, list_failures=1)

    turn = run(client, first, from_start=True)

    assert turn.messages == ["part", "rest"]
    assert client.opened[0].closed, "the stream opened before the failed list leaks"


def test_it_gives_up_after_the_reconnect_budget():
    first = Stream([], fail=drop())
    client = Client([Stream([], fail=drop()) for _ in range(3)], [user("u1")])

    with pytest.raises(httpx2.RemoteProtocolError):
        run(client, first, from_start=True, reconnects=2)


def test_a_reopened_stream_is_closed_when_the_turn_ends():
    first = Stream([], fail=drop())
    reopened = Stream([msg("m1", "answer"), idle("i1"), msg("m9", "never read")])
    client = Client([reopened], [user("u1")])

    run(client, first, from_start=True)

    assert reopened.closed


def test_an_api_error_is_not_mistaken_for_a_drop():
    """A refusal from the API is not a connection problem; retrying hides it."""
    first = Stream([], fail=ValueError("not a transport error"))
    client = Client([], [])

    with pytest.raises(ValueError):
        run(client, first, from_start=True)


def test_the_slack_relay_survives_a_drop_too():
    first = Stream([tool("t1", "get_pods")], fail=drop())
    history = PREVIOUS + [user("u2"), tool("t1", "get_pods"), msg("m2", "the answer")]
    client = Client([Stream([idle("i2")])], history)
    slack_turn = SimpleNamespace(status=lambda *_: None)

    render = relay_consume(turn_events(client, "sesn_x", first, after="u2",
                                       sleep=lambda s: None), slack_turn)

    assert render.messages == ["the answer"]
    assert render.tools == ["get_pods"]


def test_sent_event_id_reads_the_first_accepted_event():
    assert sent_event_id(SimpleNamespace(data=[SimpleNamespace(id="u2")])) == "u2"
    assert sent_event_id(SimpleNamespace(data=None)) is None
    assert sent_event_id(None) is None
