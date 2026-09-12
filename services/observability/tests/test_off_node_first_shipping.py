"""Acceptance criterion: "every event ships to the real local sink
continuously as it's produced, not accumulated in memory and flushed at
the end" (master spec Section 16.4's "off-node-first" primary channel).

Real test, per the task brief: raise inside the middle of a sequence
(simulating the process crashing mid-run) and confirm every event
emitted before the crash already reached the sink -- read back via a
BRAND NEW `EventSink`/reader that never shared a Python object, a
thread, or any in-memory state with whatever produced the events, the
same way a real operator (or a real Loki/Tempo query) would read
whatever a now-dead node already shipped.
"""

from __future__ import annotations

from orchestrator.hooks import DestructiveCommandHook, HookChain, HookChainBlockedError, ToolCallContext

from observability import EventSink, ObservabilityClient, read_events_from_file


class _CrashSimulatingTool:
    """A tool implementation that succeeds N times, then raises on call
    N+1 -- the process's "crash" for this test. Everything dispatched
    before the raise must already be durable off-node by the time it
    happens; nothing about this class's own state (a plain in-memory
    counter) is what the test verifies -- the sink's file is."""

    def __init__(self, crash_on_call: int):
        self._crash_on_call = crash_on_call
        self.calls = 0

    def __call__(self, arguments: dict):
        self.calls += 1
        if self.calls == self._crash_on_call:
            raise RuntimeError("simulated node/process crash mid-run")
        return {"ok": True, "call": self.calls}


def test_every_event_before_a_simulated_crash_already_reached_the_off_node_store(tmp_path):
    store_path = tmp_path / "off_node_store.jsonl"

    # ---- "Process 1": ships events as it goes, then crashes. This
    # function's own `sink`/`observability`/`hook_chain` objects are
    # deliberately never referenced again after this block -- everything
    # after is read back from nothing but the file on disk. ----
    def run_process_and_crash():
        sink = EventSink(path=store_path)
        observability = ObservabilityClient(sink=sink, service_name="orchestrator")
        hook_chain = HookChain(
            [DestructiveCommandHook()], observability=observability, trace_id="trace-crash-mid-run"
        )
        tool = _CrashSimulatingTool(crash_on_call=4)  # crashes on the 4th of 6 intended calls

        for i in range(6):
            ctx = ToolCallContext(
                tool_name="write_file",
                arguments={"path": f"src/file_{i}.py"},
                invoking_skill="implementer",
                stage="implementation",
                run_id="run-crash-1",
            )
            hook_chain.dispatch(ctx, tool)
        # unreachable in the successful case -- the loop above always
        # raises by the 4th iteration.

    try:
        run_process_and_crash()
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "simulated node/process crash" in str(exc)
    assert raised, "expected the 4th tool call to raise, simulating a mid-run crash"

    # ---- Read-back: a completely independent reconstruction from the
    # file alone (no reference to the `sink`/`observability`/`hook_chain`
    # objects created inside `run_process_and_crash` -- they are out of
    # scope and garbage by now). ----
    recovered = read_events_from_file(store_path)
    tool_call_events = [e for e in recovered if e.name == "tool_call"]

    # Exactly 4 tool_call events reached the store: 3 successful, 1 with
    # the crash's own error recorded -- never more (calls 5/6 never ran),
    # never fewer (nothing buffered/lost between calls 1-3 and the crash).
    assert len(tool_call_events) == 4
    assert [e.attributes.get("error", "") for e in tool_call_events] == ["", "", "", "simulated node/process crash mid-run"]
    for e in tool_call_events:
        assert e.trace_id == "trace-crash-mid-run"
        assert e.run_id == "run-crash-1"


def test_hook_chain_blocked_call_also_ships_before_any_later_crash(tmp_path):
    """The BLOCK branch is a third code path in `HookChain.dispatch`
    (distinct from success and from a raised tool_fn exception) -- prove
    it also ships off-node-first, not just the two paths above."""
    store_path = tmp_path / "off_node_store_block.jsonl"
    sink = EventSink(path=store_path)
    observability = ObservabilityClient(sink=sink, service_name="orchestrator")
    hook_chain = HookChain([DestructiveCommandHook()], observability=observability, trace_id="trace-blocked")

    ctx = ToolCallContext(
        tool_name="run_shell",
        arguments={"command": "rm -rf /important"},
        invoking_skill="implementer",
        stage="implementation",
        run_id="run-blocked-1",
    )
    try:
        hook_chain.dispatch(ctx, lambda args: None)
        blocked = False
    except HookChainBlockedError:
        blocked = True
    assert blocked

    recovered = read_events_from_file(store_path)
    blocked_events = [e for e in recovered if e.name == "tool_call"]
    assert len(blocked_events) == 1
    assert blocked_events[0].attributes["blocked_by"] == "DestructiveCommandHook"
