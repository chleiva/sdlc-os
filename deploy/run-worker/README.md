# deploy/run-worker — the real live-run tools

This is the missing orchestrator process entrypoint (CLAUDE.md's "Known
cross-deliverable gaps"), built as a pair of manual tools rather than a
Docker Compose service:

- **`live_run.py`** — the task description is a CLI argument you type.
- **`jira_poll_run.py`** — the task description is a real story pulled
  from a real Jira Cloud site (polling, not a push webhook — see its
  own docstring for exactly why, and what the real production path
  looks like instead).

Both share the exact same real orchestrator wiring, factored into
`_run_lib.py`'s `run_once()` so the two never silently drift apart.

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
6. (`jira_poll_run.py` only) A real `JiraClient.find_stories_in_status`
   JQL search, a real `gating.evaluate` opt-in check (never "in the
   trigger status" alone — master spec Sec. 4.4), and a real
   `post_comment` back to the Jira issue reporting the outcome (PR URL,
   or why the run was abandoned).

## What's still a deliberate simplification, stated plainly

- **No real `GatesService` integration yet.** `GatesService.open_gate`
  needs a real Jira issue to resolve an approver from
  (`resolve_default_approver`) — both scripts' terminal prompt is a
  stand-in, even `jira_poll_run.py` (which has a real issue key
  available but doesn't yet wire it through to `GatesService`).
- **Task description is carried in `jira_key`.** `core.py`'s
  `_run_context` has no dedicated free-text task-description field
  today. `jira_poll_run.py` at least keeps the *real* issue key
  inspectable (`run.jira_key` reads `"PROJ-123: <task text>"`, not pure
  prose) — `_run_lib.run_once`'s own docstring explains why. Giving
  `Run`/`_run_context` an actual dedicated task-description field is
  still a real, disclosed gap, not fixed by this.
- **Polling, not a push webhook.** See `jira_poll_run.py`'s own
  docstring for the full reasoning: the real production path (Jira
  Automation → signed webhook relay → `job-dispatcher` →
  orchestrator) needs a real OAuth app, a publicly-reachable relay, and
  `job-dispatcher` actually calling the orchestrator (it doesn't,
  anywhere in this codebase, today) — polling proves the same "real
  story in, real PR out" loop without first standing up all of that.
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

## Run it — `live_run.py` (task typed on the command line)

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
`chleiva/returnby`), `LIVE_RUN_TENANT_ID` (default `live-run-tenant`),
`BEDROCK_MAX_TURNS` (overrides `BedrockToolUseAgentBackend`'s per-subtask
turn cap, which otherwise derives from the plan's own story size — see
that class's docstring).

## Run it — `jira_poll_run.py` (task pulled from a real Jira story)

Everything `live_run.py` needs above, plus real Jira Cloud access. The
**simplest real setup** (basic auth — an API token, not a full OAuth
app registration):

1. In your Jira Cloud site: **Project settings → Workflow**, confirm a
   status meaning "queued and ready to start" exists (Jira's own
   default, `Selected for Development`, is fine — don't invent a new
   one). Note the exact project key (e.g. `PROJ`).
2. Apply a label to the story you want picked up — default
   `ai-factory` (Sec. 4.4: never "in the trigger status" alone is
   sufficient; a real opt-in signal is always required too — see
   `gating.py`).
3. Generate a real API token: https://id.atlassian.com/manage-profile/security/api-tokens
4. Fill in the repo root's `.env`'s `JIRA_*` block:
   ```
   JIRA_BASE_URL=https://your-site.atlassian.net
   JIRA_PROJECT_KEY=PROJ
   JIRA_TRIGGER_STATUS=Selected for Development
   JIRA_OPT_IN_LABEL=ai-factory
   JIRA_AUTH_MODE=basic
   JIRA_BASIC_AUTH_EMAIL=you@example.com
   JIRA_BASIC_AUTH_API_TOKEN=<the token from step 3>
   ```
5. Create a story in that project, in that status, with that label.
6. ```bash
   .venv/bin/python jira_poll_run.py            # poll once, run at most one new story, exit
   .venv/bin/python jira_poll_run.py --watch 60 # poll every 60s until Ctrl-C
   ```

Already-processed issue keys are tracked in `data/jira_processed.json`
(gitignored) so re-polling never re-triggers the same story — delete
that file (or the whole `data/` directory) for a clean slate.

(`JIRA_OAUTH_BEARER_TOKEN` + `JIRA_AUTH_MODE=oauth_bearer` is also
supported, matching Sec. 17.1's preferred non-human-identity model, but
needs a real OAuth 2.0 (3LO) app registration first — see
`services/issue-tracker/SETUP.md` step 1. Basic auth is the pragmatic
default for a first real test.)

Real state (Registry DB, plan/progress stores, git mirror clones) is
kept under `data/` and `mirrors/` in this directory — gitignored, safe
to delete between runs if you want a clean slate.
