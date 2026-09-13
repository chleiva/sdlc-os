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

Usage:
    cd deploy/run-worker
    python3 jira_poll_run.py            # poll once, run at most one new story, exit
    python3 jira_poll_run.py --watch 60 # poll every 60s, run each new story as found, until Ctrl-C

Reads configuration from the repo root's `.env` (see `.env.example`'s
JIRA_* block): JIRA_BASE_URL, JIRA_PROJECT_KEY, JIRA_TRIGGER_STATUS,
JIRA_OPT_IN_LABEL, JIRA_AUTH_MODE (+ the matching credential pair --
JIRA_BASIC_AUTH_EMAIL/JIRA_BASIC_AUTH_API_TOKEN for "basic", or
JIRA_OAUTH_BEARER_TOKEN for "oauth_bearer" -- basic auth, a plain Jira
Cloud API token, is the far simpler one to set up for a first real
test; see this directory's README), plus everything `live_run.py`
itself needs (GitHub App / Bedrock config) since this script runs the
exact same real orchestrator pipeline once it finds a story.
"""

from __future__ import annotations

import json
import os
import sys
import time

from _run_lib import DATA_DIR, REPO_ROOT, load_dotenv, require_env

_PROCESSED_PATH = DATA_DIR / "jira_processed.json"


def _load_processed() -> set[str]:
    if not _PROCESSED_PATH.exists():
        return set()
    return set(json.loads(_PROCESSED_PATH.read_text()))


def _mark_processed(issue_key: str) -> None:
    processed = _load_processed()
    processed.add(issue_key)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PROCESSED_PATH.write_text(json.dumps(sorted(processed), indent=2))


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


def _poll_once() -> bool:
    """Returns True if a real run was started (whether it finished
    cleanly or was abandoned at a gate/checkpoint) -- False if nothing
    new was found."""
    from issue_tracker import gating

    from _run_lib import run_once

    jira_client, config = _build_jira_client()
    processed = _load_processed()

    print(f"[jira_poll] Searching {config.project_key} for issues in status {config.trigger_status!r} ...")
    search_result = jira_client.find_stories_in_status(project_key=config.project_key, status=config.trigger_status)
    if search_result.get("outcome") != "ok":
        raise SystemExit(f"jira_poll_run.py: find_stories_in_status failed: {search_result}")

    candidates = [k for k in search_result["data"]["issue_keys"] if k not in processed]
    if not candidates:
        print("[jira_poll] No new candidate issues (either none in the trigger status, or all already processed).")
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
        # through its real Jira lifecycle as work actually happens
        # (New): trigger_status ("Ready", "Selected for Development",
        # whatever the team calls "pick me") -> approval_status ("In
        # Progress" -- a human/board picks the exact name via
        # JIRA_APPROVAL_STATUS) the moment this picks it up, and, only
        # on a genuine successful completion, -> done_status ("Done",
        # JIRA_DONE_STATUS). An abandoned run (a gate rejected, or a
        # checkpoint wasn't cleared) deliberately does NOT move to
        # done_status -- it stays in approval_status, needing a human,
        # with a comment explaining why.
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

        outcome = run_once(task_description=task_description, real_jira_key=issue_key)
        _mark_processed(issue_key)

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
            comment = f"SDLC Auto started a run for this story but it was abandoned at stage {outcome.final_stage!r} (exit code {outcome.exit_code}). Still needs a human look -- left in {config.approval_status!r}, not moved to {config.done_status!r}."
            post_result = jira_client.post_comment(issue_key=issue_key, body=comment, comment_type="general")
            if post_result.get("outcome") != "ok":
                print(f"[jira_poll] Note: posting the outcome comment back to {issue_key} did not succeed: {post_result}")

        return True  # one story per invocation, matching live_run.py's single-task shape

    return False


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    watch_seconds: float | None = None
    args = sys.argv[1:]
    if args and args[0] == "--watch":
        if len(args) < 2:
            raise SystemExit("usage: jira_poll_run.py [--watch <seconds>]")
        watch_seconds = float(args[1])

    if watch_seconds is None:
        _poll_once()
        return 0

    print(f"[jira_poll] Watching every {watch_seconds:.0f}s (Ctrl-C to stop) ...")
    try:
        while True:
            _poll_once()
            time.sleep(watch_seconds)
    except KeyboardInterrupt:
        print("\n[jira_poll] Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
