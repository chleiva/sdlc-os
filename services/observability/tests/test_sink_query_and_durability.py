"""Direct unit coverage of `EventSink`/`read_events_from_file`'s own
query surface and durability, independent of any instrumented service --
the foundation every other test in this package builds on.
"""

from __future__ import annotations

from observability.sink import EventSink, read_events_from_file


def test_emit_is_immediately_queryable_in_process():
    sink = EventSink()
    ev = sink.emit(kind="log", name="hello", trace_id="t1", run_id="r1", tenant_id="ten1", attributes={"x": 1})
    assert sink.all_events() == [ev]
    assert sink.by_trace_id("t1") == [ev]
    assert sink.by_run_id("r1") == [ev]
    assert sink.by_kind("log") == [ev]
    assert sink.by_kind("metric") == []


def test_sequence_is_strictly_increasing_across_many_emits():
    sink = EventSink()
    events = [sink.emit(kind="log", name=f"e{i}") for i in range(50)]
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == 50


def test_file_backed_sink_is_durably_readable_by_an_independent_reader(tmp_path):
    path = tmp_path / "store.jsonl"
    sink = EventSink(path=path)
    sink.emit(kind="span", name="a", trace_id="t1")
    sink.emit(kind="metric", name="b", trace_id="t1", attributes={"amount": 1.5})
    sink.close()

    # A reader with zero reference to `sink` -- only the file path.
    recovered = read_events_from_file(path)
    assert [e.name for e in recovered] == ["a", "b"]
    assert recovered[1].attributes["amount"] == 1.5


def test_read_events_from_file_on_a_missing_file_returns_empty_not_an_error(tmp_path):
    assert read_events_from_file(tmp_path / "never_written.jsonl") == []
