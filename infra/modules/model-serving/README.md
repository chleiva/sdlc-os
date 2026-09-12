# model-serving

Cloud-agnostic Helm chart deployment of vLLM + the pinned model artifact
(spec §13.5, §14.3). This module owns deploying the chart; it does not
own tenant-cell provisioning logic (D6, per this deliverable's brief) or
the model artifact's checksum-pinning process itself (spec §13.5) beyond
surfacing `model_weight_checksum` as a variable.

## Explicit gap

The `preStop` hook in `chart/templates/deployment.yaml` calls a
`/stop_accepting` path that vLLM does not currently expose as a real
endpoint — it is a best-effort placeholder for the "stop accepting new
inference requests" step of spec §14.8's interruption sequence, written
defensively (`|| true`) so it never blocks the grace period on a 404.
Replacing it with a real drain signal is D6/D2 work once the
model-serving tenant-cell provisioning logic exists — flagged here rather
than presented as a working drain endpoint.
