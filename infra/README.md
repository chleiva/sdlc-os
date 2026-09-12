# infra/ — IaC Foundation & Phase 0 Environment (deliverable F1)

Module tree + Phase 0 pilot environment per master spec §14.3/§14.7 and
`docs/deliverables/wave0-F1-iac-foundation.md`. IaC engine is
**OpenTofu** (`tofu`), never the Terraform CLI (spec §14.2).

## Layout

```
infra/
  modules/
    network/{aws,gcp,azure,baremetal}/
    gpu-node-pool/{aws,gcp,azure,baremetal}/
    model-serving/            # cloud-agnostic
    orchestrator/              # cloud-agnostic
    sandbox-runtime/{firecracker,kata,gvisor}/
    spot-lifecycle/            # cloud-agnostic layout; AWS signal path implemented
    secrets/{aws-secrets-manager,gcp-secret-manager,azure-key-vault,vault}/
    observability/             # cloud-agnostic
  environments/
    pilot-aws-g7e/             # fully built out, tofu validate-clean
    prod-aws/                  # same real modules, scaled up; not apply-tested
    prod-gcp/                  # portability-proof STRUCTURE only (stub modules underneath)
    prod-azure/ prod-baremetal/ # README only, no .tf — see their READMEs
  scripts/
    bootstrap.sh                # the 5-step sequence, spec §14.7
    smoke-test.sh                # step 5's runner (infra checks; D7's suite content plugs in)
    simulate-spot-interruption.sh  # Phase 0.5's drill, spec §21
    lib/spot-discovery.sh        # live spot price/interruption discovery, spec §14.8
    lib/seed-secrets.sh          # step 3
```

## What's real vs. what's a stated stub

**Built out for real, `tofu validate`-clean** (every module below was
`tofu init`+`tofu validate`d against real provider schemas, network
access included, as part of this deliverable):

- `network/aws`, `gpu-node-pool/aws`, `secrets/aws-secrets-manager`
- `model-serving`, `orchestrator`, `observability`, `spot-lifecycle`
  (cloud-agnostic modules; spot-lifecycle's AWS signal path)
- `sandbox-runtime/firecracker`
- `environments/pilot-aws-g7e` (full composition: EKS cluster, system
  node group, Karpenter, and every module above wired together)
- `environments/prod-aws` (same modules, production-scaled; not
  apply-tested, only validate-tested)
- `environments/prod-gcp` (portability-proof **structure**: real
  `tofu validate`-clean composition demonstrating the module swap, but
  the `gcp` modules underneath are stubs — see its README)
- `scripts/bootstrap.sh` and its `lib/` scripts — `bash -n`-clean;
  `spot-discovery.sh`'s logic was dry-run tested against AWS's real,
  live Spot Instance Advisor data feed (not mocked) during this build,
  and correctly reproduced spec §14.8's own eu-south-2 example.

**Explicit stubs** (same variable/output contract as the real module,
zero real provisioning — each has its own README.md with what a real
implementation needs):

- `network/{gcp,azure,baremetal}`, `gpu-node-pool/{gcp,azure,baremetal}`,
  `secrets/{gcp-secret-manager,azure-key-vault,vault}`
- `sandbox-runtime/{kata,gvisor}`
- `environments/{prod-azure,prod-baremetal}` (no `.tf` at all yet)

**Known gaps inside the "real" modules**, each flagged in its own
module's README/comments rather than silently present:

1. `orchestrator`'s Helm chart deploys a placeholder echo-server image,
   not a real agent runtime (D2 doesn't exist yet).
2. `model-serving`'s `preStop` hook calls a `/stop_accepting` path vLLM
   doesn't actually expose yet — a documented best-effort placeholder.
3. `sandbox-runtime/firecracker` registers a RuntimeClass and renders a
   node-bootstrap script, but that script isn't yet wired into
   `gpu-node-pool/aws`'s `EC2NodeClass.userData` — a one-line integration
   gap, not a redesign.
4. `spot-lifecycle` implements the AWS signal path for real; GCP/Azure
   signal sources are not implemented (`cloud_provider` only turns on
   real logic for `"aws"`).
5. `pilot-aws-g7e/eks.tf`'s Karpenter controller IAM policy is a
   deliberately flagged `PowerUserAccess` placeholder — replace with
   Karpenter's documented least-privilege policy before any real apply.
6. `observability`'s Grafana admin-password secret is referenced by name
   but nothing here syncs the AWS Secrets Manager entry into the
   Kubernetes Secret Grafana's chart expects — needs External Secrets
   Operator or the Secrets Store CSI Driver, not built in this pass.
7. `scripts/smoke-test.sh` runs real infra-level checks (Helm release
   status, a live round-trip against the model-serving endpoint) but the
   actual §20.1 evaluation-suite subset is D7's deliverable — the script
   looks for it at a `services/verification/` path and logs an honest
   gap message if it isn't there yet, rather than faking a pass.
8. `scripts/simulate-spot-interruption.sh` proves the infrastructure half
   of the Phase 0.5 drill (signal → cordon, within the warning window)
   for real; the "no developer-visible data loss" half depends on D2's
   checkpoint/resume logic, which doesn't exist yet — the script says so
   rather than declaring the drill fully passed.

**Nothing in this tree was run against a real cloud account** (no AWS
credentials, no live cluster, in this build environment) — every module
and environment was validated by construction (`tofu validate`,
`tofu fmt`, `bash -n`) against real provider/CLI schemas, not
apply-tested end-to-end. Treat `pilot-aws-g7e` as ready for a first real
`bootstrap.sh` run and the gaps above as the first things to expect from
that run, not as hidden defects.

## No secrets in this tree

`grep`-verified: no `aws_secretsmanager_secret_version` (or equivalent)
resource, no hardcoded credential, no committed `terraform.tfvars`
(only `.tfvars.example` files) anywhere under `infra/`. Secret *values*
only ever flow through `scripts/lib/seed-secrets.sh`, directly from a
human prompt or CI's own secret store to the cloud secret manager's API
— never through a `.tf` file or `tofu state`.
