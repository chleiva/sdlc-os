# spot-lifecycle

Implements spec §14.8's interruption-handling chain for AWS. Real,
working HCL for the AWS signal path; GCP/Azure are explicit gaps (see
below); baremetal is a deliberate no-op.

## What is real here

- EventBridge rules for both AWS interruption signals (the ~2-minute Spot
  interruption warning, and the earlier non-guaranteed rebalance
  recommendation) feeding a dedicated SQS queue.
- IAM for `aws-node-termination-handler` (NTH) running in
  queue-processor mode against that queue.
- The `helm_release` deploying NTH itself.

## What this module does NOT do (by design, not oversight)

NTH natively performs **cordon** (step 1) and **drain** (step 3,
respecting each pod's grace period). Steps 2 (tell model-serving to stop
accepting new requests), 4 (flush observability off-node), and 5 (force
an immediate checkpoint write) are **pod-level** behaviors — they belong
on the model-serving and orchestrator pod specs (`preStop` lifecycle
hooks + a `terminationGracePeriodSeconds` sized against this module's
`warning_window_seconds` output), not on a node-level watcher reaching
into another module's container. This module supplies the webhook
signal and the timing budget those hooks key off; it does not itself
contain application logic for "how to checkpoint a run," which is
orchestrator/D2 territory per this deliverable's brief.

## Explicit gaps

- **GCP/Azure signal sources are not implemented.** `cloud_provider` only
  turns real logic on for `"aws"`. GCP preemption notice (via the
  `preempted` metadata attribute) and Azure Spot eviction notice (via
  Scheduled Events) would each need their own resource block here behind
  the same `enabled` gate pattern used for AWS. This is a Phase 1.5 gap,
  not a hidden one.
- **Chart values are not apply-verified.** The `helm_release` block's
  `set` blocks match the aws-node-termination-handler chart's documented
  values at time of writing; pin an exact chart `version` and re-check
  against that version's published `values.yaml` before the first real
  `tofu apply` — Helm chart value shapes change across versions and this
  was written without a live cluster to validate against.
- **The interruption *drill* script** (simulating an interruption notice
  end-to-end to satisfy this deliverable's acceptance criterion 3) lives
  in `infra/scripts/simulate-spot-interruption.sh`, not in this module —
  see that script's header comment for what it does and does not
  exercise.
