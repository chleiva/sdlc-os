# `fake_services/` — fixtures for `test_contract_ci.py`

Two minimal fake "services" (`svc_a` passes, `svc_b`'s one test
deliberately fails) used to prove `contract_ci.py` discovers a service
by its `tests/` directory + `.venv`, shells out to that service's own
`pytest`, and reports pass/fail/not-runnable honestly — without needing
any of the repo's own real (much heavier) services for that specific
proof. (`test_contract_ci.py` separately also runs the CI orchestrator
against the real `services/mcp-stubs` suite, for an end-to-end proof
against real Wave-0 contract tests.)

Each fake service's `.venv` is a symlink to this package's own
`services/platform-release/.venv` (all either one needs is `pytest`
itself) rather than a separately-provisioned virtualenv — cheaper to set
up, and it exercises the exact same `.venv/bin/python -m pytest` code
path `contract_ci.py` uses against a real service. Like every other
`.venv` in this repo, it's gitignored and only appears after `pip
install -e ".[dev]"` is run in `services/platform-release/` — until
then, `discover_services` correctly reports these two as
`not_runnable`, not as a false pass (see
`test_service_with_no_venv_is_not_runnable_not_silently_passed`).

If this symlink is ever missing (e.g. a fresh checkout before the
package's own `.venv` is created), recreate it with:

```
cd services/platform-release
ln -s ../../../../.venv tests/fixtures/fake_services/svc_a/.venv
ln -s ../../../../.venv tests/fixtures/fake_services/svc_b/.venv
```

(the symlink target is relative to the link's own directory, not the
shell's cwd -- `svc_a/.venv -> ../../../../.venv` walks up
`svc_a -> fake_services -> fixtures -> tests -> platform-release` and
then into that directory's own `.venv`.)
