"""Acceptance criterion: "A run resumes correctly from its last Registry
checkpoint after a simulated process restart (durable execution, Section
16.1)."

The proof shape: build one `Orchestrator`, drive it partway, then throw
that whole Python object away (`del orchestrator`) and construct a
completely fresh one from nothing but the on-disk state (`RegistryService`
re-opened on the same DB file, and fresh `PlanArtifactStore`/
`RunProgressStore` instances re-opened on the same directories) -- proving
resumption reads from durable storage, not from any in-process cache.
"""
from __future__ import annotations

from run_registry import RegistryService

from orchestrator.core import Orchestrator
from orchestrator.model_backend import ScriptedAgentBackend, SubTask
from orchestrator.plan_artifact import PlanArtifactStore
from orchestrator.progress import RunProgressStore
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult

from ._factories import make_diff_output, make_plan_output
from .conftest import make_orchestrator


def test_resume_after_simulated_process_restart_mid_implementation(tmp_path, tenant_id):
    db_path = str(tmp_path / "registry.db")
    plan_dir = tmp_path / "plan_artifacts"
    progress_dir = tmp_path / "progress"

    # ---- "Process 1": drive to the plan gate, approve it, and run the
    # first of two subtasks -- then the process disappears. ----
    plan = make_plan_output(
        scope_in=("src/a.py", "src/b.py"),
        subtasks=[
            SubTask(task_id="t1", description="implement a", parallel_group=None, depends_on=()),
            SubTask(task_id="t2", description="implement b", parallel_group=None, depends_on=("t1",)),
        ],
    )
    diffs = [make_diff_output(files_touched=("src/a.py",), lines_changed=5, subtask_id="t1")]
    backend_1 = ScriptedAgentBackend(plans=[plan], diffs=diffs)

    registry_1 = RegistryService(db_path)
    orch_1 = Orchestrator(
        registry=registry_1, tenant_id=tenant_id, agent_backend=backend_1,
        verification_runner=ScriptedVerificationRunner(), plan_store=PlanArtifactStore(plan_dir),
        progress_store=RunProgressStore(progress_dir),
    )
    status = orch_1.start_run(jira_key="PROJ-20", repo="acme/app", branch="feature/resume", trace_id="trace-20")
    run_id = status.run_id
    # Only one scripted diff -- the drive loop completes t1 (persisting
    # its progress for real), then immediately attempts t2 and blows up
    # because backend_1's script is exhausted. That failure inside the
    # very same call is this test's stand-in for "process 1 crashes mid
    # subtask t2": t1's progress was already durably saved beforehand.
    try:
        orch_1.approve_plan(status.run_id, decision="approve")
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "expected process 1 to fail attempting subtask t2 with an exhausted script"

    registry_1.close()
    del orch_1
    del registry_1
    del backend_1

    # ---- "Process 2": a brand new Orchestrator, brand new RegistryService
    # (re-opened on the same DB file), brand new stores (re-opened on the
    # same directories), and a fresh AgentBackend that only knows how to
    # do t2. It must resume exactly where process 1 left off: t1 already
    # done, t2 remaining -- never redoing t1, never losing the plan. ----
    registry_2 = RegistryService(db_path)
    backend_2 = ScriptedAgentBackend(diffs=[make_diff_output(files_touched=("src/b.py",), lines_changed=5, subtask_id="t2")])
    orch_2 = Orchestrator(
        registry=registry_2, tenant_id=tenant_id, agent_backend=backend_2,
        verification_runner=ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")]),
        plan_store=PlanArtifactStore(plan_dir), progress_store=RunProgressStore(progress_dir),
    )

    resumed_status = orch_2.resume_run(run_id)

    assert resumed_status.stage == "change_review_gate"
    assert resumed_status.paused is True
    assert resumed_status.pause_kind == "gate"
    assert backend_2.implement_call_count == 1  # only t2 was (re-)executed, not t1 again

    progress = RunProgressStore(progress_dir).load(run_id)
    assert set(progress.completed_subtask_ids) == {"t1", "t2"}
    assert set(progress.files_touched) == {"src/a.py", "src/b.py"}

    registry_2.close()


def test_resume_after_restart_while_paused_at_a_pending_checkpoint(tmp_path, tenant_id):
    """The other durable-pause shape: the process restarts while a
    Section 9.3 checkpoint (not a gate) is pending. Resuming must still
    see the pending elicitation and refuse to silently continue past it."""
    db_path = str(tmp_path / "registry.db")
    plan_dir = tmp_path / "plan_artifacts"
    progress_dir = tmp_path / "progress"

    plan = make_plan_output(scope_in=("src/a.py",), subtasks=[SubTask(task_id="t1", description="a", parallel_group=None, depends_on=())])
    diff = make_diff_output(files_touched=("src/a.py", "src/oops.py"), lines_changed=5, subtask_id="t1")

    registry_1 = RegistryService(db_path)
    orch_1 = make_orchestrator(
        registry=registry_1, tenant_id=tenant_id, plan_store=PlanArtifactStore(plan_dir),
        progress_store=RunProgressStore(progress_dir), agent_backend=ScriptedAgentBackend(plans=[plan], diffs=[diff]),
    )
    status = orch_1.start_run(jira_key="PROJ-21", repo="acme/app", branch="feature/resume-cp", trace_id="trace-21")
    status = orch_1.approve_plan(status.run_id, decision="approve")
    assert status.pause_kind == "checkpoint"
    run_id = status.run_id
    registry_1.close()
    del orch_1, registry_1

    registry_2 = RegistryService(db_path)
    orch_2 = make_orchestrator(
        registry=registry_2, tenant_id=tenant_id, plan_store=PlanArtifactStore(plan_dir),
        progress_store=RunProgressStore(progress_dir), agent_backend=ScriptedAgentBackend(),
    )
    resumed = orch_2.resume_run(run_id)
    assert resumed.paused is True
    assert resumed.pause_kind == "checkpoint"
    assert resumed.checkpoint.trigger == "risk"
    registry_2.close()
