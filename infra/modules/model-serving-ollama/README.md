# model-serving-ollama

Cloud-agnostic Helm chart deployment of Ollama (official `ollama/ollama`
image) serving the pinned model `ornith-1.5-35b-a3b` (spec §13.5), as a
spot-only, scale-to-zero sibling to `modules/model-serving`'s vLLM
deployment — not a replacement for it. See `environments/pilot-aws-g7e`
for both wired in side by side.

## Why a sibling module, not a `serving_backend` switch

See `main.tf`'s header comment for the full reasoning. Short version:
vLLM's chart is built around vLLM's own CLI flags, health path, and a
warning-window-sized `preStop` drain hook; Ollama shares none of those
(no tensor-parallel/gpu-utilization flags, no `/health` path, a
`postStart`-driven pull step vLLM has no equivalent of, a different
default port/API shape). This tier is also the only one that is
spot-only/KEDA-scaled-to-zero. A sibling module — same variable-naming
conventions as `modules/model-serving`, trivially composed side by side
— keeps that isolation without inventing a new pattern; this repo
already does the sibling-module-per-variant thing for
`gpu-node-pool/{aws,gcp,azure,baremetal}`.

## What is real here

- A real `helm_release` deploying the local chart in `./chart`: a
  Kubernetes `Deployment` running the official `ollama/ollama` image,
  and a `Service` exposing its OpenAI-compatible endpoint (routes under
  `/v1/*`, e.g. `/v1/chat/completions`, `/v1/models`) on port 11434.
- A `postStart` lifecycle hook that runs `ollama pull ornith-1.5-35b-a3b`
  on first pod start, after polling for the Ollama server's own port to
  come up (see `chart/templates/deployment.yaml`'s comment for why it
  polls rather than assumes ordering).
- An opt-in (`keda_enabled = true`) KEDA `ScaledObject` (real
  `kubernetes_manifest` resource) with `minReplicaCount: 0`,
  `maxReplicaCount: 1`, driven by a `metrics-api` trigger.
- Resource requests/limits and a `node.kubernetes.io/instance-type`
  nodeSelector parameterized (not hardcoded) around a single-GPU,
  48GB-class instance profile — default `g6e.xlarge` (NVIDIA L40S).

## Restoring the model cache from S3 (`model_cache_s3_uri`)

Set `model_cache_s3_uri` (e.g. `s3://your-bucket/ornith-1.5-35b-a3b/`)
and `service_account_role_arn` (an IRSA role ARN, see
`environments/pilot-aws-g7e/eks.tf`'s `aws_iam_role.ollama_model_cache`
for how the pilot environment provisions one) to replace the "pull from
Ollama's public registry on every cold start" behavior below with:

1. A real IRSA-annotated `ServiceAccount` is rendered
   (`chart/templates/serviceaccount.yaml`) and used as the pod's
   `serviceAccountName`.
2. A new **init container** (a minimal AWS CLI image, pinned tag — see
   `chart/values.yaml`'s `modelCache.awsCliImage`/`awsCliImageTag`) runs
   `aws s3 sync <model_cache_s3_uri> /ollama-models` into a shared
   `emptyDir` volume, before the main container starts.
3. The main container's `OLLAMA_MODELS` env var is pointed at that same
   `/ollama-models` path (Ollama's documented override for its default
   model-store location — see "Ollama's model-store layout" below), so
   if the sync actually restored the model, Ollama finds it already on
   disk.
4. The existing `postStart: ollama pull` hook (below) is kept
   **unconditionally** as a real fallback — `ollama pull` is a no-op
   once the model is already present, so this is always safe to run,
   not an either/or with the restore step. This means a first-ever run
   (empty bucket/prefix) or a misconfigured `model_cache_s3_uri` still
   works, just slowly (the original pull-only behavior), rather than
   failing outright.

Both variables default to `null`/unset, in which case **none** of the
above renders — no `ServiceAccount`, no init container, no
`OLLAMA_MODELS` override, no `emptyDir` volume — and this tier's
behavior is byte-for-byte the pull-only behavior this module has always
had. This is deliberately additive/opt-in, not a replacement for the
always-pull path, which stays fully intact.

### Ollama's model-store layout

Ollama's own documented storage layout: on Linux/macOS it defaults to
`~/.ollama/models` (`/root/.ollama/models` in this image, since the
official `ollama/ollama` image runs as root with `HOME=/root`);
`OLLAMA_MODELS` overrides this directly to whatever directory you point
it at, and that directory holds Ollama's `blobs/`/`manifests/` layout
directly (not a nested `.ollama/` dir underneath it). This module points
`OLLAMA_MODELS` at `/ollama-models`, the `emptyDir` mount, when
`model_cache_s3_uri` is set — the restore init container's `aws s3 sync`
target and Ollama's own read path agree on this exact directory.

### What a human must still do

This module does **not** provision the S3 bucket — same
"consumes an already-existing input" pattern this repo already uses
elsewhere (e.g. `services/tenant-cell`'s KMS-key disclaimer: the module
consumes a key/bucket ARN, it does not create the underlying resource).
Before setting `model_cache_s3_uri`, a human still needs to:

1. **Create the S3 bucket** (any region; nothing here provisions it).
   Standard `s3:PutBucketEncryption`/versioning/lifecycle hygiene is the
   bucket owner's call, not this module's.
2. **Seed the cache once**, from anywhere with real internet access
   (not required to be inside this cluster/VPC):
   ```sh
   ollama pull ornith-1.5-35b-a3b
   aws s3 sync ~/.ollama/models s3://<bucket>/<prefix>/
   ```
   (adjust the local source path if `OLLAMA_MODELS` was already
   overridden wherever this pull is run). This is a one-time step per
   model version — re-run it only when the pinned model changes.
3. **Grant/verify the IRSA role** (`service_account_role_arn`) can reach
   that bucket — the pilot environment's `aws_iam_role.ollama_model_cache`
   (see `environments/pilot-aws-g7e/eks.tf`) already scopes a real
   least-privilege `s3:GetObject`/`s3:ListBucket` policy to exactly the
   configured bucket/prefix ARN, nothing else; a hand-rolled deployment
   of this module elsewhere needs the equivalent.

### Why S3, not an EBS-backed PersistentVolumeClaim

The two follow-ups originally flagged here were "bake the model into a
custom image" and "a PVC mounted at the model-store path." A PVC is the
more obvious fix, but this tier's whole design point is spot-only,
scale-to-zero (see the `ScaledObject` below): Karpenter reclaiming the
underlying EC2 instance when KEDA scales to 0, then provisioning a fresh
one in whichever AZ has capacity when it scales back to 1, is the normal
steady-state path here, not an edge case. An EBS volume is
**zone-locked** — a PVC bound to an EBS volume in `us-east-1a` cannot
attach to a fresh node Karpenter happens to place in `us-east-1b`, which
is exactly this tier's normal failure mode, not a rare one. S3 has no
such constraint: any node in any AZ in the bucket's region can restore
from it. Cost-wise this is also cheaper for this workload: ~23GB at S3
Standard pricing is roughly **$0.50/month**, versus an equivalent
~23GB `gp3` EBS volume at roughly **$2–3/month** — and the EBS number
doesn't even account for the zonal-lock problem above, which S3 simply
doesn't have. The "bake into a custom image" option remains a valid
alternative not taken here (it trades a larger, layer-cacheable image
pull for zero runtime S3 dependency) — not implemented in this pass
either, and still a legitimate follow-up if image-build tooling for a
custom `ollama/ollama` derivative is ever set up.

## Known follow-up: model still pulled fresh on every pod start when `model_cache_s3_uri` is unset

`model_cache_s3_uri` is opt-in (default `null`). Without it set, `ollama
pull` still runs from scratch every time a new pod starts — this chart
declares no persistent volume for Ollama's model store and no custom
image with the model pre-baked in. For `ornith-1.5-35b-a3b` (a ~23GB
quantized model) that means real, non-trivial time and bandwidth on
every fresh pod, which is a direct cost to the KEDA scale-to-zero story
this module also implements: scaling from 0 → 1 replica does not mean
"ready to serve" until the pull finishes, potentially minutes after the
pod starts, depending on available bandwidth. Setting
`model_cache_s3_uri` (see "Restoring the model cache from S3" above) is
the real fix for this now; it remains unsolved-by-default because the
feature is opt-in, not because it isn't real.

## Model context-length vs. VRAM caveat

`ornith-1.5-35b-a3b` is served on a single 48GB-class card (default
`g6e.xlarge`, one NVIDIA L40S) with no tensor/pipeline parallelism (this
tier is explicitly single-GPU, per the platform's single-cell,
no-GPU-sharing design). After the quantized weights occupy VRAM, the
remaining headroom bounds Ollama's usable context length and concurrent
request count (`OLLAMA_NUM_PARALLEL`, `num_ctx`) — those are Ollama
server/request-time settings, not exposed as Terraform variables here,
and **have not been tuned or load-tested against real hardware** in this
build environment (no live GPU available). Before relying on this tier
for anything beyond a serving-backend comparison, a human should
actually load-test the real context-length/concurrency envelope this
specific model/instance-type/quantization combination supports and set
Ollama's own runtime parameters accordingly (via `OLLAMA_*` env vars on
the Deployment, not currently plumbed as chart values — another
follow-up, not solved here).

## KEDA trigger: run-registry integration gap

The `ScaledObject`'s trigger is KEDA's generic `metrics-api` scaler,
pointed at `var.keda_metrics_api_url`. The exact contract this
`ScaledObject` expects from that URL:

- **Request**: a plain `GET` to `var.keda_metrics_api_url` (no auth
  configured here — see below).
- **Response**: `200 OK`, JSON body with an integer field readable at
  the top level, e.g. `{"count": 3}` — matches `valueLocation: "count"`
  in the trigger's `metadata`.
- **Semantics**: the count of runs currently in a state awaiting a model
  response from this specific tier/tenant (so KEDA scales 0 → 1 the
  moment that count goes above 0, and back to 0 after
  `keda_cooldown_period_seconds` once it's been back at 0 the whole
  cooldown window).

**This endpoint does not exist in `services/run-registry` today** —
verified against that service's actual surface before writing this
module: `run_registry.service.RegistryService` (and the MCP tool server
wrapping it, `run_registry.mcp_server`) exposes `list_runs`,
`get_run`, `transition_stage`, etc. as MCP tool calls / a Python library
API, not as an HTTP endpoint at all, let alone a plain `GET` returning a
JSON count. There is also no stage in the Registry's fixed nine-stage
vocabulus (`run_registry.stages`) named anything like "awaiting model
response" — the closest concept would need to be derived (e.g. "runs in
`implementation`/`plan_authoring`/etc. with an open, not-yet-resolved
model call"), which isn't tracked as Registry state today either.

Per this task's own directive: **this is stated here as the concrete
integration gap, not invented as a fake endpoint.** Building it is
`services/run-registry` work (out of this module's directory scope —
`infra/` does not touch `services/`), needs a design decision, and
should be reconciled with whatever `orchestrator`/D2 actually tracks
about in-flight model calls before being built. Until it exists,
`keda_enabled` should stay `false` (its default) — the `ScaledObject`
resource this module declares is real, syntactically valid HCL against
KEDA's actual CRD schema, but has no real, working `url` to point at
yet.

No authentication is configured on the `metrics-api` trigger
(`authenticationRef` is not set) — if/when the real endpoint requires
one, KEDA's `TriggerAuthentication` CRD is the mechanism; not added here
since there is no real endpoint yet to authenticate against.

## Prerequisites (not installed by this module)

- **KEDA's controller** must already be installed on the cluster, via
  its own Helm chart, cluster-wide — same assumption
  `modules/gpu-node-pool/aws` makes about Karpenter's controller. This
  module only declares the per-tenant `ScaledObject` CRD instance.
- **`gpu-node-pool/aws`'s spot-only NodePool instance** for this tier
  (see that module's README "Spot-only, scale-to-zero pools") must exist
  and its `node_pool_name` output must be passed to `var.node_pool_name`
  here — this module does not provision compute itself.
- **The S3 bucket in `model_cache_s3_uri`**, if that variable is set —
  see "What a human must still do" above. Also requires an IRSA
  role/`ServiceAccount` webhook (the EKS Pod Identity Webhook that
  actually injects `AWS_ROLE_ARN`/`AWS_WEB_IDENTITY_TOKEN_FILE` into a
  pod annotated with `eks.amazonaws.com/role-arn`) to already be part of
  the cluster's control plane — true for any real EKS cluster with IRSA
  enabled, not something this module or `environments/pilot-aws-g7e`
  installs.

## Real vs. mocked / not-yet-validated

- Real: the Helm chart, its Deployment/Service, the `postStart` pull
  hook's shape, the `ScaledObject` HCL, the model-cache-restore init
  container + IRSA `ServiceAccount` (when `model_cache_s3_uri`/
  `service_account_role_arn` are set).
- Not validated against a live cluster or a real GPU in this build
  environment (no AWS credentials, no live cluster, no live GPU
  available) — see `infra/README.md`'s top-level caveat. `tofu plan`
  against the `kubernetes_manifest` `ScaledObject` resource specifically
  requires a reachable Kubernetes API serving KEDA's actual CRD schema;
  expect a connection error there, not a structural one, when run
  without a live, KEDA-equipped cluster. The `aws s3 sync` init
  container and the IRSA role's actual S3 access are likewise not
  validated against a real bucket/cluster here — no real AWS credentials
  exist in this build environment (see repo-wide convention).
