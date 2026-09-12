# prod-aws

Production AWS composition, same module tree as `pilot-aws-g7e` but sized
per the scaling ladder (spec §13.3, §14.9): the full G7e instance-type
candidate set instead of a single `g7e.2xlarge`, multi-AZ node pool
sizing, and `endpoint_public_access = false` behind a bastion/VPN (left
as a TODO — see `main.tf`).

**Status: skeleton, not yet apply-tested.** This deliverable's build
budget went into fully validating `pilot-aws-g7e` end-to-end (module
contract, `tofu validate`, the bootstrap sequence). `prod-aws` reuses the
identical `aws` module set — nothing here is a stub — but the environment
composition itself has not been run through `tofu plan`/`apply` the way
`pilot-aws-g7e` was. Treat it as "the same real modules, scaled up," not
as a second independently-verified environment.

## What's different from pilot-aws-g7e

- `instance_types` includes the full ladder
  (`g7e.2xlarge`, `g7e.12xlarge`, `g7e.24xlarge`, `g7e.48xlarge`) so
  Karpenter's capacity-optimized allocation (spec §14.8) has the full
  candidate set to diversify across, not a single size.
- `az_count = 3` and a wider `min_size`/`max_size` range on the GPU pool.
- `mesh_enabled = true` and `endpoint_public_access` should be set
  `false` with a bastion/VPN path added before real use (not built here —
  an operational choice, see `pilot-aws-g7e/README.md` point 3 for the
  same note there).
