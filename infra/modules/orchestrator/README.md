# orchestrator

F1's scope here is the **substrate**: namespace, RBAC, NetworkPolicy,
service-mesh wiring, and a Helm skeleton — not the agent runtime logic
itself (D2), not the MCP server implementations (F3/D3-D5), and not the
gates/autonomy logic (D9). See `wave1-D2-orchestrator-core.md` and
`wave0-F3-mcp-contracts.md` for those.

## Explicit gap: placeholder container image

`chart/values.yaml`'s `image` points at a generic echo-server image, not
a real orchestrator build — D2 does not exist yet as of this
deliverable. This exists so `bootstrap.sh`'s step 4 (Helm apply) and step
5 (smoke run) have a real, schedulable Deployment to apply and probe end
to end today; swapping in D2's real image is a values override at the
environment composition layer, not a change to this module's shape.

## Service mesh

`mesh_enabled = false` by default; `environments/pilot-aws-g7e` turns it
on. Linkerd is the reference mesh (simpler mTLS-by-default posture for a
single-cluster pilot than Istio); the `mesh_provider = "istio"` path in
`variables.tf` is declared for the contract but not implemented in
`main.tf` — only the linkerd branch installs anything. Flagged rather
than silently absent.
