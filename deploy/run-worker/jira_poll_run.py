#!/usr/bin/env python3
"""The Jira-triggered sibling of `live_run.py` -- a real story in a real
Jira Cloud site, not a CLI argument, is the task source.

**Why polling, not a push webhook.** The spec's real production path is
Jira Automation (a rule watching for the trigger status) -> a signed
webhook relay -> `job-dispatcher` -> the orchestrator (see
`services/issue-tracker/SETUP.md`, steps 6-7). That needs a real OAuth
app registration, a publicly-reachable relay (Jira Cloud cannot call
`localhost`), and `job-dispatcher` actually calling the orchestrator --
which it does not do anywhere in this codebase yet (a real, disclosed
gap -- CLAUDE.md's "Known cross-deliverable gaps"). Polling sidesteps
all three: this script uses the exact same real `JiraClient`/`gating.py`
D4 already built and tested, just called from a loop instead of from a
webhook handler, to prove "create a story in Jira -> real PR" end to
end without first standing up that additional infrastructure. Swapping
this script out for the real push-webhook path later does not change
anything about the orchestrator wiring itself (`_run_lib.py`), only how
a task's arrival is detected.

**Real gap this closes (New): a paused run no longer needs anyone at a
keyboard.** This script used to call `_run_lib.run_once`, which blocks
on a terminal `input()` for every gate/checkpoint -- structurally
impossible to satisfy for an unattended, automatically-triggered run
(there is no terminal). Every pause now instead: posts a real Jira
comment describing exactly what's needed and how to reply, real-
assigns the issue to a real person (`JiraClient.assign_issue`), and
persists enough state (`data/jira_pending_decisions.json`) to resume
the *same* run later -- then this invocation simply exits. A later poll
tick (`_check_pending_decisions`) looks for a real new reply comment
(`approve`/`reject` for a gate, `continue`/`stop` for a checkpoint) --
identified by comment id ordering, not by comment author (a real bug
found and fixed: filtering out "the bot's own account" breaks for the
common single-user setup where your own API token *is* your own Jira
account, so your reply and the bot's notification share one identity)
-- and, if found, resumes that exact run with the decision applied, via
`_run_lib.resume_paused_run_async` -- which may pause again (another
comment/assignment, another wait) or finish for real (PR opened, or
abandoned), exactly like the first pass would have, just spread across
separate invocations instead of one blocking process.

**Real gap this ALSO closes (New): Section 12 autonomy levels are now
actually consulted.** `services/gates.autonomy` already implements
L0-L3 (master spec Sec. 12) -- L3 requires neither the plan-approval
nor the change-review gate, only a genuine Sec. 9.3 checkpoint anomaly
still pauses -- but nothing in the orchestrator's own drive loop ever
checked it; every pause always asked a human regardless of level. This
script now defaults to L3 (an automatic, unattended trigger should not
stop for a routine gate); `live_run.py` still defaults to L1 (ask at
every gate -- a human is right there). Override either with the
`AUTONOMY_LEVEL` env var. See `_run_lib.drive`'s own docstring.

Usage:
    cd deploy/run-worker
    python3 jira_poll_run.py            # one tick: check pending decisions, then look for one new story
    python3 jira_poll_run.py --watch 60 # one tick every 60s, until Ctrl-C

Reads configuration from the repo root's `.env` (see `.env.example`'s
JIRA_* block): JIRA_BASE_URL, JIRA_PROJECT_KEY, JIRA_TRIGGER_STATUS,
JIRA_OPT_IN_LABEL, JIRA_APPROVAL_STATUS, JIRA_DONE_STATUS,
JIRA_AUTH_MODE (+ the matching credential pair -- JIRA_BASIC_AUTH_EMAIL/
JIRA_BASIC_AUTH_API_TOKEN for "basic", or JIRA_OAUTH_BEARER_TOKEN for
"oauth_bearer" -- basic auth, a plain Jira Cloud API token, is the far
simpler one to set up for a first real test; see this directory's
README), plus everything `live_run.py` itself needs (GitHub App /
Bedrock config) since this script runs the exact same real orchestrator
pipeline once it finds a story.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from typing import Any

from _run_lib import DATA_DIR, REPO_ROOT, Environment, RunOutcome, load_dotenv, require_env

_PROCESSED_PATH = DATA_DIR / "jira_processed.json"
_PENDING_PATH = DATA_DIR / "jira_pending_decisions.json"
_LOCK_PATH = DATA_DIR / "jira_poll.lock"

# A human's reply comment must be exactly one of these words (trailing
# punctuation/whitespace/case ignored) -- deliberately narrow, matching
# this codebase's own "explicit, narrow signal" discipline for opt-in
# (Sec. 4.4), not a free-text NLP guess at intent.
_POSITIVE_WORDS = {"approve", "approved", "yes", "y", "continue", "lgtm", "go", "proceed"}
_NEGATIVE_WORDS = {"reject", "rejected", "no", "n", "stop", "abandon", "cancel"}


def _load_processed() -> set[str]:
    if not _PROCESSED_PATH.exists():
        return set()
    return set(json.loads(_PROCESSED_PATH.read_text()))


def _mark_processed(issue_key: str) -> None:
    processed = _load_processed()
    processed.add(issue_key)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PROCESSED_PATH.write_text(json.dumps(sorted(processed), indent=2))


def _load_pending() -> dict[str, dict]:
    if not _PENDING_PATH.exists():
        return {}
    return json.loads(_PENDING_PATH.read_text())


def _save_pending(pending: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PENDING_PATH.write_text(json.dumps(pending, indent=2))


def _match_decision(comment_body: str, pause_kind: str) -> str | None:
    word = comment_body.strip().lower().rstrip(".!?")
    if word in _POSITIVE_WORDS:
        return "approve" if pause_kind == "gate" else "continue"
    if word in _NEGATIVE_WORDS:
        return "reject" if pause_kind == "gate" else "stop"
    return None


def _describe_pause(status: Any, env: Environment) -> str:
    """The real comment body posted to Jira when a run pauses -- what a
    human needs to know, and the exact word to reply with."""
    if status.pause_kind == "gate":
        if status.stage == "plan_approval_gate":
            artifact = env.plan_store.load_latest(status.run_id) or {}
            subtasks = [s["description"] for s in artifact.get("subtask_graph", {}).get("subtasks", [])]
            subtask_lines = "\n".join(f"- {s}" for s in subtasks)
            return (
                "SDLC Auto has a real plan ready for your review.\n\n"
                f"Outcomes: {artifact.get('outcomes')}\n"
                f"Story size: {artifact.get('risk', {}).get('story_size')}\n"
                f"Subtasks:\n{subtask_lines}\n\n"
                'Reply with a comment containing exactly "approve" to proceed, or "reject" to abandon this run.'
            )
        if status.stage == "change_review_gate":
            return (
                "SDLC Auto's real implementation passed verification and is awaiting your review before opening a pull request.\n\n"
                'Reply with a comment containing exactly "approve" to open the PR, or "reject" to abandon this run.'
            )
        return f"SDLC Auto is paused at an unexpected gate stage {status.stage!r}."
    if status.pause_kind == "checkpoint":
        elicitation = status.checkpoint
        return (
            f"SDLC Auto hit a real {elicitation.trigger} checkpoint: {elicitation.reason}\n\n"
            'Reply with a comment containing exactly "continue" to proceed, or "stop" to abandon this run.'
        )
    return f"SDLC Auto is paused for an unexpected reason ({status.pause_kind!r})."


def _make_on_pause(jira_client: Any, *, assignee_account_id: str | None):
    """Returns the real `on_pause(status, env)` callback `_run_lib`'s
    async resolvers call: posts a real comment, real-assigns the issue,
    and persists everything needed to resume this exact run once a
    human replies."""

    def _on_pause(status: Any, env: Environment) -> None:
        issue_key = env.real_jira_key
        description = _describe_pause(status, env)
        comment_result = jira_client.post_comment(issue_key=issue_key, body=description, comment_type="general")
        last_seen_comment_id = comment_result["data"]["comment_id"] if comment_result.get("outcome") == "ok" else None

        if assignee_account_id:
            assign_result = jira_client.assign_issue(issue_key=issue_key, account_id=assignee_account_id)
            if assign_result.get("outcome") != "ok":
                print(f"[jira_poll] Note: assigning {issue_key} did not succeed: {assign_result}")

        pending = _load_pending()
        pending[issue_key] = {
            "run_id": status.run_id,
            "task_description": env.task_description,
            "branch_name": env.branch_name,
            "worktree_path": str(env.worktree_path),
            "pause_kind": status.pause_kind,
            "stage": status.stage,
            "last_seen_comment_id": last_seen_comment_id,
        }
        _save_pending(pending)
        print(f"[jira_poll] {issue_key}: paused ({status.pause_kind}/{status.stage}) -- commented + assigned, waiting for a reply.")

    return _on_pause


def _build_jira_client():
    from issue_tracker.config import TenantJiraConfig
    from issue_tracker.jira_client import JiraClient

    auth_mode = require_env("JIRA_AUTH_MODE")
    opt_in_label = os.environ.get("JIRA_OPT_IN_LABEL", "ai-factory").strip() or None
    config = TenantJiraConfig(
        # Same tenant id this directory's live_run.py already uses --
        # no separate Jira-specific tenant id concept needed for this
        # single-tenant manual tooling.
        tenant_id=os.environ.get("LIVE_RUN_TENANT_ID", "live-run-tenant"),
        base_url=require_env("JIRA_BASE_URL"),
        auth_mode=auth_mode,
        oauth_bearer_token=(require_env("JIRA_OAUTH_BEARER_TOKEN") if auth_mode == "oauth_bearer" else None),
        basic_auth_email=(require_env("JIRA_BASIC_AUTH_EMAIL") if auth_mode == "basic" else None),
        basic_auth_api_token=(require_env("JIRA_BASIC_AUTH_API_TOKEN") if auth_mode == "basic" else None),
        project_key=require_env("JIRA_PROJECT_KEY"),
        opt_in_label=opt_in_label,
        trigger_status=require_env("JIRA_TRIGGER_STATUS"),
        approval_status=os.environ.get("JIRA_APPROVAL_STATUS", "In Progress"),
        change_review_status=os.environ.get("JIRA_CHANGE_REVIEW_STATUS", "In Review"),
        done_status=os.environ.get("JIRA_DONE_STATUS", "Done"),
    )
    return JiraClient(config=config), config


def _finish_issue(jira_client: Any, config: Any, issue_key: str, outcome: RunOutcome) -> None:
    """Real completion handling -- shared by the pickup path and the
    resume-after-decision path, since a run can reach a real terminal
    state (finished or abandoned) from either."""
    if outcome.exit_code == 0:
        comment = (
            f"SDLC Auto opened a real pull request for this story: {outcome.pr_url}"
            if outcome.pr_url
            else f"SDLC Auto ran this story to completion (final stage: {outcome.final_stage}), but no PR URL was recorded."
        )
        done = jira_client.transition_status(issue_key=issue_key, target_status=config.done_status, comment=comment)
        if done.get("outcome") not in ("ok", "empty"):
            print(f"[jira_poll] Note: transitioning {issue_key} to {config.done_status!r} did not succeed: {done}; posting the outcome as a plain comment instead.")
            jira_client.post_comment(issue_key=issue_key, body=comment, comment_type="general")
    else:
        comment = (
            f"SDLC Auto's run for this story was abandoned at stage {outcome.final_stage!r} "
            f"(exit code {outcome.exit_code}). Still needs a human look -- left in "
            f"{config.approval_status!r}, not moved to {config.done_status!r}."
        )
        post_result = jira_client.post_comment(issue_key=issue_key, body=comment, comment_type="general")
        if post_result.get("outcome") != "ok":
            print(f"[jira_poll] Note: posting the outcome comment back to {issue_key} did not succeed: {post_result}")


def _check_pending_decisions() -> None:
    """For every story already picked up and currently paused awaiting a
    human decision: look for a real new reply comment matching the
    narrow approve/reject/continue/stop vocabulary, and resume that
    exact run if one is found.

    Real bug this closes: an earlier version also required the new
    comment's author to differ from the bot's own account, meant to
    ignore the bot's own notification comment. That breaks for exactly
    the common single-user setup this tool is built for: your own API
    token *is* your own Jira account, so your reply and the bot's
    notification share one identity, and the filter silently excluded
    your real "approve" reply as if it were the bot's own noise.
    Comment-id ordering (only look past `last_seen_comment_id`, set to
    the id of the bot's own notification right after it's posted) is
    sufficient on its own: the *next* comment chronologically can only
    be a reply, and the narrow exact-word vocabulary (`_match_decision`
    requires the *entire* comment body to equal one word) means the
    bot's own multi-sentence notification could never accidentally
    match it anyway, even without an author check."""
    from _run_lib import resume_paused_run_async

    pending = _load_pending()
    if not pending:
        return

    jira_client, config = _build_jira_client()
    whoami = jira_client.whoami()
    bot_account_id = whoami["data"]["account_id"] if whoami.get("outcome") == "ok" else None
    on_pause = _make_on_pause(jira_client, assignee_account_id=bot_account_id)

    for issue_key, record in list(pending.items()):
        comments_result = jira_client.list_comments(issue_key=issue_key)
        if comments_result.get("outcome") != "ok":
            print(f"[jira_poll] Note: could not fetch comments for {issue_key}: {comments_result}")
            continue

        last_seen = int(record["last_seen_comment_id"]) if record.get("last_seen_comment_id") else -1
        new_comments = [c for c in comments_result["data"]["comments"] if int(c["comment_id"]) > last_seen]
        if not new_comments:
            continue

        decision: str | None = None
        for c in reversed(new_comments):  # most recent reply wins
            decision = _match_decision(c["body"], record["pause_kind"])
            if decision is not None:
                break
        if decision is None:
            print(f"[jira_poll] {issue_key}: new repl{'y' if len(new_comments) == 1 else 'ies'} found, but none matched approve/reject/continue/stop.")
            continue

        print(f"[jira_poll] {issue_key}: real decision {decision!r} found -- resuming run {record['run_id']!r}.")
        try:
            outcome = resume_paused_run_async(
                run_id=record["run_id"], real_jira_key=issue_key, task_description=record["task_description"],
                branch_name=record["branch_name"], worktree_path=record["worktree_path"],
                pause_kind=record["pause_kind"], stage=record["stage"],
                decision=decision, on_pause=on_pause,
            )
        except Exception as exc:
            # Real resilience fix: a real, transient infrastructure
            # failure (a Bedrock timeout that exhausts its own retries,
            # a GitHub outage, ...) must never crash this whole process
            # -- that would silently stop checking every OTHER pending
            # story too, not just this one. The pending record is left
            # untouched (this `except` runs before anything below that
            # would clear/advance it), so the next poll tick naturally
            # retries this exact same resume attempt from the run's
            # real, durable state -- see resume_paused_run_async's own
            # docstring for why it's now safe to retry without
            # double-applying the decision.
            print(f"[jira_poll] {issue_key}: resuming run {record['run_id']!r} failed with a real error ({exc!r}); will retry on the next poll.")
            continue

        if outcome.paused:
            continue  # on_pause already refreshed this issue's pending record for the new stage

        _finish_issue(jira_client, config, issue_key, outcome)
        pending = _load_pending()
        pending.pop(issue_key, None)
        _save_pending(pending)
        _mark_processed(issue_key)


def _poll_once() -> bool:
    """Returns True if a real run was newly started for one story --
    False if nothing new was found. Real opt-in gating (Sec. 4.4) is
    re-checked here regardless of what the search already filtered on."""
    from issue_tracker import gating

    from _run_lib import start_run_async

    jira_client, config = _build_jira_client()
    processed = _load_processed()
    pending = _load_pending()

    print(f"[jira_poll] Searching {config.project_key} for issues in status {config.trigger_status!r} ...")
    search_result = jira_client.find_stories_in_status(project_key=config.project_key, status=config.trigger_status)
    if search_result.get("outcome") != "ok":
        raise SystemExit(f"jira_poll_run.py: find_stories_in_status failed: {search_result}")

    candidates = [k for k in search_result["data"]["issue_keys"] if k not in processed and k not in pending]
    if not candidates:
        print("[jira_poll] No new candidate issues (none in the trigger status, or already processed/awaiting a decision).")
        return False

    for issue_key in candidates:
        issue_result = jira_client.get_issue(issue_key=issue_key)
        if issue_result.get("outcome") != "ok":
            print(f"[jira_poll] Skipping {issue_key}: get_issue returned {issue_result.get('outcome')!r}: {issue_result}")
            continue
        data = issue_result["data"]

        decision = gating.evaluate(
            tenant_id=config.tenant_id,
            issue_key=issue_key,
            status=data["status"],
            labels=data.get("labels", []),
            issue_type=data["issue_type"],
            config=config,
        )
        if isinstance(decision, gating.NotOptedIn):
            print(f"[jira_poll] {decision.reason}")
            continue

        # Real opt-in match -- this is the one story to run. Move it
        # through its real Jira lifecycle as work actually happens:
        # trigger_status ("Ready") -> approval_status ("In Progress")
        # the moment this picks it up, and, only on a genuine successful
        # completion, -> done_status ("Done"). An abandoned run stays in
        # approval_status, needing a human -- see `_finish_issue`.
        summary = data["summary"]
        description = data.get("description") or ""
        task_description = f"{summary}\n\n{description}".strip()
        print(f"[jira_poll] {issue_key} is opted in (matched {decision.matched_label or decision.matched_issue_type!r}).")

        pickup = jira_client.transition_status(
            issue_key=issue_key, target_status=config.approval_status,
            comment="SDLC Auto picked up this story and is starting a real run.",
        )
        if pickup.get("outcome") not in ("ok", "empty"):
            print(f"[jira_poll] Note: transitioning {issue_key} to {config.approval_status!r} did not succeed: {pickup}")
        print(f"[jira_poll] Starting a real run for {issue_key} ...")

        whoami = jira_client.whoami()
        bot_account_id = whoami["data"]["account_id"] if whoami.get("outcome") == "ok" else None
        on_pause = _make_on_pause(jira_client, assignee_account_id=bot_account_id)

        try:
            outcome = start_run_async(task_description=task_description, real_jira_key=issue_key, on_pause=on_pause)
        except Exception as exc:
            # Real resilience fix, same reasoning as _check_pending_
            # decisions' own try/except: a real transient failure (e.g.
            # a Bedrock timeout exhausting its own retries) before the
            # run ever reached its first pause must not crash this
            # whole process, and must not leave the story silently
            # stuck with no explanation -- it already left
            # JIRA_TRIGGER_STATUS, so a later poll's search will never
            # find it again on its own.
            print(f"[jira_poll] {issue_key}: starting a real run failed with a real error ({exc!r}).")
            jira_client.post_comment(
                issue_key=issue_key,
                body=(
                    f"SDLC Auto hit a real error starting this run and could not continue: {exc}. "
                    f"Move this story back to {config.trigger_status!r} (with the {config.opt_in_label!r} "
                    "label still applied) to retry from scratch."
                ),
                comment_type="general",
            )
            return True

        if outcome.paused:
            print(f"[jira_poll] {issue_key}: run paused, waiting for your reply comment (see the issue).")
        else:
            _finish_issue(jira_client, config, issue_key, outcome)
            _mark_processed(issue_key)

        return True  # one story per invocation, matching live_run.py's single-task shape

    return False


def _tick() -> None:
    _check_pending_decisions()
    _poll_once()


def _acquire_lock_or_exit() -> None:
    """Real bug this closes: two `jira_poll_run.py` invocations (a
    `--watch` loop already running, plus a one-shot second invocation
    started in another terminal -- exactly what happened on a real
    live run) raced to resume the same paused run, and one of them hit
    a real `OrchestratorError` from `core.py`'s drive loop (see
    `core.py`'s own comment on this -- a defensive fix landed there
    too, but preventing the race outright, not just surviving it, is
    the real fix for what this single-operator manual tool actually
    needs: no more than one instance running against this same `data/`
    directory at a time. A plain `flock` on a lock file is sufficient
    -- this is a local, single-machine tool, not a distributed system;
    released automatically on process exit (including a crash), so a
    stale lock from a killed process never wedges future runs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(
            "jira_poll_run.py: another instance is already running (lock held on "
            f"{_LOCK_PATH}) -- refusing to start a second one against the same run data."
        )
    # Deliberately never closed/released explicitly: held for this
    # process's entire lifetime, released automatically (by the OS) on
    # exit of any kind.


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    _acquire_lock_or_exit()

    watch_seconds: float | None = None
    args = sys.argv[1:]
    if args and args[0] == "--watch":
        if len(args) < 2:
            raise SystemExit("usage: jira_poll_run.py [--watch <seconds>]")
        watch_seconds = float(args[1])

    if watch_seconds is None:
        _tick()
        return 0

    print(f"[jira_poll] Watching every {watch_seconds:.0f}s (Ctrl-C to stop) ...")
    try:
        while True:
            _tick()
            time.sleep(watch_seconds)
    except KeyboardInterrupt:
        print("\n[jira_poll] Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
