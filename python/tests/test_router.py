"""Tests for message routing."""

from embedded_bridge.receivers.router import Router


class SimpleReceiver:
    """Test receiver that records all fed messages."""

    def __init__(self):
        self.messages: list[bytes | str] = []

    def feed(self, message: bytes | str) -> None:
        self.messages.append(message)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestRouting:
    def test_none_predicate_receives_all(self):
        r = SimpleReceiver()
        router = Router([(r, None)])
        router.feed("line 1")
        router.feed("line 2")
        assert r.messages == ["line 1", "line 2"]

    def test_predicate_filters_messages(self):
        r = SimpleReceiver()
        router = Router([(r, lambda m: isinstance(m, str) and m.startswith("OK"))])
        router.feed("OK result")
        router.feed("ERROR something")
        router.feed("OK done")
        assert r.messages == ["OK result", "OK done"]

    def test_multiple_receivers_independent(self):
        trace = SimpleReceiver()
        event = SimpleReceiver()
        router = Router([
            (trace, lambda m: isinstance(m, str) and m.startswith('{"ph":')),
            (event, lambda m: isinstance(m, str) and m.startswith("T=")),
        ])
        router.feed('{"ph":"B","ts":1000}')
        router.feed("T=0.001 GPS_STARTED")
        router.feed("regular log line")

        assert len(trace.messages) == 1
        assert len(event.messages) == 1

    def test_message_matches_multiple_receivers(self):
        r1 = SimpleReceiver()
        r2 = SimpleReceiver()
        # Both predicates match the same message
        router = Router([
            (r1, lambda m: "data" in m),
            (r2, lambda m: "data" in m),
        ])
        router.feed("data payload")
        assert r1.messages == ["data payload"]
        assert r2.messages == ["data payload"]

    def test_none_predicate_and_specific_predicate(self):
        catch_all = SimpleReceiver()
        specific = SimpleReceiver()
        router = Router([
            (catch_all, None),
            (specific, lambda m: isinstance(m, str) and m.startswith("TRACE")),
        ])
        router.feed("TRACE event")
        router.feed("other")

        assert catch_all.messages == ["TRACE event", "other"]
        assert specific.messages == ["TRACE event"]

    def test_passthrough_gets_unmatched(self):
        passed: list[bytes | str] = []
        specific = SimpleReceiver()
        router = Router(
            [(specific, lambda m: isinstance(m, str) and m.startswith("X"))],
            passthrough=passed.append,
        )
        router.feed("X matched")
        router.feed("not matched")
        router.feed("also not")

        assert specific.messages == ["X matched"]
        assert passed == ["not matched", "also not"]

    def test_no_passthrough_silently_drops(self):
        specific = SimpleReceiver()
        router = Router([(specific, lambda m: False)])
        router.feed("will not match")
        assert specific.messages == []
        # no error raised

    def test_none_predicate_does_not_count_as_match_for_passthrough(self):
        """A catch-all (None predicate) receiver doesn't prevent passthrough."""
        catch_all = SimpleReceiver()
        passed: list[bytes | str] = []
        router = Router(
            [(catch_all, None)],
            passthrough=passed.append,
        )
        router.feed("message")
        assert catch_all.messages == ["message"]
        assert passed == ["message"]  # also goes to passthrough


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------


class TestAdd:
    def test_add_receiver_after_construction(self):
        router = Router()
        r = SimpleReceiver()
        router.add(r)
        router.feed("hello")
        assert r.messages == ["hello"]

    def test_add_with_predicate(self):
        router = Router()
        r = SimpleReceiver()
        router.add(r, lambda m: isinstance(m, str) and m.startswith("OK"))
        router.feed("OK yes")
        router.feed("NO")
        assert r.messages == ["OK yes"]


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


class TestNesting:
    def test_router_as_receiver_in_another_router(self):
        inner_recv = SimpleReceiver()
        inner = Router([(inner_recv, None)])

        outer = Router([(inner, lambda m: isinstance(m, str) and m.startswith("FWD"))])
        outer.feed("FWD data")
        outer.feed("not forwarded")

        assert inner_recv.messages == ["FWD data"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_router_does_not_raise(self):
        router = Router()
        router.feed("anything")  # no error

    def test_bytes_messages(self):
        r = SimpleReceiver()
        router = Router([(r, None)])
        router.feed(b"binary data")
        assert r.messages == [b"binary data"]

    def test_mixed_bytes_and_str(self):
        r = SimpleReceiver()
        router = Router([(r, None)])
        router.feed("text")
        router.feed(b"binary")
        assert r.messages == ["text", b"binary"]

    def test_predicate_exception_does_not_stop_routing(self):
        """A broken predicate logs and skips, doesn't kill the router."""
        broken = SimpleReceiver()
        healthy = SimpleReceiver()

        def bad_predicate(m):
            raise ValueError("broken")

        router = Router([
            (broken, bad_predicate),
            (healthy, None),
        ])
        router.feed("test")
        assert broken.messages == []
        assert healthy.messages == ["test"]
