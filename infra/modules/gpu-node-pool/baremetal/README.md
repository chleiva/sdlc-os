# gpu-node-pool/baremetal — Phase 1.5 stub

Not a working bare-metal GPU node pool. Satisfies the module contract
(identical `variables.tf` / `outputs.tf` to `gpu-node-pool/aws`) only.
This variant's real implementation is the most different internally of
the four (no spot/preemption concept, capacity is whatever hardware is
racked) even though its variable/output contract must stay identical —
see `main.tf`.
