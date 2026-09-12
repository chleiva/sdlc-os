# two-tenants — validation composition

**This is not a deployable environment.** It exists purely to run
`tofu init`/`tofu validate`/`tofu fmt`/`tofu plan` against two concurrent
instantiations of `../../modules/tenant-cell` (tenant `acme` on the
Phase-0-equivalent `g7e.2xlarge` tier with a frontier-API escalation
posture, tenant `globex` on the `g7e.12xlarge` tier with a second
self-hosted reviewer model) and prove, for real, that the two tenants'
compositions produce non-overlapping resource sets. See
`providers.tf`'s header comment for why its provider blocks use fake
credentials/an unreachable host, and why that's fine for what this
composition is for.

## Hard constraint this environment was built under

No live cloud account and no reachable Kubernetes cluster exist in this
environment (documented in D6's task brief; the same constraint F1's own
modules were built and validated under). Every command below was
actually run, not just described.

## Commands run and what they actually produced

```
$ tofu fmt -recursive -check -diff .
(no output -- clean)

$ tofu init -input=false
...
OpenTofu has been successfully initialized!

$ tofu validate
Success! The configuration is valid.

$ tofu plan -input=false
```

`tofu plan`'s real, actual output (not simulated) includes, among other
resources:

```
Changes to Outputs:
  + acme_namespace          = "model-serving-acme-822b33ad"
  + acme_node_pool_name     = "pilot-aws-g7e-tenant-acme-822b33ad-gpu-node-pool"
  + globex_namespace        = "model-serving-globex-5bc1a08d"
  + globex_node_pool_name   = "pilot-aws-g7e-tenant-globex-5bc1a08d-gpu-node-pool"
  + pool_names_are_distinct = true
```

The `-822b33ad`/`-5bc1a08d` suffixes are the first 8 hex characters of
`sha256("acme")`/`sha256("globex")` -- `main.tf`'s `local.tenant_slug`
formula, replicated exactly in
`services/tenant-cell/src/tenant_cell/naming.py`. Running
`tenant_cell.naming.node_pool_name("pilot-aws-g7e", "acme")` in the
control-plane's own Python package produces the byte-for-byte identical
string `"pilot-aws-g7e-tenant-acme-822b33ad-gpu-node-pool"` -- confirmed
directly against this same `tofu plan` run, not just asserted in prose.

...and 18 real resources fully planned across both tenants before it
errors, including (per tenant) a distinctly-named `aws_iam_role.node`,
`aws_iam_instance_profile.node`, four `aws_iam_role_policy_attachment`s,
a `kubernetes_namespace_v1` (for `acme`'s primary model, `globex`'s
primary *and* reviewer model, per `globex`'s reviewer-model config), and
a `helm_release.vllm` per model deployment. Every one of these resource
addresses/names is derived from `tenant_id` and is structurally distinct
between the two tenants -- this is what the brief's "distinct node pool
names/labels derived from tenant_id, inspectable in the plan" acceptance
criterion asks for, and it is directly inspectable in this real plan
output, not asserted only in prose.

`tofu plan` then errors -- **exactly twice, one per tenant, both on the
same resource type**:

```
Error: Invalid configuration for API client

  with module.tenant_acme.module.gpu_node_pool.kubernetes_manifest.ec2_node_class,
  ...
Error: Invalid configuration for API client

  with module.tenant_globex.module.gpu_node_pool.kubernetes_manifest.ec2_node_class,
  ...
```

## The honest boundary: why exactly these two resources, and only these

`kubernetes_manifest` (the Karpenter `EC2NodeClass`/`NodePool` CRDs
gpu-node-pool/aws creates) is a documented special case in the
`hashicorp/kubernetes` provider: it fetches the target cluster's live
OpenAPI/CRD schema to validate the manifest shape, at plan time, every
time -- there is no way to plan this resource type without a reachable
Kubernetes API server, full stop, independent of whether the resource
would actually change. Every other resource in this composition (plain
typed resources: `aws_iam_role`, `aws_iam_instance_profile`,
`aws_iam_role_policy_attachment`, `kubernetes_namespace_v1`, and
`helm_release` for a wholly new release) plans from schema + configured
values alone and needs no live API call for a resource that doesn't yet
exist -- which is exactly why the plan above got as far as computing and
displaying both tenants' fully distinct resource names/outputs before
failing only on the two resources that have no other option.

This is the same hard constraint F1's own `gpu-node-pool` module was
built under (no live cluster to validate `kubernetes_manifest` against
in this environment) -- this composition doesn't work around it or hide
it; it demonstrates precisely where the boundary is and confirms
everything on the real-cloud-independent side of that boundary.

**What a human with a real cluster gets for free once one exists**: point
`providers.tf`'s `kubernetes`/`helm` provider blocks at a real cluster's
endpoint/CA/token (the same way `environments/pilot-aws-g7e/providers.tf`
does), delete the fake-credential AWS provider overrides, supply real
`cluster_name`/`network_id`/`gpu_subnet_ids`/`egress_allowlist_group_id`
values from that environment's own network module outputs, and this same
configuration plans (and applies) both tenants' full `kubernetes_manifest`
resources too -- nothing in `../../modules/tenant-cell` changes.

## Targeted plan, if you want to isolate just the cloud-only resources

```
tofu plan -input=false \
  -target=module.tenant_acme.module.gpu_node_pool.aws_iam_role.node \
  -target=module.tenant_acme.module.gpu_node_pool.aws_iam_instance_profile.node \
  -target=module.tenant_globex.module.gpu_node_pool.aws_iam_role.node \
  -target=module.tenant_globex.module.gpu_node_pool.aws_iam_instance_profile.node
```

completes with **zero errors** (no resource in this targeted subgraph
ever touches the kubernetes/helm providers), for a plan that fully
succeeds end to end against this composition's fake AWS credentials
alone.

## A latent, pre-existing gap this composition's `tofu plan` surfaced

Running a real `tofu plan` against realistic tag values (not just
reading the HCL) surfaced a real bug: `model-serving`'s `tags` variable
is applied directly as a Kubernetes `Namespace`'s `metadata.labels`, and
Kubernetes label keys/values reject `:` in the name part. F1's own
`environments/pilot-aws-g7e/variables.tf` default `var.tags` uses
colon-separated keys (`"sdlc-auto:managed-by"`) and passes them straight
through to `module.model_serving`'s `tags` -- that would fail the same
way the moment its own `tofu plan` ever reached a real, reachable
cluster (it hasn't, under the same hard constraint). `../../modules/tenant-cell/main.tf`'s
`local.model_serving_labels` works around this at its own call sites
(dot-separated keys); it does not modify `model-serving` itself, which
is outside this deliverable's scope. **Flagged for human review**: worth
deciding whether to fix this at the source (`model-serving`'s own `tags`
variable, or renaming/splitting it into a dedicated `labels` variable)
so every current and future caller doesn't have to work around it
individually the way this module does.
