# network/baremetal — Phase 1.5 stub

Not a working bare-metal network implementation — satisfies the module
contract (same `variables.tf` / `outputs.tf` as `network/aws`) only, so
`infra/environments/prod-baremetal` has a valid module path. `main.tf`
declares no provider and creates no resources.

## What the real implementation needs (Phase 1.5 portability proof, §14.10, §21)

Bare metal has no cloud VPC/subnet primitive, so this variant's shape
will necessarily differ more from the cloud variants internally even
though its variable/output *contract* must not:

- VLAN or L3 segmentation for the public/private/gpu tiers (exact
  mechanism depends on the target rack's switch fabric — out of scope to
  guess at here).
- `egress_allowlist_group_id` maps to whatever local firewall mechanism
  is in play (nftables/iptables ruleset id, or a vendor switch ACL id) —
  the *output name and shape* (a single opaque id string) still has to
  match the cloud variants so the module stays swappable.
- A private mirror or cache of the model artifact store reachable from
  the gpu tier without a public egress path, analogous to the AWS
  module's S3 gateway endpoint.

This is the variant most likely to need genuine per-deployment
customization even after Phase 1.5 — document that expectation rather
than pretending one bare-metal implementation covers every rack.
