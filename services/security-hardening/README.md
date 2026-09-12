# D10 — Security / NHI Hardening Pass

An audit-and-harden pass across Wave 1 (D1-D8) plus D9, which landed
partway through this pass — see the final agent report for the full
acceptance-criteria checklist, every file patched outside this
directory (and why), what's mocked vs. real, and spec ambiguities
flagged for human review.

This directory holds no product logic — it is real, adversarial pytest
tests that import and drive each target service's actual code as a
real, editable local dependency (never a mock of the deliverable under
test; the *external* boundary each of those services already mocks —
GitHub, Jira, a live model endpoint — stays mocked exactly as that
service's own test suite already does).

## Setup

```bash
cd services/security-hardening
python3 -m venv .venv
.venv/bin/pip install --upgrade pip

# Every target service, editable (order matters: run-registry and
# kms-boundary first, since several others depend on them):
for pkg in run-registry kms-boundary issue-tracker job-dispatcher orchestrator \
           index-server source-control tenant-cell fleet-dashboard \
           verification-pipeline gates; do
  .venv/bin/pip install -e ../$pkg
done

.venv/bin/pip install -e ".[dev]"

# Re-assert editable installs for the local-path deps whose own
# pyproject.toml also lists other siblings as dependencies (pip
# otherwise silently reinstalls them as non-editable copies as a side
# effect of the previous step -- same three-step dance fleet-dashboard's
# own README documents for run-registry):
for pkg in run-registry kms-boundary issue-tracker job-dispatcher orchestrator \
           index-server source-control tenant-cell fleet-dashboard \
           verification-pipeline gates; do
  .venv/bin/pip install -e ../$pkg --force-reinstall --no-deps
done
```

## Running the tests

```bash
cd services/security-hardening
.venv/bin/python -m pytest tests/ -v
```

All 100+ tests pass against a clean install at the time this was
written. Nothing here needs network access or a live cloud/SaaS
account — every external boundary is the same real, local mock each
target service's own suite already uses (D4's mock Jira, D5's mock
GitHub, D3's on-disk fixture repos, F2's real SQLite-backed
`RegistryService`).

## What's here

One test file per deliverable/cross-cutting check named in the D10
brief (`docs/deliverables/wave2-D10-security-hardening.md`):

- `test_d1_webhook_forgery.py`
- `test_d2_hook_chain_and_secrets.py`
- `test_d3_index_tenant_isolation.py`
- `test_d5_source_control_hardening.py`
- `test_d6_kms_and_node_isolation.py`
- `test_d9_gates_self_approval_bypass.py`
- `test_f2_d8_registry_fail_closed.py`
- `test_cross_cutting_prompt_injection.py`
- `test_cross_cutting_nhi_inventory.py`
- `test_cross_cutting_credential_revocation.py`
- `test_cross_cutting_dependency_policy_blocking.py`

See the final agent report (delivered at the end of this deliverable's
run, not a file in this directory) for the acceptance-criteria mapping,
every surgical fix landed in another deliverable's own file because a
test here found a real gap, and honest documentation of the findings
that were deliberately NOT patched (with the reasoning for each).
