# gpu-node-pool/gcp — Phase 1.5 stub

Not a working GCP GPU node pool. Satisfies the module contract (identical
`variables.tf` / `outputs.tf` to `gpu-node-pool/aws`) only. See
`main.tf` for what the real implementation needs. Build and validate
against a real GCP project with GPU quota as its own scoped piece of
work — this stub exists so the directory tree and module-contract review
can proceed ahead of that build.
