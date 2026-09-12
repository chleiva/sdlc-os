# job-dispatcher — D1: Job Dispatcher, Tenant Resolution, Webhook Auth

Wave 1 deliverable. The single entry point for every external trigger
(a Jira status transition or equivalent): verifies the per-tenant HMAC
signature and replay window *before* anything else runs, resolves the
triggering tenant, creates the Run in the Registry, and requests
capacity from that tenant's own compute cell.

The HMAC/timestamp gate uses a nominal-type pattern — there is no
constructor path that reaches tenant resolution without the check
having already passed, proven with a call-counting spy, not just a
"returns 401" test.

**Known limitation, documented rather than hidden**: same-tenant request
coalescing is in-memory-only in this pass — correct for one process, not
yet safe for a real multi-replica deployment (needs a shared store, e.g.
Redis, or routed through the Registry). See `capacity.py`'s docstring.

## Install & run tests

```bash
cd services/job-dispatcher
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ../run-registry
.venv/bin/pip install -e ../issue-tracker
.venv/bin/pip install -e ".[dev]"
# re-assert editable installs (pip's own resolution step above can
# silently reinstall these two path deps non-editably as a side effect):
.venv/bin/pip install -e ../run-registry --force-reinstall --no-deps
.venv/bin/pip install -e ../issue-tracker --force-reinstall --no-deps

.venv/bin/python -m pytest -v
```

21 tests. Capacity provisioning and Jira are both real client code
against local mocks (a `MockCapacityProvider` standing in for a real
Karpenter/Kubernetes integration D6 plugs in later; `issue-tracker`'s own
mock Jira server for the delay-comment path) — no external accounts
needed.

## Run it

```bash
.venv/bin/python -m job_dispatcher --config config/tenants.json --db /tmp/registry.db --port 8809
```

(copy `config/tenants.example.json` to `config/tenants.json` first).
