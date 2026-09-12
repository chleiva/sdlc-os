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

## Known follow-up: model pulled fresh on every pod start

`ollama pull` runs from scratch every time a new pod starts — this chart
declares no persistent volume for Ollama's model store (`/root/.ollama`)
and no custom image with the model pre-baked in. For
`ornith-1.5-35b-a3b` (a ~23GB quantized model) that means real,
non-trivial time and bandwidth on every fresh pod, which is a direct cost
to the KEDA scale-to-zero story this module also implements: scaling
from 0 → 1 replica does not mean "ready to serve" until the pull
finishes, potentially minutes after the pod starts, depending on
available bandwidth. **This is flagged as a follow-up, not solved in
this pass** — two credible real fixes, neither implemented here:

1. Bake the model into a custom image derived from `ollama/ollama`
   (`ollama serve & ollama pull ornith-1.5-35b-a3b`, then commit),
   trading a larger image pull (which container runtimes can layer-cache
   across pods on the same node, unlike a fresh `ollama pull` into an
   ephemeral filesystem) for zero per-pod model-download cost.
2. A `PersistentVolumeClaim` mounted at `/root/.ollama`, pulled once and
   reused across pod restarts on nodes that can reattach it — awkward
   for the *scale-to-zero* case specifically, since Karpenter reclaiming
   the underlying EC2 instance (the whole point of this tier) may not
   preserve the same EBS volume attachment across a fresh node, so this
   would need to be paired with a plan for what happens to the PVC.

Either is a real, scoped follow-up; this pass intentionally ships the
simple always-pull behavior with the cost documented rather than
building either fix without a live cluster to validate it against.

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

## Real vs. mocked / not-yet-validated

- Real: the Helm chart, its Deployment/Service, the `postStart` pull
  hook's shape, the `ScaledObject` HCL.
- Not validated against a live cluster or a real GPU in this build
  environment (no AWS credentials, no live cluster, no live GPU
  available) — see `infra/README.md`'s top-level caveat. `tofu plan`
  against the `kubernetes_manifest` `ScaledObject` resource specifically
  requires a reachable Kubernetes API serving KEDA's actual CRD schema;
  expect a connection error there, not a structural one, when run
  without a live, KEDA-equipped cluster.
