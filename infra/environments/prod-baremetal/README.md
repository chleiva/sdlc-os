# prod-baremetal — not built out in this pass

Same status as `prod-azure` — see that directory's README.md for why:
this deliverable scoped a full build to `pilot-aws-g7e` plus the `aws`
variant of each cloud-specific module, and `prod-gcp` was built
separately as a concrete demonstration of the module-swap acceptance
criterion. This directory is intentionally empty of `.tf` files; it
exists so `infra/environments/` has the shape spec §14.3 requires.

Bare metal is also the variant most likely to need genuine
per-deployment customization even once built (see
`infra/modules/network/baremetal/README.md` and
`infra/modules/gpu-node-pool/baremetal/README.md`) — there is no single
"prod-baremetal" shape the way there is for a named cloud provider,
since it depends on whatever rack/fabric the organization already owns.
