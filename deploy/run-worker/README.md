# deploy/run-worker — the real live-run tool

This is the missing orchestrator process entrypoint (CLAUDE.md's "Known
cross-deliverable gaps"), built as a manual tool for a first real live
run rather than a Docker Compose service (job-dispatcher/Jira are
deliberately not wired to it yet — see `live_run.py`'s own docstring for
the staged plan this fits into).

## What this actually does, for real

1. Real GitHub App JWT + installation-token exchange, a real mirror
   clone/fetch, a real `git worktree`-isolated branch.
2. Real Bedrock/MiniMax M2.5 planning, and a real multi-turn tool-using
   implementation loop (`orchestrator.tool_use_bedrock_backend
   .BedrockToolUseAgentBackend`) that actually writes files into that
   worktree — computed `DiffOutput` comes from a real `git diff`, never
   the model's own self-report.
3. Real verification (`orchestrator.real_verification_runner
   .RealVerificationRunner`): real `pytest` + real `ruff`/`mypy` against
   the real worktree. Five of Section 11.1's seven layers are honestly
   reported as `skipped — not implemented in this pass` (see that
   module's own docstring for exactly which, and why).
4. A real terminal-based approval gate at both Section 9 human gates.
5. A real `git push` + a real PR via `SourceControlService.open_pr`.

## What's still a deliberate simplification, stated plainly

- **No real `GatesService` integration yet.** `GatesService.open_gate`
  needs a real Jira issue to resolve an approver from
  (`resolve_default_approver`) — this script's terminal prompt is a
  stand-in until Jira is wired (this repo's staged plan's next step).
- **Task description is carried in `jira_key`.** `core.py`'s
  `_run_context` has no dedicated free-text task-description field
  today — in the real system this would come from the Jira ticket body.
  This is a real, disclosed gap to close properly (give `Run`/`_run_context`
  an actual task-description field) once Jira triggers real runs instead
  of this manual tool.
- **`research_fn`/index-server are not wired.** The RESEARCH stage is a
  no-op in this pass — the model plans/implements from the task
  description and its own tool-driven exploration of the worktree
  (`list_files`/`read_file`) alone, not a real repo-wide index lookup.

## Install

This is a manual tool, not a packaged service — run it from a venv that
already has each of these four sibling packages' own dependencies
installed (their own READMEs document the exact sequence; do each in
turn, in this order, into ONE shared venv):

```bash
cd deploy/run-worker
python3 -m venv .venv
VENV="$(pwd)/.venv"
.venv/bin/pip install --upgrade pip

# Install each sibling from ITS OWN directory, not by relative path from
# here — pip resolves a package's own further-sibling `file:` dependencies
# (e.g. issue-tracker's real dependency on kms-boundary) relative to the
# invocation's cwd, not the target package's directory, so installing
# straight from deploy/run-worker/ (found the hard way, building this
# tool) silently looks in the wrong place. `cd` into each real service
# directory first, every time, matching every other multi-sibling
# service's own README in this repo.
(cd ../../services/kms-boundary && "$VENV/bin/pip" install -e .)
(cd ../../services/run-registry && "$VENV/bin/pip" install -e .)
(cd ../../services/issue-tracker && "$VENV/bin/pip" install -e .)
(cd ../../services/source-control && "$VENV/bin/pip" install -e .)
(cd ../../services/verification-pipeline && "$VENV/bin/pip" install -e .)
(cd ../../services/orchestrator && "$VENV/bin/pip" install -e .)
# re-assert editable installs (pip's own resolution can silently make
# these non-editable as a side effect of a later step — same dance
# every other multi-sibling service README in this repo documents):
(cd ../../services/kms-boundary && "$VENV/bin/pip" install -e . --force-reinstall --no-deps)
(cd ../../services/run-registry && "$VENV/bin/pip" install -e . --force-reinstall --no-deps)

# pytest itself: `verification_pipeline.layers.existing_tests` shells out
# to `sys.executable -m pytest` against the real worktree -- it's only in
# verification-pipeline's own [dev] extra (not a base dependency, since
# that package's own product code never needs it, only its own test
# suite does), so this venv needs it explicitly too. Found the hard way:
# without this, a missing-pytest failure silently reads as "0/0 tests
# passed" rather than erroring loudly -- always sanity-check with a
# throwaway real repo (see "Sanity-check before a real run" below)
# rather than assume this step alone is enough.
"$VENV/bin/pip" install "pytest>=8.0"
```

## Run it

Fill in the repo root's `.env` first (`GITHUB_APP_ID`,
`GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`,
`ORCHESTRATOR_INFERENCE_VENDOR=bedrock`, `BEDROCK_MODEL_ID`,
`AWS_REGION`, and either `AWS_BEARER_TOKEN_BEDROCK` or IAM credentials —
see the repo root's `.env.example`), then:

```bash
.venv/bin/python live_run.py "Add a hello.py module with a greet(name) \
function that returns f'Hello, {name}!', and a matching test in test_hello.py"
```

Optional env vars this tool alone reads (not part of the Docker Compose
`.env.example` surface): `LIVE_RUN_REPOSITORY` (default
`chleiva/returnby`), `LIVE_RUN_TENANT_ID` (default `live-run-tenant`).

Real state (Registry DB, plan/progress stores, git mirror clones) is
kept under `data/` and `mirrors/` in this directory — gitignored, safe
to delete between runs if you want a clean slate.
