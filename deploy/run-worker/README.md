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
`_run_lib.py` so the two never silently drift apart.

## What this actually does, for real

1. Real GitHub App JWT + installation-token exchange, a real mirror
   clone/fetch, a real `git worktree`-isolated branch.
2. Real Bedrock/MiniMax M2.5 planning, and a real multi-turn tool-using
   implementation loop (`orchestrator.tool_use_bedrock_backend
   .BedrockToolUseAgentBackend`) that actually writes files into that
   worktree, with five real tools: `read_file`/`list_files` to explore,
   `write_file` for a new file or a genuine full rewrite, `edit_file`
   (str_replace-style — preferred for any change to a file that already
   exists, so a small fix doesn't regenerate the whole file) for a
   targeted change, and `finish` to end the loop. Computed `DiffOutput`
   comes from a real `git diff`, never the model's own self-report.
3. Real verification (`orchestrator.real_verification_runner
   .RealVerificationRunner`): real `pytest` + real `ruff`/`mypy` against
   the real worktree. Five of Section 11.1's seven layers are honestly
   reported as `skipped — not implemented in this pass` (see that
   module's own docstring for exactly which, and why).
4. A real approval gate at both Section 9 human gates -- `live_run.py`'s
   is a terminal prompt (a human is right there); `jira_poll_run.py`'s
   is asynchronous: a real Jira comment + real assignment, never a
   blocking prompt (see "Async human-in-the-loop" below).
5. A real `git push` + a real PR via `SourceControlService.open_pr`.
6. (`jira_poll_run.py` only) A real `JiraClient.find_stories_in_status`
   JQL search, a real `gating.evaluate` opt-in check (never "in the
   trigger status" alone — master spec Sec. 4.4).

## Async human-in-the-loop (`jira_poll_run.py` only)

An automatically-triggered run cannot block on a terminal prompt --
nobody is watching one. So every gate/checkpoint pause instead — this
now includes a subtask that keeps failing identically (not just a
verification failure): `core.py`'s `_handle_implementation_failure`
counts a repeatedly-failing `implement_subtask` call toward the same
Section 9.3 "stuck" checkpoint a repeated verification failure already
uses, so it surfaces here, as a real checkpoint, once the retry budget
is exhausted -- rather than only ever being caught by `jira_poll_run
.py`'s own generic "a real transient infrastructure failure must never
crash this whole process" handler around `resume_paused_run_async`
(which still applies below that budget, and to genuinely transient
failures like a real GitHub outage):

1. Posts a real Jira comment describing exactly what's needed (the
   plan summary, or the checkpoint reason) and the exact word to reply
   with.
2. Real-assigns the issue to the account behind your configured Jira
   token (`JiraClient.assign_issue`).
3. Persists everything needed to resume this exact run later
   (`data/jira_pending_decisions.json`) and exits -- nothing is
   blocked.

Reply with a plain comment containing exactly one word: **`approve`**
or **`reject`** for a plan/change-review gate; **`continue`** or
**`stop`** for a Section 9.3 checkpoint (case-insensitive; a few
synonyms like `yes`/`lgtm`/`no` also work -- see `jira_poll_run
._POSITIVE_WORDS`/`_NEGATIVE_WORDS`). The next `jira_poll_run.py`
invocation (or the next `--watch` tick) checks every pending story for
a new reply comment (by comment-id ordering, not by author -- a real
bug found and fixed: your own API token *is* your own Jira account in
a single-user setup, so filtering out "the bot's own author" also
filtered out your real replies), and resumes that exact run with your
decision applied via `orchestrator.approve_plan`/`approve_change_
review`/`resolve_checkpoint` -- which may pause again (another comment,
another wait) or finish for real.

## Autonomy levels (Section 12) -- fewer routine questions, still a real safety net

`services/gates.autonomy`'s real L0-L3 levels are now consulted before
every gate pause (not before a checkpoint -- Sec. 12: checkpoints are
never traded away by any autonomy level, they're the actual "break the
glass" mechanism). `jira_poll_run.py` defaults to **L3**: neither the
plan-approval nor the change-review gate stops for your input at all --
only a genuine Section 9.3 checkpoint (a size/risk/stuck/time-cost
anomaly) still does. `live_run.py` defaults to **L1** (ask at every
gate, unchanged) since a human is already there typing the command.
Override either with `AUTONOMY_LEVEL=L0|L1|L2|L3` in `.env` or the
shell environment.

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
- **Jira comments only, no Slack yet.** The async human-in-the-loop
  mechanism above is deliberately Jira-only for now — a real Slack
  notification (faster to notice than a Jira comment) would be a
  genuinely separate integration (a Slack app, Events API, interactive
  buttons), layered on top of this same mechanism as a second
  notification channel, not built here.
- **A run started before this async mechanism existed stays stuck.**
  If you have an old, abandoned run sitting mid-way (an issue moved to
  `JIRA_APPROVAL_STATUS` with no further comment, and no entry in
  `data/jira_pending_decisions.json`) — it predates this fix and isn't
  picked up retroactively; its worktree/branch are still on disk under
  `mirrors/` if you want to finish it by hand, or just leave it and
  move the Jira issue back to `JIRA_TRIGGER_STATUS` to start fresh.
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
that class's docstring), `BEDROCK_FALLBACK_REGIONS` (comma-separated
real AWS regions to fail over to if `AWS_REGION` exhausts its own
retry budget — a real, persistent regional Bedrock timeout is
recoverable this way rather than just giving up; the fallback
region *sticks* for the rest of the run once it succeeds once, so a
multi-turn implementation loop doesn't keep re-paying the dead
region's full timeout on every turn — see `bedrock_backend
.call_converse_with_retry`'s own docstring. Confirm your chosen
fallback regions actually serve the same model first, e.g. `aws
bedrock get-foundation-model --model-identifier <id> --region
<region>`), and `BEDROCK_FALLBACK_MODELS` (comma-separated real Bedrock
model ids — **your own deliberate low-cost picks only, never the
expensive primary model**, ordered best-quality-first among that
low-cost set — to fall over to once `BEDROCK_MODEL_ID` has exhausted
every region above; a real live-run finding this closes: a persistently
degraded *model*, not just a region, still stalled every real call for
minutes at a time even with region fallback alone. Same sticky-once-
recovered behavior as region fallback — see `bedrock_backend
.call_converse_with_retry`'s own docstring).

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
4. Fill in the repo root's `.env`'s `JIRA_*` block — all four status
   names are your team's own real workflow, never invented by this
   System (Sec. 4.4/19); rename any of them to match:
   ```
   JIRA_BASE_URL=https://your-site.atlassian.net
   JIRA_PROJECT_KEY=PROJ
   JIRA_TRIGGER_STATUS=Selected for Development   # "pick me" -- e.g. your "Ready"
   JIRA_OPT_IN_LABEL=ai-factory
   JIRA_APPROVAL_STATUS=In Progress               # where it moves once picked up
   JIRA_DONE_STATUS=Done                          # where it moves on real success
   JIRA_AUTH_MODE=basic
   JIRA_BASIC_AUTH_EMAIL=you@example.com
   JIRA_BASIC_AUTH_API_TOKEN=<the token from step 3>
   ```
5. Create a story in that project, in that status, with that label.
6. ```bash
   .venv/bin/python jira_poll_run.py            # poll once, run at most one new story, exit
   .venv/bin/python jira_poll_run.py --watch 60 # poll every 60s until Ctrl-C
   ```

The story's real Jira status moves as work actually happens: found in
`JIRA_TRIGGER_STATUS` → real `transition_status` call to
`JIRA_APPROVAL_STATUS` the moment it's picked up (with a comment) →
real `transition_status` to `JIRA_DONE_STATUS` (with the PR link in a
comment) only on genuine successful completion. An abandoned run (a
gate rejected, or a checkpoint wasn't cleared) deliberately stays in
`JIRA_APPROVAL_STATUS` with an explanatory comment instead — it still
needs a human, not a silent "Done".

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
