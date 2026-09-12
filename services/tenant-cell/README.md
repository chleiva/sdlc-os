# tenant-cell — D6: Model Serving + Tenant Compute Cell Provisioning

Wave 1 deliverable. Two halves, per the brief:

- **`infra/`** — OpenTofu composition that calls F1's `gpu-node-pool` and
  `model-serving` modules, parameterized per tenant. See
  `infra/modules/tenant-cell/README.md` and
  `infra/compositions/two-tenants/README.md`.
- **`src/tenant_cell/`** — the Python control-logic service: the
  interruption watcher, cold-start budget tracking, model-diversity
  enforcement, and model-version-bump governance. See `src/tenant_cell/__init__.py`
  for the module-by-module index.

## What's real vs. mocked, stated plainly

| Piece | Real | Mocked |
|---|---|---|
| The interruption-watcher state machine (`interruption_watcher.py`) | Yes, fully | n/a |
| The forced checkpoint write on interruption | Yes -- calls `run_registry.RegistryService.write_checkpoint` for real | n/a |
| Model-diversity enforcement (`model_diversity.py`) | Yes, fully -- no live dependency at all | n/a |
| Model-version-bump governance (`model_version_governance.py`) | Yes, fully -- no live dependency at all | n/a |
| Tenant-id-derived resource naming (`naming.py`) | Yes -- literally mirrored from, and drift-tested against, the real committed HCL | n/a |
| Cold-start timing/budget accounting (`cold_start.py`) | Yes, as *control-logic overhead measurement* | The actual node launch/boot/model-load it would be timing in production (`provisioning_client.py`) |
| Cordon / stop-accepting-inference / drain / flush-observability / launch-a-node / health-check | The *sequence*, *timing discipline*, and *call shape* | The Kubernetes API server and cloud SDK on the other end (`provisioning_client.FakeProvisioningClient`) |
| The OpenTofu composition's resource *naming*, *distinctness*, and *shape* | Yes -- real `tofu init`/`validate`/`fmt`/`plan` against real HCL | A real cloud account and a reachable Kubernetes cluster (see `infra/compositions/two-tenants/README.md`'s exact plan output and error boundary) |

## Running the tests

```
cd services/tenant-cell
python3 -m venv .venv
.venv/bin/pip install -e ../run-registry   # F2's real RegistryService -- a sibling in-repo package, not on any index
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

25 tests, all passing at the time this was built (no live cloud/cluster
needed -- everything is either a pure function or driven against
`FakeProvisioningClient`/`FakeClock`/a real temp-file-backed
`RegistryService`).

## Validating the IaC

```
cd services/tenant-cell/infra/modules/tenant-cell
tofu init -backend=false -input=false && tofu validate

cd ../../compositions/two-tenants
tofu init -input=false && tofu validate
tofu fmt -recursive -check -diff ..
tofu plan -input=false   # see this directory's own README.md for the exact output and the honest cluster-connectivity boundary
```

## What a human with a real cloud account and Kubernetes cluster must still do

There is no live cloud account or reachable Kubernetes cluster in this
environment (this deliverable's hard constraint, same as F1's). Before
any of this runs for real:

1. Point `infra/compositions/two-tenants/providers.tf` (or, for a real
   deployment, a proper `environments/*` composition following
   `environments/pilot-aws-g7e`'s own layout) at a real EKS/GKE/AKS
   cluster's real endpoint/CA/token, the same way
   `environments/pilot-aws-g7e/providers.tf` does -- remove the
   fake-credential AWS provider overrides and the unreachable
   kubernetes/helm hosts.
2. Supply real `cluster_name`/`network_id`/`gpu_subnet_ids`/
   `egress_allowlist_group_id` from that environment's own `network`
   module outputs, and real, checksum-pinned
   `primary_model_artifact_uri`/`primary_model_weight_checksum` (and
   `reviewer_model_*` for tenants hosting a second self-hosted model).
3. Swap `provisioning_client.FakeProvisioningClient` for a real
   implementation built on `kubernetes.client` (cordon/drain) plus the
   target cloud's SDK (node launch/health) -- `interruption_watcher.py`
   and `cold_start.py` depend only on the `ProvisioningClient` Protocol,
   so nothing else changes.
4. Wire `model_version_governance.promote_model_version`'s
   `EvaluationSuiteResult` to Section 20.1's real evaluation harness
   (D13) output once it exists, instead of a caller constructing one by
   hand.
5. Confirm the exact cold-start budget number: Section 16.6 says "a few
   minutes" without pinning a figure -- `cold_start.DEFAULT_COLD_START_BUDGET_SECONDS`
   is this implementation's own placeholder (5 minutes), flagged in that
   module's docstring for a human to confirm/override per Section 19's
   configuration model.
