# `modules/platform-release`

The platform's own "what version am I running" resource — the thing a
D12 release/rollback actually applies against. See the repo-root
`services/platform-release/README.md` for the full "why `local` stands
in for a real cloud provider here" explanation; short version: no live
cloud account exists in this build environment (same constraint F1/D6
document), and `hashicorp/local`'s `local_file` resource is a real,
`tofu`-managed, stateful artifact — so a `tofu apply` here is a genuine
apply against real OpenTofu state, not a simulation, and rollback is a
genuine `tofu apply` of a prior version against that same state.

A real cloud deployment would swap this module's one resource for
whatever concretely represents "the platform's control-plane version" in
that environment — a Helm release version on
`infra/modules/orchestrator`'s Deployment, a container image tag, or an
entry in a fleet-wide version-pin ConfigMap — the versioning/rollback
*mechanics* (`tofu apply` against a prior pinned config/state, no manual
undo) are identical either way; only the resource type changes.

## Contract

- Inputs: `environment`, `platform_version`, `component_versions`,
  `release_id`, `rolled_back_from` (see `variables.tf`).
- Output: a JSON manifest (`local_file.deployed_version`) recording
  exactly those inputs, plus `is_rollback` derived from whether
  `rolled_back_from` is set.
- No side effects beyond that one file — this module deliberately does
  not fork on cloud provider, the way F1's `gpu-node-pool/{aws,gcp,...}`
  modules do, because there is nothing cloud-specific about "record the
  currently-applied version" once you're past the resource-type
  substitution described above.

## Validated

`tofu init` + `tofu validate` + `tofu fmt` clean against the real
`hashicorp/local` provider (network access was available in this build
environment for that one, low-risk, non-cloud provider — see the repo
root README's "how this was actually run" note). The composition that
wraps this module (`../../compositions/platform-deployment`) is also
`apply`-tested for real by
`services/platform-release/tests/test_rollback.py` — that test performs
two genuine `tofu apply` runs (forward release, then rollback) via
`subprocess`, in a throwaway copy of this tree, and asserts the on-disk
manifest reflects each.
