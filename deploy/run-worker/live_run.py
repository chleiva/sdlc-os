#!/usr/bin/env python3
"""The real run worker: the piece that was missing entirely (CLAUDE.md's
"Known cross-deliverable gaps": "orchestrator has no real process
entrypoint"). This script is a manually-triggered live run driven by a
task description typed on the command line -- see `jira_poll_run.py`
for the Jira-triggered sibling (a real Jira Cloud story instead of a
CLI argument as the task source). Both share the exact same real
orchestrator wiring -- see `_run_lib.py`.

Real, for real, no shortcuts:
  - A real GitHub App JWT + installation-token exchange
    (`source_control.github_client.GitHubAppClient`), a real mirror
    clone/fetch, a real `git worktree`-isolated branch
    (`SourceControlService.create_branch_worktree`), a real `git push`,
    and a real PR (`SourceControlService.open_pr`).
  - A real Bedrock/MiniMax M2.5 call for planning
    (`BedrockAgentBackend`, via `BedrockToolUseAgentBackend`'s composed
    instance) and a real, multi-turn, tool-using implementation loop
    (`BedrockToolUseAgentBackend`) that actually writes files into the
    real worktree above.
  - A real `RealVerificationRunner` (existing_test_suite +
    static_analysis layers, for real, against the real worktree).
  - A real terminal-based human gate at both Section 9 gates (plan
    approval, change review) -- this is the one deliberately-scoped
    simplification: `GatesService`'s real approver-resolution needs a
    real Jira issue to resolve an approver from (`resolve_default_
    approver`), which this script doesn't have (see `jira_poll_run.py`
    for the sibling that does).

Usage:
    cd deploy/run-worker
    python3 live_run.py "Add a hello.py module with a greet(name) function
    that returns f'Hello, {name}!', and a matching test in test_hello.py"

Reads configuration from the repo root's `.env` (see `.env.example`):
GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY_PATH,
AWS_REGION, BEDROCK_MODEL_ID, plus LIVE_RUN_REPOSITORY /
LIVE_RUN_TENANT_ID (new, this script's own, documented in this
directory's README -- not part of the Docker Compose `.env.example`
surface, since this script is a manual live-run tool, not a compose
service).
"""

from __future__ import annotations

import sys

from _run_lib import run_once


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit('usage: live_run.py "<task description>"')
    task_description = sys.argv[1]
    return run_once(task_description=task_description).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
