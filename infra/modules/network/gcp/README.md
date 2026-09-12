# network/gcp — Phase 1.5 stub

This is **not a working GCP network implementation**. It exists only to
satisfy the module contract (identical `variables.tf` / `outputs.tf` to
`network/aws`) so that:

- `infra/environments/prod-gcp` can reference `../../modules/network/gcp`
  today without a broken module path, and
- the module-contract rule (spec §14.3, §14.10 — "a composition under
  `environments/` can swap `network/aws` for `network/gcp` without
  touching the orchestrator or model-serving modules above it") is
  satisfiable in code review now, ahead of the real build.

`main.tf` declares no provider and creates no resources. `tofu plan`
against an environment composing this module will show empty/placeholder
outputs, not a provisioned GCP VPC.

## What the real implementation needs (deliverable: Phase 1.5 portability proof, §14.10, §21)

- `google_compute_network` + `google_compute_subnetwork` per tier
  (public/private/gpu), matching `network/aws`'s three-tier layout.
- `google_compute_router` + `google_compute_router_nat` for private-subnet
  egress (Cloud NAT), analogous to the AWS NAT gateways.
- `google_compute_firewall` rules implementing the same default-deny +
  explicit-allowlist egress semantics as `network/aws`'s security group,
  keyed off `var.egress_allowlist`.
- A private Google Access / VPC Service Controls path for the model
  artifact store, analogous to the AWS module's S3 gateway endpoint.

Do not hand-write this by guessing at GCP quirks under deadline pressure
inside another deliverable — build and validate it against a real GCP
account as its own scoped piece of work, the same discipline this
deliverable applied to the AWS variant.
